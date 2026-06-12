"""Lambda entry points.

Two functions share this file (the SAM template points each at one handler):

  api_handler     — synchronous, behind API Gateway HTTP API. Fast routes only
                    (create job, presign upload, poll status). Never calls the
                    LLM, so it always returns well under the 29s gateway limit.
  worker_handler  — invoked asynchronously (Event) by api_handler. Runs the
                    1-2 minute insertion (8 LLM rounds + feedback) and writes
                    progress/result into the job record as it goes.

The split is what keeps the app inside API Gateway's timeout while still doing
minute-scale work, and it's why idle cost is ~$0: nothing runs between requests.
"""

import datetime as dt
import json
import os
import uuid

import boto3

from shared.config import (
    INSERTION_GROUPS,
    LLM_MODEL,
    S3_BUCKET,
    USER_ASSET_PREFIX,
    WORKER_FUNCTION,
)
from shared.pools import POOLS, get_pool
from . import auth, ddb, feedback
from .insert import insert

_s3 = boto3.client("s3")
_lambda = boto3.client("lambda")

_CORS = {
    "Access-Control-Allow-Origin": os.getenv("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}
_ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", **_CORS},
            "body": json.dumps(body)}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ── API (synchronous) ───────────────────────────────────────────────────────
def api_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    if method == "OPTIONS":
        return _resp(200, {})

    # The signed-in user (or None for anonymous). Public routes ignore it;
    # /me* routes require it.
    user = auth.identity_from_event(event)

    try:
        # ── account + personal salon (auth required) ──
        if path == "/me" and method == "GET":
            return _require(user) or _get_me(user)
        if path == "/me/salon" and method == "GET":
            return _require(user) or _list_salon(user)
        if path.startswith("/me/salon/"):
            gate = _require(user)
            if gate:
                return gate
            parts = path.split("/")  # ['', 'me', 'salon', <piece>, ...]
            piece_id = parts[3]
            if "/comparisons/" in path:
                return _get_salon_comparison(user, piece_id, parts[5])
            if method == "DELETE":
                return _delete_salon_piece(user, piece_id)
            return _get_salon_piece(user, piece_id)

        if method == "GET" and path == "/pools":
            return _get_pools()
        if method == "GET" and path.startswith("/pools/") and path.endswith("/pieces"):
            return _browse_pieces(path.split("/")[2],
                                  event.get("queryStringParameters") or {})
        if method == "GET" and path.startswith("/pools/") and "/pieces/" in path:
            parts = path.split("/")  # ['', 'pools', <pool>, 'pieces', <id>]
            return _get_piece(parts[2], parts[4])
        if method == "GET" and path.startswith("/pools/") and "/comparisons/" in path:
            parts = path.split("/")  # ['', 'pools', <pool>, 'comparisons', <id>]
            return _get_comparison(parts[2], parts[4])
        if method == "POST" and path == "/uploads":
            return _create_upload(json.loads(event.get("body") or "{}"))
        if method == "POST" and path == "/submissions":
            return _create_submission(json.loads(event.get("body") or "{}"), user)
        if (method == "GET" and path.startswith("/submissions/")
                and "/comparisons/" in path):
            parts = path.split("/")  # ['', 'submissions', <job>, 'comparisons', <id>]
            return _get_submission_comparison(parts[2], parts[4])
        if method == "GET" and path.startswith("/submissions/"):
            return _get_submission(path.rsplit("/", 1)[-1])
        return _resp(404, {"error": f"no route for {method} {path}"})
    except (KeyError, ValueError) as e:
        return _resp(400, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 — surface a clean error to the client
        return _resp(500, {"error": f"internal error: {e}"})


def _get_pools():
    """Prefer published pool-meta items (carry live piece counts); fall back to
    the static registry so the UI still works before the first publish."""
    metas = {m["pool_id"]: m for m in ddb.list_pools()}
    pools = []
    for pid, p in POOLS.items():
        m = metas.get(pid, {})
        pools.append({
            "id": pid,
            "label": p.label,
            "jury_subject": p.jury_subject,
            "n_pieces": int(m.get("n_pieces", 0)),
            "ready": int(m.get("n_pieces", 0)) > 0,
        })
    return _resp(200, {"pools": pools})


def _browse_pieces(pool_id: str, qs: dict):
    """Scoreboard Explorer: pieces in a percentile range, sorted best-first,
    paginated. At PoC scale the whole pool is one Query; sort/filter/slice happen
    here (see PLAN's deferred rank-sorted GSI for the scale path)."""
    get_pool(pool_id)  # validate -> 400 on unknown pool

    def _int(name, default):
        try:
            return int(qs.get(name, default))
        except (TypeError, ValueError):
            return default

    min_p = max(0, _int("min", 0))
    max_p = min(100, _int("max", 100))
    offset = max(0, _int("offset", 0))
    limit = min(max(1, _int("limit", 10)), 50)

    pieces = ddb.load_eligible_pool(pool_id)
    pieces.sort(key=lambda p: -float(p.get("elo", 0)))
    in_range = [p for p in pieces
                if p.get("percentile") is not None
                and min_p <= int(p["percentile"]) <= max_p]
    page = in_range[offset:offset + limit]
    slim = [{
        "piece_id": p.get("piece_id"),
        "title": p.get("title") or p.get("piece_id"),
        "author": p.get("author"),
        "image_url": p.get("image_url"),
        "permalink": p.get("permalink"),
        "elo": round(float(p.get("elo", 0)), 1),
        "rank": p.get("rank"),
        "percentile": p.get("percentile"),
        "n_comparisons": p.get("n_comparisons", 0),
        "comparisons_summary": p.get("comparisons_summary", []),
    } for p in page]
    return _resp(200, {"pool": pool_id, "total": len(in_range),
                       "offset": offset, "limit": limit, "pieces": slim})


def _get_piece(pool_id: str, piece_id: str):
    """One piece's full scoreboard card (image, standing, and its complete
    comparison list) for the dedicated piece page."""
    pool_def = get_pool(pool_id)  # validate -> 400 on unknown pool
    p = ddb.get_piece(pool_id, piece_id)
    if not p or p.get("is_candidate"):
        return _resp(404, {"error": "unknown piece"})
    meta = ddb.get_pool_meta(pool_id) or {}
    return _resp(200, {
        "pool": pool_id,
        "pool_label": pool_def.label,
        "piece_id": p.get("piece_id"),
        "title": p.get("title") or p.get("piece_id"),
        "image_url": p.get("image_url"),
        "permalink": p.get("permalink"),
        "elo": round(float(p.get("elo", 0)), 1),
        "rank": p.get("rank"),
        "percentile": p.get("percentile"),
        "of": int(meta.get("n_pieces", 0)) or None,
        "n_comparisons": p.get("n_comparisons", 0),
        "comparisons_summary": p.get("comparisons_summary", []),
    })


def _get_comparison(pool_id: str, cmp_id: str):
    """Full detail for one jury comparison: every member ranked, with the
    overall rationale and each piece's per-round rationale + overall standing."""
    get_pool(pool_id)
    cmp = ddb.get_comparison(pool_id, cmp_id)
    if not cmp:
        return _resp(404, {"error": "unknown comparison"})

    piece_ids = cmp.get("piece_ids", [])
    members = {m["piece_id"]: m for m in ddb.batch_get_pieces(pool_id, piece_ids)}
    rationale_by_id = {x.get("piece_id"): x.get("rationale", "")
                       for x in cmp.get("per_piece", [])}
    ranking = cmp.get("ranking", [])
    size = len(piece_ids)

    def _member(pid, rank_in_set):
        m = members.get(pid, {})
        return {
            "piece_id": pid,
            "rank_in_set": rank_in_set,
            "size": size,
            "image_url": m.get("image_url"),
            "title": m.get("title") or pid,
            "author": m.get("author"),
            "permalink": m.get("permalink"),
            "overall_percentile": m.get("percentile"),
            "overall_rank": m.get("rank"),
            "rationale": rationale_by_id.get(pid, ""),
        }

    ordered = [_member(pid, i + 1) for i, pid in enumerate(ranking)]
    # Members the jury flagged not-art that round are absent from `ranking`.
    ordered += [_member(pid, None) for pid in piece_ids if pid not in ranking]

    pcts = [m["overall_percentile"] for m in ordered
            if m["overall_percentile"] is not None]
    avg_pctl = round(sum(pcts) / len(pcts), 1) if pcts else None
    return _resp(200, {
        "comparison_id": cmp.get("comparison_id"),
        "overall_rationale": cmp.get("rationale", ""),
        "model": cmp.get("model", ""),
        "created_at": cmp.get("created_at", ""),
        "avg_pctl": avg_pctl,
        "members": ordered,
    })


def _create_upload(body: dict):
    content_type = body.get("content_type", "image/jpeg")
    if content_type not in _ALLOWED_CT:
        raise ValueError(f"unsupported content_type {content_type}")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
    key = f"uploads/{uuid.uuid4().hex}.{ext}"
    url = _s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=300,
    )
    return _resp(200, {"upload_url": url, "image_key": key, "content_type": content_type})


def _create_submission(body: dict, user: dict | None = None):
    pool_id = body["pool"]
    get_pool(pool_id)  # validate
    image_key = body["image_key"]
    title = (body.get("title") or "Untitled").strip()[:140]
    job_id = uuid.uuid4().hex[:16]

    # Public-ish read URL for the report (the object also stays presign-able).
    image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{image_key}"
    # Signed-in submissions are tagged so the worker promotes them into the
    # user's permanent salon; anonymous submissions stay transient (TTL).
    ddb.create_job(job_id, pool_id, title, image_key, image_url, _now(),
                   user_sub=user["sub"] if user else None)

    _lambda.invoke(
        FunctionName=WORKER_FUNCTION,
        InvocationType="Event",  # fire-and-forget; worker updates the job record
        Payload=json.dumps({"job_id": job_id}).encode(),
    )
    return _resp(202, {"job_id": job_id, "status": "queued"})


def _get_submission(job_id: str):
    job = ddb.get_job(job_id)
    if not job:
        return _resp(404, {"error": "unknown job_id"})
    job.pop("pk", None)
    job.pop("sk", None)
    job.pop("ttl", None)
    return _resp(200, job)


def _cmp_payload(c: dict) -> dict:
    """Shape a stored per-round comparison (job- or salon-scoped) into the
    response the frontend's comparison modal expects."""
    return {
        "comparison_id": c.get("comparison_id"),
        "phase": c.get("phase"),
        "model": c.get("model", ""),
        "created_at": c.get("created_at", ""),
        "avg_pctl": c.get("avg_pctl"),
        "size": c.get("size"),
        "overall_rationale": c.get("rationale", ""),
        "members": c.get("members", []),
    }


def _get_submission_comparison(job_id: str, cmp_id: str):
    """Detail for one round of a submission's jury comparisons (mirrors the pool
    comparison endpoint's shape so the frontend renders it identically)."""
    c = ddb.get_job_comparison(job_id, cmp_id)
    if not c:
        return _resp(404, {"error": "unknown comparison"})
    return _resp(200, _cmp_payload(c))


# ── Account + personal salon ────────────────────────────────────────────────
def _require(user: dict | None):
    """Return a 401 response when no valid user; None to proceed."""
    return None if user else _resp(401, {"error": "sign in required"})


def _get_me(user: dict):
    """Profile for the signed-in user; upserts it (first call after sign-in
    registers the account)."""
    ddb.upsert_user(user["sub"], user["email"], user["name"],
                    user["picture"], _now())
    return _resp(200, {
        "sub": user["sub"], "email": user["email"],
        "name": user["name"], "picture": user["picture"],
    })


def _list_salon(user: dict):
    """The user's salon: every ranked piece, newest first. Slim cards — the
    timeline page plots percentile-over-time from these and links to detail."""
    pieces = ddb.list_user_pieces(user["sub"])
    pieces.sort(key=lambda p: p.get("submitted_at") or "", reverse=True)
    slim = [{
        "piece_id": p.get("piece_id"),
        "image_url": p.get("image_url"),
        "percentile": p.get("percentile"),
        "not_art": bool(p.get("not_art")),
        "headline": p.get("headline"),
        "submitted_at": p.get("submitted_at"),
        "pool_label": p.get("pool_label"),
    } for p in pieces]
    return _resp(200, {"pieces": slim, "count": len(slim)})


def _get_salon_piece(user: dict, piece_id: str):
    """One salon piece's full result (image, placement, feedback, the round
    summary the detail page lazy-loads via /comparisons/<round>)."""
    p = ddb.get_user_piece(user["sub"], piece_id)
    if not p:
        return _resp(404, {"error": "unknown piece"})
    for k in ("pk", "sk", "user_sub", "type"):
        p.pop(k, None)
    return _resp(200, p)


def _get_salon_comparison(user: dict, piece_id: str, cmp_id: str):
    c = ddb.get_user_piece_comparison(user["sub"], piece_id, cmp_id)
    if not c:
        return _resp(404, {"error": "unknown comparison"})
    return _resp(200, _cmp_payload(c))


def _delete_salon_piece(user: dict, piece_id: str):
    ddb.delete_user_piece(user["sub"], piece_id)
    return _resp(200, {"ok": True, "deleted": piece_id})


# ── Worker (asynchronous) ───────────────────────────────────────────────────
def worker_handler(event, context):
    job_id = event["job_id"]
    job = ddb.get_job(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}

    pool_id = job["pool_id"]
    pool_def = get_pool(pool_id)
    model = os.getenv("LLM_MODEL", LLM_MODEL)
    try:
        ddb.update_job(job_id, status="running")
        anchors = ddb.load_eligible_pool(pool_id)

        # Presigned GET so the LLM can fetch the just-uploaded candidate image.
        candidate_url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": job["image_key"]},
            ExpiresIn=3600,
        )

        def progress(rnd, total, elo, pctl):
            ddb.update_job(job_id, progress={
                "round": rnd, "total": total,
                "elo": round(float(elo), 1), "percentile": int(pctl),
            })

        result = insert(
            candidate_id=f"cand_{job_id}",
            image_url=candidate_url,
            title=job["title"],
            pool=anchors,
            jury_subject=pool_def.jury_subject,
            framing=pool_def.framing,
            criteria=pool_def.criteria,
            model=model,
            n_groups=INSERTION_GROUPS,
            progress_cb=progress,
        )

        pool_elos = result.pop("pool_elos")
        cand_rationales = [
            rd["candidate"].get("rationale_this_round")
            for rd in result["rounds"] if rd.get("candidate")
        ]
        # When the jury never scored the piece (flagged not-art every round), the
        # percentile is meaningless — report that instead of a fake placement,
        # and skip the feedback LLM call + comparison tiers it doesn't apply to.
        not_art = bool(result.get("not_art"))
        if not_art:
            headline = {
                "headline": "The jury didn't score this piece.",
                "subline": "It read as a casual sketch or unfinished work rather "
                           "than a finished submission, so it wasn't ranked.",
            }
            synthesized = ""
        else:
            headline = feedback.verdict_headline(result["percentile"])
            synthesized = feedback.synthesize_feedback(cand_rationales, model)

        # Persist each round as a job-scoped comparison (Explorer-style detail),
        # and expose a lightweight summary on the result for the frontend to list
        # and lazy-load via GET /submissions/<id>/comparisons/<round>.
        comparisons = feedback.candidate_comparisons(
            result, pool_elos,
            candidate_id=result["candidate_id"],
            candidate_image_url=job["image_url"],
            candidate_title=job["title"],
            model=model,
        )
        ddb.put_job_comparisons(job_id, pool_id, comparisons)
        comparisons_summary = [{
            "cmp_id": c["comparison_id"],
            "phase": c["phase"],
            "size": c["size"],
            "rank": c["candidate_rank"],
            "flagged_not_art": c["candidate_flagged_not_art"],
            "avg_pctl": c["avg_pctl"],
            "min_pctl": c["min_pctl"],
            "max_pctl": c["max_pctl"],
        } for c in comparisons]

        enriched = {
            **{k: v for k, v in result.items() if k != "rounds"},
            "headline": headline,
            "feedback": synthesized,
            "comparisons": comparisons_summary,
            "image_url": job["image_url"],
            "pool_id": pool_id,
            "pool_label": pool_def.label,
        }
        ddb.update_job(job_id, status="done", result=enriched)

        # Signed-in submission -> promote into the user's permanent salon.
        if job.get("user_sub"):
            _persist_to_salon(job["user_sub"], job_id, job, enriched, comparisons)
        return {"ok": True, "job_id": job_id}
    except Exception as e:  # noqa: BLE001
        ddb.update_job(job_id, status="error", error=str(e)[:500])
        return {"ok": False, "error": str(e)}


