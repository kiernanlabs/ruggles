"""Load posts from a pushshift dump file (NDJSON or .zst).

The dumps live at /r/pushshift's torrent ("separate dump files for the top 40k
subreddits"). Each subreddit ships as <Subreddit>_submissions.zst — one JSON
post per line after decompression. Records span 2012-2025 and field shape
varies (older posts lack upvote_ratio, media_metadata, etc).

Usage:
    python -m reddit_art_ranker.fetch_pushshift \\
        reddit_art_ranker/reddit/subreddits25/Watercolor_submissions/Watercolor_submissions

    python -m reddit_art_ranker.fetch_pushshift path/to/file.zst \\
        --since 2023-01-01 --limit 100 --min-score 20
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

# Reddit titles routinely include emoji that Windows' default cp1252 console
# can't encode. Replace unprintable chars instead of letting the print crash
# (which would abort the DB transaction mid-batch).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from . import db
from .config import SUBREDDIT, TARGET_POST_COUNT

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
REDDIT_IMAGE_DOMAINS = ("i.redd.it", "i.imgur.com")
ZSTD_MAX_WINDOW = 2 ** 31  # u/Watchful1 dumps use a 2GB window


def _stream_lines(path: Path):
    """Yield decoded text lines from NDJSON or .zst NDJSON."""
    if path.suffix == ".zst":
        try:
            import zstandard as zstd
        except ImportError as e:
            raise RuntimeError(
                "Reading .zst requires `pip install zstandard`, or point at "
                "the already-decompressed NDJSON file instead."
            ) from e
        dctx = zstd.ZstdDecompressor(max_window_size=ZSTD_MAX_WINDOW)
        with open(path, "rb") as f, dctx.stream_reader(f) as reader:
            buf = b""
            while True:
                chunk = reader.read(2 ** 22)
                if not chunk:
                    break
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines.pop()
                for line in lines:
                    if line:
                        yield line.decode("utf-8", errors="replace")
            if buf:
                yield buf.decode("utf-8", errors="replace")
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    yield line


def _extract_image_url(post: dict) -> str | None:
    """Best-effort: return a stable image URL for an OpenAI vision call."""
    url = post.get("url") or ""
    lower = url.lower()

    # Direct image link (most common in r/Watercolor)
    if lower.endswith(IMAGE_EXTS):
        return url
    if any(d in lower for d in REDDIT_IMAGE_DOMAINS) and "/" in url:
        return url

    # Reddit gallery
    if post.get("is_gallery") and isinstance(post.get("media_metadata"), dict):
        try:
            items = post["gallery_data"]["items"]
            first_id = items[0]["media_id"]
            meta = post["media_metadata"][first_id]
            if meta.get("s", {}).get("u"):
                return meta["s"]["u"].replace("&amp;", "&")
        except (KeyError, TypeError, IndexError):
            pass

    # Self-post with embedded media (newer reddit composer)
    md = post.get("media_metadata")
    if isinstance(md, dict) and md:
        first = next(iter(md.values()))
        if isinstance(first, dict) and first.get("s", {}).get("u"):
            return first["s"]["u"].replace("&amp;", "&")

    # Preview fallback (signed URLs — may have expired for old posts)
    preview = post.get("preview")
    if isinstance(preview, dict):
        images = preview.get("images") or []
        if images and images[0].get("source", {}).get("u"):
            return images[0]["source"]["u"].replace("&amp;", "&")
        # older schema used 'url' instead of 'u'
        if images and images[0].get("source", {}).get("url"):
            return images[0]["source"]["url"].replace("&amp;", "&")

    return None


def _parse_since(s: str | None) -> float:
    if not s:
        return 0.0
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def _as_float(v, default: float = 0.0) -> float:
    """Pushshift fields are sometimes strings in older dumps."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _head_check(image_url: str, timeout: float = 6.0) -> bool:
    """True if URL returns HTTP 200 with an image content-type."""
    try:
        req = urllib.request.Request(
            image_url, method="HEAD", headers={"User-Agent": "curl/8"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = (resp.headers.get("Content-Type") or "").lower()
            return resp.status == 200 and "image" in ct
    except Exception:
        return False


def _validate_urls(candidates: list, target: int, workers: int = 20) -> list:
    """Concurrently HEAD-check candidate (post, image_url) tuples. Keep them
    in their original (already-sorted) order and stop once we have `target`
    confirmed-good entries. Returns the kept list."""
    kept = []
    print(f"Validating image URLs (need {target} good ones from up to {len(candidates)})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        # Submit in batches of ~workers*4 so we don't validate all 200+ upfront
        # if we'll hit the target early.
        i = 0
        batch_size = max(workers, 50)
        while i < len(candidates) and len(kept) < target:
            batch = candidates[i : i + batch_size]
            futures = {ex.submit(_head_check, url): (post, url) for post, url in batch}
            for fut in concurrent.futures.as_completed(futures):
                post, url = futures[fut]
                try:
                    if fut.result():
                        kept.append((post, url))
                except Exception:
                    pass
            i += batch_size
            print(f"  validated {min(i, len(candidates))}/{len(candidates)}, kept {len(kept)}")
    # Re-sort kept entries back into the candidates' original order
    order = {id(c[0]): idx for idx, c in enumerate(candidates)}
    kept.sort(key=lambda x: order.get(id(x[0]), 1 << 30))
    return kept[:target]


def fetch(
    dump_path: Path,
    subreddit: str,
    limit: int,
    min_score: int,
    since_ts: float,
    sort_by: str,
    validate: bool = True,
    oversample: float = 1.5,
    exclude_ids: set | None = None,
) -> int:
    exclude_ids = exclude_ids or set()
    if exclude_ids:
        print(f"Excluding {len(exclude_ids)} already-stored pieces from selection.")
    print(f"Streaming {dump_path}...")
    candidates = []
    seen = kept = skipped_existing = 0
    for line in _stream_lines(dump_path):
        seen += 1
        if seen % 25000 == 0:
            print(f"  scanned {seen:>7,}  kept {kept:>5,}")
        try:
            post = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (post.get("subreddit") or "").lower() != subreddit.lower():
            continue
        if post.get("id") in exclude_ids:
            skipped_existing += 1
            continue
        if post.get("over_18"):
            continue
        if _as_float(post.get("created_utc")) < since_ts:
            continue
        if _as_int(post.get("score")) < min_score:
            continue
        if post.get("removed_by_category"):
            continue
        image_url = _extract_image_url(post)
        if not image_url:
            continue
        candidates.append((post, image_url))
        kept += 1

    print(f"\nScanned {seen:,} records; {kept:,} usable image posts in r/{subreddit}"
          + (f" ({skipped_existing:,} skipped as already-stored)." if exclude_ids else "."))

    if sort_by == "score":
        candidates.sort(key=lambda x: _as_int(x[0].get("score")), reverse=True)
    elif sort_by == "created":
        candidates.sort(key=lambda x: _as_float(x[0].get("created_utc")), reverse=True)
    elif sort_by == "random":
        import random
        random.shuffle(candidates)
    else:
        raise ValueError(f"Unknown sort: {sort_by}")

    # Oversample before validation so dead URLs don't leave us short.
    pool_size = int(limit * oversample) if validate else limit
    pool = candidates[:pool_size]
    print(f"Pre-validation pool: {len(pool)} (oversample factor {oversample}x).")

    if validate:
        selected = _validate_urls(pool, target=limit)
        print(f"After URL validation: {len(selected)} good pieces.")
        if len(selected) < limit:
            print(f"WARNING: only got {len(selected)}/{limit}. Bump --oversample.")
    else:
        selected = pool[:limit]
    print(f"Selecting {len(selected)} by {sort_by}.\n")

    saved = 0
    with db.connect() as conn:
        for post, image_url in selected:
            permalink = post.get("permalink")
            piece = {
                "reddit_id": post["id"],
                "subreddit": subreddit,
                "title": post.get("title"),
                "author": post.get("author"),
                "permalink": f"https://reddit.com{permalink}" if permalink else None,
                "image_url": image_url,
                "upvotes": _as_int(post.get("score")),
                "num_comments": _as_int(post.get("num_comments")),
                "upvote_ratio": (
                    _as_float(post["upvote_ratio"])
                    if post.get("upvote_ratio") not in (None, "") else None
                ),
                "awards": _as_int(post.get("total_awards_received")),
                "created_utc": _as_float(post.get("created_utc")),
            }
            db.upsert_piece(conn, piece, is_candidate=False)
            saved += 1
            when = dt.datetime.utcfromtimestamp(piece["created_utc"]).date()
            print(
                f"  [{saved:>3}/{len(selected)}] {post['id']:>10}  "
                f"score={piece['upvotes']:>5}  {when}  "
                f"{(post.get('title') or '')[:60]}"
            )

    print(f"\nSaved {saved} pieces from r/{subreddit}.")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_path", type=Path,
                        help="Path to *_submissions or *_submissions.zst")
    parser.add_argument("--subreddit", default=SUBREDDIT,
                        help="Stored subreddit value + filter (case-insensitive)")
    parser.add_argument("--limit", type=int, default=TARGET_POST_COUNT)
    parser.add_argument("--min-score", type=int, default=20,
                        help="Drop posts below this score (filters spam + dead posts)")
    parser.add_argument("--since", default="2023-01-01",
                        help="Earliest created date (YYYY-MM-DD), or empty for all-time")
    parser.add_argument("--sort", default="score",
                        choices=["score", "created", "random"])
    parser.add_argument("--no-validate", action="store_true",
                        help="Skip HEAD-checking image URLs before saving")
    parser.add_argument("--oversample", type=float, default=1.5,
                        help="Pre-validation pool = limit * this multiplier")
    parser.add_argument("--exclude-existing", action="store_true",
                        help="Skip posts already stored for this subreddit (for "
                             "incrementally extending a dataset with net-new pieces).")
    args = parser.parse_args()
    if not args.dump_path.exists():
        raise SystemExit(f"Dump file not found: {args.dump_path}")
    exclude_ids = None
    if args.exclude_existing:
        with db.connect() as conn:
            exclude_ids = {r["reddit_id"] for r in
                           db.get_pieces(conn, args.subreddit, include_candidates=True)}
    fetch(
        args.dump_path,
        args.subreddit,
        args.limit,
        args.min_score,
        _parse_since(args.since),
        args.sort,
        validate=not args.no_validate,
        oversample=args.oversample,
        exclude_ids=exclude_ids,
    )


if __name__ == "__main__":
    main()
