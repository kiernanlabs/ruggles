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
from boto3.dynamodb.conditions import Key

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
    """All pool-meta items (one per pool). Used by GET /pools."""
    resp = _t().scan(
        FilterExpression=Key("sk").eq("META"),
    )
    return _floats(resp.get("Items", []))


def get_pool_meta(pool_id: str) -> dict | None:
    resp = _t().get_item(Key={"pk": f"POOL#{pool_id}", "sk": "META"})
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
