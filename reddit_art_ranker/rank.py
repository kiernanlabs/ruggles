"""Run an ELO ranking pass across all stored pieces in a subreddit.

Strategy: each pass partitions pieces into disjoint groups of GROUP_SIZE.
Early passes use random grouping for coverage; later passes bucket by current
rating so close-rated pieces face each other (that's where ELO learns most).
Groups within a pass run in parallel against the LLM.

Pieces flagged as "not art" by the LLM twice or more are permanently excluded
from future groups.

Usage:
    python -m reddit_art_ranker.rank
    python -m reddit_art_ranker.rank --passes 8 --workers 5 --subreddit Watercolor
"""

import argparse
import concurrent.futures
import random
import sys
import threading
import time

from . import db
from .config import (
    GROUP_SIZE,
    LLM_MODEL,
    RANKING_PASSES,
    RANKING_RANDOM_PASSES,
    SUBREDDIT,
)
from .elo import apply_group_ranking
from .llm import rank_group

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Pieces with >= this many not-art flags are excluded from future groups.
NOT_ART_EXCLUDE_AT = 2


def _build_groups(piece_ids_with_elo: list, group_size: int, by_rating: bool) -> list:
    if by_rating:
        ordered = sorted(piece_ids_with_elo, key=lambda x: x[1])
        ids = [pid for pid, _ in ordered]
    else:
        ids = [pid for pid, _ in piece_ids_with_elo]
        random.shuffle(ids)

    groups = []
    for i in range(0, len(ids), group_size):
        chunk = ids[i : i + group_size]
        if len(chunk) == group_size:
            groups.append(chunk)
    random.shuffle(groups)
    return groups


def _load_eligible(conn, subreddit: str) -> tuple[dict, dict]:
    """Eligible = not a candidate, not exceeded the not-art exclusion threshold."""
    rows = conn.execute(
        """
        SELECT p.reddit_id, p.image_url, r.elo, r.n_not_art_flags
        FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
        WHERE p.subreddit = ? AND p.is_candidate = 0
              AND r.n_not_art_flags < ?
        """,
        (subreddit, NOT_ART_EXCLUDE_AT),
    ).fetchall()
    ratings = {r["reddit_id"]: float(r["elo"]) for r in rows}
    urls = {r["reddit_id"]: r["image_url"] for r in rows}
    return ratings, urls


def _run_group(group: list, urls: dict, model: str) -> dict | None:
    """LLM call for one group. Returns a dict with rich result, or None on error."""
    image_urls = [urls[pid] for pid in group]
    try:
        result = rank_group(image_urls, model=model)
    except Exception as e:
        return {"error": str(e), "group": group}

    ranked_ids = [group[orig_idx] for orig_idx in result["order"]]
    not_art_ids = [group[orig_idx] for orig_idx in result["not_art_indices"]]
    per_piece = [
        {"piece_id": group[item["original_index"]], "rationale": item["rationale"]}
        for item in result["per_piece_rationales"]
    ]
    return {
        "group": group,
        "ranked_ids": ranked_ids,
        "not_art_ids": not_art_ids,
        "per_piece": per_piece,
        "rationale": result["rationale"],
    }


