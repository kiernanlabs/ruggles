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


def _build_extend_groups(focus_ids: list, ratings: dict, group_size: int,
                         by_rating: bool, focus_per_group: int) -> list:
    """Groups for EXTEND mode: each group is up to `focus_per_group` new (focus)
    pieces plus anchor pieces drawn from the already-ranked pool, filling to
    `group_size`. Anchors calibrate the new pieces to the global ELO scale and
    are NOT frozen (they keep fluctuating). Returns list of piece-id groups."""
    focus = [(pid, ratings[pid]) for pid in focus_ids if pid in ratings]
    anchors = [(pid, ratings[pid]) for pid in ratings if pid not in set(focus_ids)]
    if not anchors:
        raise RuntimeError("EXTEND needs an existing ranked pool (no anchors found).")

    if by_rating:
        focus.sort(key=lambda x: x[1])
    else:
        random.shuffle(focus)

    groups = []
    for i in range(0, len(focus), focus_per_group):
        chunk = focus[i:i + focus_per_group]
        chunk_ids = [pid for pid, _ in chunk]
        n_anchor = max(1, group_size - len(chunk_ids))
        if by_rating:
            mean_elo = sum(e for _, e in chunk) / len(chunk)
            nearest = sorted(anchors, key=lambda a: abs(a[1] - mean_elo))
            window = nearest[:max(n_anchor, min(40, len(nearest)))]
            picks = random.sample(window, min(n_anchor, len(window)))
        else:
            picks = random.sample(anchors, min(n_anchor, len(anchors)))
        groups.append(chunk_ids + [pid for pid, _ in picks])
    random.shuffle(groups)
    return groups


def _load_focus_ids(conn, subreddit: str) -> list:
    """Eligible, never-yet-compared pieces — the freshly-fetched set to rank."""
    rows = conn.execute(
        """
        SELECT p.reddit_id FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
        WHERE p.subreddit = ? AND p.is_candidate = 0
              AND r.n_not_art_flags < ? AND r.n_comparisons = 0
        """,
        (subreddit, NOT_ART_EXCLUDE_AT),
    ).fetchall()
    return [r["reddit_id"] for r in rows]


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


def _run_group(group: list, urls: dict, rank_fn) -> dict | None:
    """LLM call for one group. Returns a dict with rich result, or None on error.
    `rank_fn(image_urls)` is a pre-bound ranker (local or cloud/pool-aware)."""
    image_urls = [urls[pid] for pid in group]
    try:
        result = rank_fn(image_urls)
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


def _resolve_rank_fn(model: str, pool_id: str | None):
    """Return a `rank_fn(image_urls)`. With --pool, route through the cloud's
    pool-aware jury (parameterized framing/criteria from shared/pools.py) so the
    initial ranking matches the production evaluation; otherwise use the local
    (watercolor) prompt."""
    if not pool_id:
        return lambda urls: rank_group(urls, model=model)

    import sys as _sys
    from pathlib import Path as _Path
    cloud_dir = _Path(__file__).resolve().parent.parent / "reddit_art_ranker_cloud"
    if str(cloud_dir) not in _sys.path:
        _sys.path.insert(0, str(cloud_dir))
    from shared.llm import rank_group as cloud_rank_group
    from shared.pools import get_pool
    pool = get_pool(pool_id)
    print(f"Using cloud pool-aware jury for '{pool_id}' "
          f"(jury_subject={pool.jury_subject!r}).")
    return lambda urls: cloud_rank_group(
        urls, model=model, jury_subject=pool.jury_subject,
        framing=pool.framing, criteria=pool.criteria)


def run(subreddit: str, passes: int, model: str, workers: int,
        random_passes: int, pool_id: str | None = None,
        extend: bool = False, focus_per_group: int = 4) -> None:
    rank_fn = _resolve_rank_fn(model, pool_id)
    with db.connect() as conn:
        ratings, urls = _load_eligible(conn, subreddit)
        focus_ids = _load_focus_ids(conn, subreddit) if extend else None

    if len(ratings) < GROUP_SIZE:
        print(f"Need at least {GROUP_SIZE} eligible pieces, found {len(ratings)}.")
        return

    n = len(ratings)
    bucketed_passes = passes - random_passes
    if extend:
        if not focus_ids:
            print("EXTEND: no new (0-comparison) pieces to rank. Nothing to do.")
            return
        groups_per_pass = -(-len(focus_ids) // focus_per_group)  # ceil
        total_groups = passes * groups_per_pass
        print(f"EXTENDING r/{subreddit}: ranking {len(focus_ids)} NEW pieces against "
              f"{n - len(focus_ids)} existing anchors, over {passes} passes "
              f"({focus_per_group} new + anchors per {GROUP_SIZE}-group, {workers} workers).")
        print("  Existing pieces keep their ELO/comparisons and continue to fluctuate.")
    else:
        total_groups = passes * (n // GROUP_SIZE)
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
        if extend:
            groups = _build_extend_groups(focus_ids, ratings, GROUP_SIZE,
                                          by_rating, focus_per_group)
            n_focus_live = sum(1 for pid in focus_ids if pid in ratings)
            scope = f"{n_focus_live} new + anchors"
        else:
            piece_list = [(pid, ratings[pid]) for pid in ratings]
            groups = _build_groups(piece_list, GROUP_SIZE, by_rating=by_rating)
            scope = f"{len(ratings)} eligible"
        mode = "rating-bucketed" if by_rating else "random"
        print(f"Pass {pass_idx + 1}/{passes} ({mode}, {scope}): {len(groups)} groups")

        pass_start = time.time()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_run_group, g, urls, rank_fn): i for i, g in enumerate(groups)}
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
    parser.add_argument("--pool", default=None,
                        help="Cloud pool id (shared/pools.py). When set, ranks with "
                             "that pool's framing/criteria via the cloud jury.")
    parser.add_argument("--extend", action="store_true",
                        help="Incremental mode: rank only NEW (0-comparison) pieces, "
                             "drawing existing pieces in as (unfrozen) anchors. "
                             "Existing ELO/comparisons are preserved and keep moving.")
    parser.add_argument("--focus-per-group", type=int, default=4,
                        help="New pieces per group in --extend mode; the rest are anchors "
                             "(default 4 new + 1 anchor in a 5-group).")
    args = parser.parse_args()
    run(args.subreddit, args.passes, args.model, args.workers, args.random_passes,
        pool_id=args.pool, extend=args.extend, focus_per_group=args.focus_per_group)


if __name__ == "__main__":
    main()
