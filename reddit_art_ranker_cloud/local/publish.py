"""Publish a locally-ranked pool from SQLite into AWS (DynamoDB + optional S3).

This is the bridge between the local experiment and the cloud. It reads the
finished leaderboard out of the original module's `reddit_rankings.db`, then:

  1. (optional) mirrors each piece's image into S3 so the cloud app never
     depends on reddit/imgur hot-links (some preview URLs are signed and expire).
  2. batch-writes one DynamoDB item per piece under the pool's partition, plus
     a single pool-metadata item.

Idempotent: re-running overwrites the same items (PutItem on a deterministic
key). Run it again after a re-rank to refresh ELOs.

Usage:
    python -m reddit_art_ranker_cloud.local.publish \\
        --sqlite ../reddit_art_ranker/reddit_rankings.db \\
        --subreddit Watercolor --pool watercolor --mirror-images

Requires AWS credentials in the environment (e.g. `aws configure` / SSO) and
the SAM stack already deployed (so the table + bucket exist).
"""

import argparse
import hashlib
import sqlite3
import sys
from decimal import Decimal

import boto3
import urllib.request

from shared.config import (
    DDB_TABLE,
    ELO_INITIAL,
    NOT_ART_EXCLUDE_AT,
    S3_BUCKET,
)
from shared.pools import get_pool

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_IMG_EXT_BY_CT = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
}


def _num(v, default=0):
    """DynamoDB rejects float; everything numeric goes in as Decimal."""
    if v is None or v == "":
        return Decimal(str(default))
    return Decimal(str(v))


def _read_pool(sqlite_path: str, subreddit: str) -> list[dict]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.reddit_id, p.title, p.author, p.permalink, p.image_url,
               p.upvotes, p.num_comments, p.upvote_ratio, p.awards, p.created_utc,
               r.elo, r.n_comparisons, r.n_not_art_flags
        FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
        WHERE p.subreddit = ? AND p.is_candidate = 0
        ORDER BY r.elo DESC
        """,
        (subreddit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _mirror_to_s3(s3, bucket: str, pool_id: str, piece_id: str, url: str) -> str | None:
    """Download a piece image and re-upload to S3. Returns the new public URL,
    or None on failure (caller keeps the original URL as a fallback)."""
    key = f"pool/{pool_id}/{piece_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            ct = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].lower()
            data = resp.read()
        ext = _IMG_EXT_BY_CT.get(ct, "jpg")
        full_key = f"{key}.{ext}"
        s3.put_object(Bucket=bucket, Key=full_key, Body=data, ContentType=ct,
                      CacheControl="public, max-age=31536000")
        return f"https://{bucket}.s3.amazonaws.com/{full_key}"
    except Exception as e:
        print(f"    ! mirror failed for {piece_id}: {e}")
        return None


def publish(sqlite_path: str, subreddit: str, pool_id: str,
            mirror_images: bool, table_name: str, bucket: str) -> None:
    pool = get_pool(pool_id)  # validates pool_id
    pieces = _read_pool(sqlite_path, subreddit)
    if not pieces:
        raise SystemExit(f"No non-candidate pieces for subreddit '{subreddit}' "
                         f"in {sqlite_path}.")

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    s3 = boto3.client("s3") if mirror_images else None

    eligible = [p for p in pieces if (p["n_not_art_flags"] or 0) < NOT_ART_EXCLUDE_AT]
    print(f"Publishing pool '{pool_id}' ({pool.label}): {len(pieces)} pieces "
          f"({len(eligible)} eligible) -> table {table_name}"
          + (f", mirroring images to s3://{bucket}" if mirror_images else ""))

    with table.batch_writer() as batch:
        for i, p in enumerate(pieces, 1):
            piece_id = p["reddit_id"]
            image_url = p["image_url"]
            if mirror_images:
                mirrored = _mirror_to_s3(s3, bucket, pool_id, piece_id, image_url)
                if mirrored:
                    image_url = mirrored
            item = {
                "pk": f"POOL#{pool_id}",
                "sk": f"PIECE#{piece_id}",
                "type": "piece",
                "pool_id": pool_id,
                "piece_id": piece_id,
                "title": p["title"] or "",
                "author": p["author"] or "",
                "permalink": p["permalink"] or "",
                "image_url": image_url,
                "source_image_url": p["image_url"],  # keep the original too
                "upvotes": _num(p["upvotes"]),
                "num_comments": _num(p["num_comments"]),
                "upvote_ratio": _num(p["upvote_ratio"], None) if p["upvote_ratio"] is not None else None,
                "awards": _num(p["awards"]),
                "created_utc": _num(p["created_utc"]),
                "elo": _num(p["elo"], ELO_INITIAL),
                "n_comparisons": int(p["n_comparisons"] or 0),
                "n_not_art_flags": int(p["n_not_art_flags"] or 0),
                "is_candidate": False,
            }
            batch.put_item(Item={k: v for k, v in item.items() if v is not None})
            if i % 25 == 0 or i == len(pieces):
                print(f"  wrote {i}/{len(pieces)}")

    # Pool metadata item (used by GET /pools and to show counts in the UI)
    elos = sorted((float(p["elo"]) for p in eligible))
    table.put_item(Item={
        "pk": f"POOL#{pool_id}",
        "sk": "META",
        "type": "pool_meta",
        "pool_id": pool_id,
        "label": pool.label,
        "jury_subject": pool.jury_subject,
        "subreddit": subreddit,
        "n_pieces": len(eligible),
        "elo_min": _num(elos[0]) if elos else _num(ELO_INITIAL),
        "elo_max": _num(elos[-1]) if elos else _num(ELO_INITIAL),
    })
    print(f"Done. Pool '{pool_id}' is live with {len(eligible)} eligible pieces.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", required=True, help="Path to reddit_rankings.db")
    ap.add_argument("--subreddit", required=True,
                    help="Subreddit value as stored in the SQLite pieces table")
    ap.add_argument("--pool", required=True, help="Target cloud pool id (see shared/pools.py)")
    ap.add_argument("--mirror-images", action="store_true",
                    help="Download each image and re-host in S3 (durable, recommended)")
    ap.add_argument("--table", default=DDB_TABLE)
    ap.add_argument("--bucket", default=S3_BUCKET)
    args = ap.parse_args()
    publish(args.sqlite, args.subreddit, args.pool, args.mirror_images,
            args.table, args.bucket)


if __name__ == "__main__":
    main()
