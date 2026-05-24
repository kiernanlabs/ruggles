"""Pull recent image posts from a subreddit into the local SQLite store.

Usage:
    python -m reddit_art_ranker.fetch_reddit
    python -m reddit_art_ranker.fetch_reddit --subreddit Watercolour --limit 100
"""

import argparse
import os

import praw
from dotenv import load_dotenv

from . import db
from .config import (
    REDDIT_LISTING,
    REDDIT_TIME_FILTER,
    REDDIT_USER_AGENT,
    SUBREDDIT,
    TARGET_POST_COUNT,
)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _reddit() -> praw.Reddit:
    load_dotenv()
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise RuntimeError(
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env "
            "(create a script-type app at https://www.reddit.com/prefs/apps)."
        )
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=REDDIT_USER_AGENT,
    )


def _extract_image_url(submission) -> str | None:
    url = submission.url or ""
    if url.lower().endswith(IMAGE_EXTS):
        return url
    if getattr(submission, "is_gallery", False):
        try:
            first_id = submission.gallery_data["items"][0]["media_id"]
            meta = submission.media_metadata[first_id]
            variants = meta.get("p") or []
            if variants:
                return variants[-1]["u"].replace("&amp;", "&")
            return meta["s"]["u"].replace("&amp;", "&")
        except (KeyError, AttributeError, IndexError):
            return None
    preview = getattr(submission, "preview", None)
    if isinstance(preview, dict):
        images = preview.get("images") or []
        if images:
            return images[0]["source"]["url"].replace("&amp;", "&")
    return None


def fetch(subreddit: str, limit: int, listing: str, time_filter: str) -> int:
    reddit = _reddit()
    sub = reddit.subreddit(subreddit)

    if listing == "top":
        stream = sub.top(time_filter=time_filter, limit=limit * 3)
    elif listing == "hot":
        stream = sub.hot(limit=limit * 3)
    elif listing == "new":
        stream = sub.new(limit=limit * 3)
    else:
        raise ValueError(f"Unknown listing: {listing}")

    saved = 0
    with db.connect() as conn:
        for submission in stream:
            if saved >= limit:
                break
            if submission.over_18 or submission.removed_by_category:
                continue
            image_url = _extract_image_url(submission)
            if not image_url:
                continue
            piece = {
                "reddit_id": submission.id,
                "subreddit": subreddit,
                "title": submission.title,
                "author": str(submission.author) if submission.author else None,
                "permalink": f"https://reddit.com{submission.permalink}",
                "image_url": image_url,
                "upvotes": int(submission.score),
                "num_comments": int(submission.num_comments),
                "upvote_ratio": float(submission.upvote_ratio),
                "awards": int(getattr(submission, "total_awards_received", 0) or 0),
                "created_utc": float(submission.created_utc),
            }
            db.upsert_piece(conn, piece, is_candidate=False)
            saved += 1
            print(f"  [{saved:>3}/{limit}] {submission.id}  {submission.title[:70]}")

    print(f"\nSaved {saved} pieces from r/{subreddit}.")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--limit", type=int, default=TARGET_POST_COUNT)
    parser.add_argument("--listing", default=REDDIT_LISTING, choices=["top", "hot", "new"])
    parser.add_argument("--time-filter", default=REDDIT_TIME_FILTER,
                        choices=["hour", "day", "week", "month", "year", "all"])
    args = parser.parse_args()
    fetch(args.subreddit, args.limit, args.listing, args.time_filter)


if __name__ == "__main__":
    main()