def run(subreddit: str, passes: int, model: str, workers: int,
        random_passes: int) -> None:
    with db.connect() as conn:
        ratings, urls = _load_eligible(conn, subreddit)

    if len(ratings) < GROUP_SIZE:
        print(f"Need at least {GROUP_SIZE} eligible pieces, found {len(ratings)}.")
        return

    n = len(ratings)
    total_groups = passes * (n // GROUP_SIZE)
    bucketed_passes = passes - random_passes
    print(f"Ranking {n} pieces in r/{subreddit} over {passes} passes "
          f"of {GROUP_SIZE}-way groups ({workers} workers).")
    print(f"  Phase 1: {random_passes} random passes (wide-range)")
    print(f"  Phase 2: {bucketed_passes} rating-bucketed passes (focused on close-rated)")
    print(f"Approx LLM calls: {total_groups}\n")

    db_lock = threading.Lock()

    for pass_idx in range(passes):
        # Reload eligible pieces at the start of each pass (a piece may have
        # been flagged out by an earlier pass).
        with db.connect() as conn:
            ratings, urls = _load_eligible(conn, subreddit)

        by_rating = pass_idx >= random_passes
        piece_list = [(pid, ratings[pid]) for pid in ratings]
        groups = _build_groups(piece_list, GROUP_SIZE, by_rating=by_rating)
        mode = "rating-bucketed" if by_rating else "random"
        print(f"Pass {pass_idx + 1}/{passes} ({mode}, {len(ratings)} eligible): "
              f"{len(groups)} groups")

        pass_start = time.time()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_run_group, g, urls, model): i for i, g in enumerate(groups)}
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                results.append((futures[fut], fut.result()))
                done += 1
                if done % 10 == 0 or done == len(groups):
                    print(f"  {done}/{len(groups)} groups returned "
                          f"({time.time() - pass_start:.0f}s elapsed)")

        # Apply all ELO updates using the pre-pass rating snapshot, then commit
        # accumulated deltas at the end of the pass.
        deltas = {pid: 0.0 for pid in ratings}
        comp_counts = {pid: 0 for pid in ratings}
        flag_increments = {}
        ok = err = flagged = 0
        for g_idx, res in sorted(results, key=lambda x: x[0]):
            if "error" in res:
                err += 1
                print(f"  group {g_idx + 1} FAILED: {res['error'][:200]}")
                continue
            ok += 1
            for pid in res["not_art_ids"]:
                flag_increments[pid] = flag_increments.get(pid, 0) + 1
                flagged += 1

            if len(res["ranked_ids"]) < 2:
                # Nothing meaningful to ELO-update; just log the comparison
                with db_lock, db.connect() as conn:
                    db.record_comparison(
                        conn, model=model, subreddit=subreddit,
                        piece_ids=res["group"], ranking=res["ranked_ids"],
                        rationale=res["rationale"], per_piece_rationales=res["per_piece"],
                    )
                continue

            new_ratings_partial, partial_counts = apply_group_ranking(
                ratings, res["ranked_ids"]
            )
            for pid in res["ranked_ids"]:
                deltas[pid] += new_ratings_partial[pid] - ratings[pid]
                comp_counts[pid] += partial_counts[pid]

            with db_lock, db.connect() as conn:
                db.record_comparison(
                    conn, model=model, subreddit=subreddit,
                    piece_ids=res["group"], ranking=res["ranked_ids"],
                    rationale=res["rationale"], per_piece_rationales=res["per_piece"],
                )

        # Persist ELO + flag updates
        with db.connect() as conn:
            for pid, d in deltas.items():
                if comp_counts[pid] > 0:
                    db.update_rating(conn, pid, ratings[pid] + d, comp_counts[pid])
            for pid in flag_increments:
                for _ in range(flag_increments[pid]):
                    db.increment_not_art_flag(conn, pid)

        elapsed = time.time() - pass_start
        print(f"  pass complete in {elapsed:.0f}s: {ok} ok, {err} err, "
              f"{flagged} not-art flags issued ({len(flag_increments)} unique pieces)\n")

    with db.connect() as conn:
        excluded = conn.execute(
            "SELECT COUNT(*) FROM ratings WHERE n_not_art_flags >= ?",
            (NOT_ART_EXCLUDE_AT,),
        ).fetchone()[0]
    print(f"Done. {excluded} pieces excluded for repeated not-art flags. "
          "Run report or analyze to see results.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--passes", type=int, default=RANKING_PASSES)
    parser.add_argument("--random-passes", type=int, default=RANKING_RANDOM_PASSES,
                        help="First N passes use random grouping (wide-range); "
                             "remaining passes use rating-bucketed (focused)")
    parser.add_argument("--model", default=LLM_MODEL)
    parser.add_argument("--workers", type=int, default=5,
                        help="Concurrent LLM calls per pass")
    args = parser.parse_args()
    run(args.subreddit, args.passes, args.model, args.workers, args.random_passes)


if __name__ == "__main__":
    main()
