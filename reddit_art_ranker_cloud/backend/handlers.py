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
    WORKER_FUNCTION,
)
from shared.pools import POOLS, get_pool
from . import ddb, feedback
from .insert import insert

_s3 = boto3.client("s3")
_lambda = boto3.client("lambda")

_CORS = {
    "Access-Control-Allow-Origin": os.getenv("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
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

    try:
        if method == "GET" and path == "/pools":
            return _get_pools()
        if method == "POST" and path == "/uploads":
            return _create_upload(json.loads(event.get("body") or "{}"))
        if method == "POST" and path == "/submissions":
            return _create_submission(json.loads(event.get("body") or "{}"))
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


def _create_submission(body: dict):
    pool_id = body["pool"]
    get_pool(pool_id)  # validate
    image_key = body["image_key"]
    title = (body.get("title") or "Untitled").strip()[:140]
    job_id = uuid.uuid4().hex[:16]

    # Public-ish read URL for the report (the object also stays presign-able).
    image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{image_key}"
    ddb.create_job(job_id, pool_id, title, image_key, image_url, _now())

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
            model=model,
            n_groups=INSERTION_GROUPS,
            progress_cb=progress,
        )

        pool_elos = result.pop("pool_elos")
        cand_rationales = [
            rd["candidate"].get("rationale_this_round")
            for rd in result["rounds"] if rd.get("candidate")
        ]
        enriched = {
            **{k: v for k, v in result.items() if k != "rounds"},
            "headline": feedback.verdict_headline(result["percentile"]),
            "feedback": feedback.synthesize_feedback(cand_rationales, model),
            "tiers": feedback.comparison_tiers(result, pool_elos),
            "rounds": result["rounds"],  # full detail for the collapsible section
            "image_url": job["image_url"],
            "pool_label": pool_def.label,
        }
        ddb.update_job(job_id, status="done", result=enriched)
        return {"ok": True, "job_id": job_id}
    except Exception as e:  # noqa: BLE001
        ddb.update_job(job_id, status="error", error=str(e)[:500])
        return {"ok": False, "error": str(e)}
