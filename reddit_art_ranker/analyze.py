"""Leaderboard + correlation of ELO with Reddit engagement.

Usage:
    python -m reddit_art_ranker.analyze
    python -m reddit_art_ranker.analyze --subreddit Watercolor --top 20
"""

import argparse
import csv
from pathlib import Path
from statistics import mean

from . import db
from .config import MODULE_DIR, SUBREDDIT


def _spearman(xs: list, ys: list) -> float | None:
    """Spearman rank correlation, stdlib only."""
    if len(xs) < 3:
        return None

    def ranks(values):
        sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
        r = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and values[sorted_idx[j + 1]] == values[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[sorted_idx[k]] = avg_rank
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den_x = sum((a - mx) ** 2 for a in rx) ** 0.5
    den_y = sum((b - my) ** 2 for b in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def run(subreddit: str, top_n: int, export_csv: bool) -> None:
    with db.connect() as conn:
        rows = db.get_ratings(conn, subreddit)

    if not rows:
        print(f"No pieces in r/{subreddit}.")
        return

    rows = [dict(r) for r in rows]
    candidates = [r for r in rows if r["is_candidate"]]
    full_pool = [r for r in rows if not r["is_candidate"]]
    excluded = [r for r in full_pool if (r.get("n_not_art_flags") or 0) >= 2]
    pool = [r for r in full_pool if (r.get("n_not_art_flags") or 0) < 2]

    print(f"\n=== r/{subreddit} leaderboard "
          f"({len(pool)} eligible pieces, {len(excluded)} excluded as not-art, "
          f"{len(candidates)} candidates) ===\n")
    print(f"{'Rank':>4}  {'ELO':>7}  {'N':>3}  {'Up':>6}  {'Cmt':>4}  Title")
    print("-" * 100)
    for i, r in enumerate(pool[:top_n], 1):
        title = (r["title"] or "")[:50]
        print(
            f"{i:>4}  {r['elo']:>7.1f}  {r['n_comparisons']:>3}  "
            f"{(r['upvotes'] or 0):>6}  {(r['num_comments'] or 0):>4}  {title}"
        )
    if len(pool) > top_n:
        print(f"   ...  ({len(pool) - top_n * 2} pieces hidden)  ...")
        for i, r in enumerate(pool[-top_n:], len(pool) - top_n + 1):
            title = (r["title"] or "")[:50]
            print(
                f"{i:>4}  {r['elo']:>7.1f}  {r['n_comparisons']:>3}  "
                f"{(r['upvotes'] or 0):>6}  {(r['num_comments'] or 0):>4}  {title}"
            )

    print("\n=== Correlation: ELO vs. Reddit engagement (Spearman rho) ===")
    elos = [r["elo"] for r in pool]
    for metric in ("upvotes", "num_comments", "upvote_ratio"):
        ys = [r[metric] for r in pool if r[metric] is not None]
        xs = [r["elo"] for r in pool if r[metric] is not None]
        rho = _spearman(xs, ys)
        rho_str = f"{rho:+.3f}" if rho is not None else "n/a"
        print(f"  ELO vs {metric:<13} rho = {rho_str}  (n={len(xs)})")

    if candidates:
        print("\n=== Candidates ===")
        for c in candidates:
            higher = sum(1 for p in pool if p["elo"] > c["elo"])
            pct = int(round(100.0 * (len(pool) - higher) / max(1, len(pool))))
            print(
                f"  {c['reddit_id']}  ELO {c['elo']:.1f}  "
                f"rank {higher + 1}/{len(pool) + 1}  (~{pct}th percentile)  "
                f"\"{(c['title'] or '')[:50]}\""
            )

    if excluded:
        print(f"\n=== Excluded ({len(excluded)} pieces flagged as not-art >= 2x) ===")
        for r in excluded[:15]:
            print(f"  {r['reddit_id']:>10}  flags={r['n_not_art_flags']}  "
                  f"upvotes={(r['upvotes'] or 0):>5}  {(r['title'] or '')[:60]}")
        if len(excluded) > 15:
            print(f"  ...and {len(excluded) - 15} more")

    if export_csv:
        out = Path(MODULE_DIR) / f"leaderboard_{subreddit}.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "rank", "reddit_id", "elo", "n_comparisons",
                    "upvotes", "num_comments", "upvote_ratio",
                    "title", "permalink", "image_url", "is_candidate",
                ],
            )
            writer.writeheader()
            for i, r in enumerate(rows, 1):
                writer.writerow(
                    {
                        "rank": i,
                        "reddit_id": r["reddit_id"],
                        "elo": round(r["elo"], 1),
                        "n_comparisons": r["n_comparisons"],
                        "upvotes": r["upvotes"],
                        "num_comments": r["num_comments"],
                        "upvote_ratio": r["upvote_ratio"],
                        "title": r["title"],
                        "permalink": r["permalink"],
                        "image_url": r["image_url"],
                        "is_candidate": r["is_candidate"],
                    }
                )
        print(f"\nExported {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--csv", action="store_true", help="Export leaderboard CSV")
    args = parser.parse_args()
    run(args.subreddit, args.top, args.csv)


if __name__ == "__main__":
    main()
