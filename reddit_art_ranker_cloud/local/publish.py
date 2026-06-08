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
import json
import sqlite3
import sys
from decimal import Decimal

import boto3
import urllib.request

# Load AWS creds + region from the project-root .env before any boto3 client.
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")
except Exception:
    pass

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


def _read_comparisons(sqlite_path: str, subreddit: str) -> list[dict]:
    """Initial-ranking jury rounds (candidate_id IS NULL) for a subreddit. The
    consumer-insertion rounds (candidate_id set) are tied to ephemeral candidates
    and are intentionally excluded from the public scoreboard."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, created_at, model, piece_ids_json, ranking_json, rationale,
               per_piece_rationales_json
        FROM comparisons
        WHERE subreddit = ? AND candidate_id IS NULL
        ORDER BY id
        """,
        (subreddit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def publish_comparisons(sqlite_path: str, subreddit: str, pool_id: str,
                        table_name: str) -> None:
    """Publish the jury comparison records + per-piece rank/percentile that power
    the Scoreboard Explorer. Does NOT touch piece images — it only adds CMP#<id>
    items and updates rank/percentile/comparisons_summary on existing PIECE rows
    in place, so it's safe to run after a normal image-mirroring publish.
    """
    pool = get_pool(pool_id)  # validates pool_id
    pieces = _read_pool(sqlite_path, subreddit)  # non-candidate, elo DESC
    eligible = [p for p in pieces if (p["n_not_art_flags"] or 0) < NOT_ART_EXCLUDE_AT]
    if not eligible:
        raise SystemExit(f"No eligible pieces for subreddit '{subreddit}'.")

    # Rank + percentile over the eligible set (same convention as the consumer
    # path: percentile = share of the pool rated lower).
    elos = [float(p["elo"]) for p in eligible]
    n = len(elos)
    pct_by_id, rank_by_id = {}, {}
    for p in eligible:
        e = float(p["elo"])
        pct_by_id[p["reddit_id"]] = int(round(100.0 * sum(1 for x in elos if x < e) / n))
        rank_by_id[p["reddit_id"]] = sum(1 for x in elos if x > e) + 1

    comparisons = _read_comparisons(sqlite_path, subreddit)
    table = boto3.resource("dynamodb").Table(table_name)
    print(f"Publishing {len(comparisons)} comparisons for pool '{pool_id}' "
          f"({pool.label}) -> table {table_name}")

    # Write comparison items + accumulate each piece's per-comparison summary.
    summaries: dict[str, list] = {}
    with table.batch_writer() as batch:
        for c in comparisons:
            cid = int(c["id"])
            piece_ids = json.loads(c["piece_ids_json"])
            ranking = json.loads(c["ranking_json"]) if c["ranking_json"] else []
            per_piece = (json.loads(c["per_piece_rationales_json"])
                         if c["per_piece_rationales_json"] else [])
            member_pcts = [pct_by_id[pid] for pid in piece_ids if pid in pct_by_id]
            avg_pctl = round(sum(member_pcts) / len(member_pcts), 1) if member_pcts else None
            min_pctl = min(member_pcts) if member_pcts else None
            max_pctl = max(member_pcts) if member_pcts else None

            batch.put_item(Item={
                "pk": f"POOL#{pool_id}",
                "sk": f"CMP#{cid:06d}",
                "type": "comparison",
                "pool_id": pool_id,
                "comparison_id": cid,
                "piece_ids": piece_ids,
                "ranking": ranking,
                "rationale": c["rationale"] or "",
                "per_piece": [{"piece_id": x.get("piece_id"),
                               "rationale": x.get("rationale") or ""}
                              for x in per_piece],
                "model": c["model"] or "",
                "created_at": c["created_at"] or "",
            })
            for pid in piece_ids:
                if pid not in pct_by_id:  # excluded piece — not browsable, skip
                    continue
                summaries.setdefault(pid, []).append({
                    "cmp_id": cid,
                    "rank": (ranking.index(pid) + 1) if pid in ranking else None,
                    "size": len(piece_ids),
                    "avg_pctl": Decimal(str(avg_pctl)) if avg_pctl is not None else None,
                    "min_pctl": min_pctl,
                    "max_pctl": max_pctl,
                })

    # Update each eligible piece in place (rank is a DynamoDB reserved word).
    for i, p in enumerate(eligible, 1):
        pid = p["reddit_id"]
        table.update_item(
            Key={"pk": f"POOL#{pool_id}", "sk": f"PIECE#{pid}"},
            UpdateExpression="SET #pct = :pct, #rnk = :rnk, #cs = :cs",
            ExpressionAttributeNames={"#pct": "percentile", "#rnk": "rank",
                                      "#cs": "comparisons_summary"},
            ExpressionAttributeValues={":pct": pct_by_id[pid], ":rnk": rank_by_id[pid],
                                       ":cs": summaries.get(pid, [])},
        )
        if i % 50 == 0 or i == len(eligible):
            print(f"  updated {i}/{len(eligible)} piece rankings")
    print(f"Done. {len(comparisons)} comparisons + {len(eligible)} rankings "
          f"published for '{pool_id}'.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", required=True, help="Path to reddit_rankings.db")
    ap.add_argument("--subreddit", required=True,
                    help="Subreddit value as stored in the SQLite pieces table")
    ap.add_argument("--pool", required=True, help="Target cloud pool id (see shared/pools.py)")
    ap.add_argument("--mirror-images", action="store_true",
                    help="Download each image and re-host in S3 (durable, recommended)")
    ap.add_argument("--with-comparisons", action="store_true",
                    help="Also publish the comparisons table + per-piece rank/percentile "
                         "(powers the Scoreboard Explorer)")
    ap.add_argument("--comparisons-only", action="store_true",
                    help="Publish ONLY comparisons + rank/percentile; skip the piece/image "
                         "(re)publish. Use to add explorer data to an already-published pool.")
    ap.add_argument("--table", default=DDB_TABLE)
    ap.add_argument("--bucket", default=S3_BUCKET)
    args = ap.parse_args()
    if args.comparisons_only:
        publish_comparisons(args.sqlite, args.subreddit, args.pool, args.table)
        return
    publish(args.sqlite, args.subreddit, args.pool, args.mirror_images,
            args.table, args.bucket)
    if args.with_comparisons:
        publish_comparisons(args.sqlite, args.subreddit, args.pool, args.table)


if __name__ == "__main__":
    main()
