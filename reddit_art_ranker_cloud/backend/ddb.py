"""DynamoDB data layer for the cloud backend.

Single-table design (table name from DDB_TABLE):

  Pieces      pk=POOL#<pool>   sk=PIECE#<piece_id>   (written by local/publish.py)
  Pool meta   pk=POOL#<pool>   sk=META
  Jobs        pk=JOB#<job_id>  sk=META               (one consumer submission)

A pool is ~100 small items, so loading the whole eligible anchor set for an
insertion is a single Query — no GSI needed. Floats are stored as Decimal and
converted back to float on read (`_floats`).
"""

import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from shared.config import DDB_TABLE, NOT_ART_EXCLUDE_AT, SUBMISSION_TTL_SECONDS

_table = None


def _t():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(DDB_TABLE)
    return _table


def _floats(obj):
    """Recursively turn DynamoDB Decimals into float/int for app use."""
    if isinstance(obj, list):
        return [_floats(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _floats(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def _nums(obj):
    """Inverse of _floats — turn floats into Decimal before writing."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_nums(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _nums(v) for k, v in obj.items()}
    return obj


# ── Pools / pieces ──────────────────────────────────────────────────────────
def list_pools() -> list[dict]:
    """All pool-meta items (one per pool). Used by GET /pools.

    Pool metas and job records *both* use sk="META" (and both carry a pool_id),
    so filtering on sk alone sweeps jobs into the pool list, where a job can
    clobber its pool's real meta in the caller's pool_id->item map. The pk
    prefix is the discriminator: pool metas live under POOL#<id>, jobs under
    JOB#<id>. Paginate so the list stays complete as the table grows.
    """
    items, kwargs = [], dict(
        FilterExpression=Attr("pk").begins_with("POOL#") & Attr("sk").eq("META"),
    )
    while True:
        resp = _t().scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return _floats(items)


def get_pool_meta(pool_id: str) -> dict | None:
    resp = _t().get_item(Key={"pk": f"POOL#{pool_id}", "sk": "META"})
    item = resp.get("Item")
    return _floats(item) if item else None


def get_comparison(pool_id: str, cmp_id) -> dict | None:
    """One jury comparison record (group of pieces + ranking + rationales).
    Written by local/publish.py's publish_comparisons."""
    try:
        cid = int(cmp_id)
    except (TypeError, ValueError):
        return None
    resp = _t().get_item(Key={"pk": f"POOL#{pool_id}", "sk": f"CMP#{cid:06d}"})
    item = resp.get("Item")
    return _floats(item) if item else None


def batch_get_pieces(pool_id: str, piece_ids: list) -> list[dict]:
    """Fetch specific pieces by id (≤5 per comparison). Used to enrich a
    comparison's members with image_url/title/percentile/rank.

    Uses point GetItems rather than BatchGetItem: the set is tiny, and the
    Lambda role grants GetItem but not BatchGetItem (see infra/iam/role-policy)."""
    out = []
    for pid in dict.fromkeys(piece_ids or []):
        resp = _t().get_item(Key={"pk": f"POOL#{pool_id}", "sk": f"PIECE#{pid}"})
        item = resp.get("Item")
        if item:
            out.append(item)
    return _floats(out)


def get_piece(pool_id: str, piece_id: str) -> dict | None:
    """One published piece by id (for the dedicated piece page)."""
    resp = _t().get_item(Key={"pk": f"POOL#{pool_id}", "sk": f"PIECE#{piece_id}"})
    item = resp.get("Item")
    return _floats(item) if item else None


def load_eligible_pool(pool_id: str) -> list[dict]:
    """Every non-candidate, non-excluded piece in the pool (anchor candidates).
    Paginates the Query so pools larger than 1 page are fully loaded."""
    items, kwargs = [], dict(
        KeyConditionExpression=Key("pk").eq(f"POOL#{pool_id}")
        & Key("sk").begins_with("PIECE#"),
    )
    while True:
        resp = _t().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    pieces = _floats(items)
    return [p for p in pieces
            if int(p.get("n_not_art_flags", 0)) < NOT_ART_EXCLUDE_AT
            and not p.get("is_candidate")]


# ── Jobs (consumer submissions) ─────────────────────────────────────────────
def create_job(job_id: str, pool_id: str, title: str, image_key: str,
               image_url: str, created_at: str) -> None:
    _t().put_item(Item=_nums({
        "pk": f"JOB#{job_id}",
        "sk": "META",
        "type": "job",
        "job_id": job_id,
        "pool_id": pool_id,
        "title": title,
        "image_key": image_key,
        "image_url": image_url,
        "status": "queued",
        "progress": {"round": 0, "total": 0},
        "created_at": created_at,
        "ttl": int(time.time()) + SUBMISSION_TTL_SECONDS,
    }))


def update_job(job_id: str, **fields) -> None:
    fields = _nums(fields)
    expr = ", ".join(f"#{k} = :{k}" for k in fields)
    _t().update_item(
        Key={"pk": f"JOB#{job_id}", "sk": "META"},
        UpdateExpression="SET " + expr,
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
    )


def get_job(job_id: str) -> dict | None:
    resp = _t().get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})
    item = resp.get("Item")
    return _floats(item) if item else None


def put_job_comparisons(job_id: str, pool_id: str, comparisons: list,
                        ttl_seconds: int = SUBMISSION_TTL_SECONDS) -> None:
    """Persist a submission's per-round jury comparisons as CMP#<round> items
    under the job's partition (same shape as the published pool comparisons, but
    job-scoped so they don't pollute the public scoreboard and expire with the
    job's TTL)."""
    if not comparisons:
        return
    ttl = int(time.time()) + ttl_seconds
    with _t().batch_writer() as batch:
        for c in comparisons:
            batch.put_item(Item=_nums({
                "pk": f"JOB#{job_id}",
                "sk": f"CMP#{int(c['comparison_id']):06d}",
                "type": "comparison",
                "job_id": job_id,
                "pool_id": pool_id,
                "ttl": ttl,
                **c,
            }))


def get_job_comparison(job_id: str, cmp_id) -> dict | None:
    """One round's comparison detail for a submission."""
    try:
        cid = int(cmp_id)
    except (TypeError, ValueError):
        return None
    resp = _t().get_item(Key={"pk": f"JOB#{job_id}", "sk": f"CMP#{cid:06d}"})
    item = resp.get("Item")
    return _floats(item) if item else None