def _persist_to_salon(user_sub: str, job_id: str, job: dict,
                      enriched: dict, comparisons: list) -> None:
    """Copy the (transient) upload to a permanent user-owned key and write the
    finished result + its round comparisons under USER#<sub> with no TTL.

    Best-effort: the public report already succeeded, so a salon-persist failure
    is logged via the exception but must not fail the job."""
    try:
        src_key = job["image_key"]
        ext = src_key.rsplit(".", 1)[-1] if "." in src_key else "jpg"
        dest_key = f"{USER_ASSET_PREFIX}{user_sub}/{job_id}.{ext}"
        try:
            _s3.copy_object(
                Bucket=S3_BUCKET,
                CopySource={"Bucket": S3_BUCKET, "Key": src_key},
                Key=dest_key,
            )
            image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{dest_key}"
        except Exception:  # noqa: BLE001 — fall back to the transient URL
            dest_key, image_url = src_key, job["image_url"]

        ddb.put_user_piece(user_sub, {
            "piece_id": job_id,
            "pool_id": enriched.get("pool_id"),
            "pool_label": enriched.get("pool_label"),
            "image_key": dest_key,
            "image_url": image_url,
            "percentile": enriched.get("percentile"),
            "not_art": bool(enriched.get("not_art")),
            "not_art_reason": enriched.get("not_art_reason", ""),
            "headline": enriched.get("headline"),
            "feedback": enriched.get("feedback", ""),
            "comparisons": enriched.get("comparisons", []),
            "submitted_at": job.get("created_at"),
        })
        ddb.put_user_piece_comparisons(user_sub, job_id, comparisons)
    except Exception as e:  # noqa: BLE001 — never let salon-persist fail the job
        print(f"[salon] persist failed for {user_sub}/{job_id}: {e}")
