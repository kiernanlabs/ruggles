"""Cloud candidate-insertion algorithm.

Same two-phase design as the original module's insert() (random ballpark
rounds, then focused rounds against close-ELO anchors), but:

  * the anchor pool is loaded once into memory from DynamoDB (it's ~100 small
    items) instead of re-queried from SQLite each round;
  * only the candidate's ELO mutates — anchors stay frozen, exactly as before,
    so a consumer submission never perturbs the published leaderboard;
  * a `progress_cb` is invoked after each round so the worker can stream
    round/ELO/percentile into the job record for the frontend to poll.

Returns the same `result` dict the report renderer expects.
"""

import random
import time

from shared.config import (
    ELO_INITIAL,
    GROUP_SIZE,
    INSERTION_FOCUSED_WINDOW,
    INSERTION_RANDOM_GROUPS,
)
from shared.elo import apply_group_ranking
from shared.llm import rank_group


def _pick_random_anchors(pool: list, n: int) -> list:
    return list(pool) if len(pool) <= n else random.sample(pool, n)


def _pick_focused_anchors(pool: list, candidate_elo: float, n: int,
                          window: int) -> list:
    if len(pool) <= n:
        return list(pool)
    by_distance = sorted(pool, key=lambda r: abs(float(r["elo"]) - candidate_elo))
    neighborhood = by_distance[:max(window, n)]
    return random.sample(neighborhood, n)


def _pctl(elo: float, pool_elos: list) -> int:
    if not pool_elos:
        return 0
    below = sum(1 for e in pool_elos if e < elo)
    return int(round(100.0 * below / len(pool_elos)))


def insert(
    candidate_id: str,
    image_url: str,
    title: str,
    pool: list,
    jury_subject: str,
    model: str,
    n_groups: int,
    random_groups: int = INSERTION_RANDOM_GROUPS,
    focused_window: int = INSERTION_FOCUSED_WINDOW,
    progress_cb=None,
) -> dict:
    """`pool` is the list of eligible anchor dicts from ddb.load_eligible_pool.
    `image_url` is a URL the LLM can fetch (presigned S3 GET or a data URI)."""
    if len(pool) < GROUP_SIZE - 1:
        raise RuntimeError(
            f"Pool too small: need >= {GROUP_SIZE - 1} anchors, got {len(pool)}."
        )

    pool_elos_static = [float(p["elo"]) for p in pool]
    candidate_elo = ELO_INITIAL
    rounds = []

    for g_idx in range(n_groups):
        if g_idx < random_groups:
            phase = "random"
            anchors = _pick_random_anchors(pool, GROUP_SIZE - 1)
        else:
            phase = "focused"
            anchors = _pick_focused_anchors(
                pool, candidate_elo, GROUP_SIZE - 1, focused_window
            )
        random.shuffle(anchors)

        elo_sorted = sorted(pool, key=lambda r: -float(r["elo"]))
        rank_by_id = {r["piece_id"]: i + 1 for i, r in enumerate(elo_sorted)}

        group_ids = [candidate_id] + [a["piece_id"] for a in anchors]
        image_urls = [image_url] + [a["image_url"] for a in anchors]
        local_ratings = {candidate_id: candidate_elo}
        for a in anchors:
            local_ratings[a["piece_id"]] = float(a["elo"])

        try:
            result = rank_group(image_urls, model=model, jury_subject=jury_subject)
        except Exception as e:
            rounds.append({"round": g_idx + 1, "phase": phase, "error": str(e),
                           "anchors": [], "candidate": {}})
            continue

        ranked_ids = [group_ids[i] for i in result["order"]]
        not_art_ids = [group_ids[i] for i in result["not_art_indices"]]
        per_piece = [
            {"piece_id": group_ids[item["original_index"]], "rationale": item["rationale"]}
            for item in result["per_piece_rationales"]
        ]
        rat_by_id = {x["piece_id"]: x["rationale"] for x in per_piece}
        frozen = {a["piece_id"] for a in anchors}

        round_info = {
            "round": g_idx + 1,
            "phase": phase,
            "anchors": [
                {
                    "reddit_id": a["piece_id"],
                    "title": a.get("title"),
                    "permalink": a.get("permalink"),
                    "image_url": a.get("image_url"),
                    "anchor_pre_elo": float(a["elo"]),
                    "anchor_pre_rank": rank_by_id.get(a["piece_id"]),
                    "rationale_this_round": rat_by_id.get(a["piece_id"]),
                    "placed_position": (ranked_ids.index(a["piece_id"]) + 1
                                        if a["piece_id"] in ranked_ids else None),
                    "flagged_not_art": a["piece_id"] in not_art_ids,
                }
                for a in anchors
            ],
            "candidate": {
                "rationale_this_round": rat_by_id.get(candidate_id),
                "placed_position": (ranked_ids.index(candidate_id) + 1
                                    if candidate_id in ranked_ids else None),
                "flagged_not_art": candidate_id in not_art_ids,
            },
            "overall_rationale": result["rationale"],
        }

        if candidate_id not in not_art_ids and candidate_id in ranked_ids:
            new_ratings, _ = apply_group_ranking(
                local_ratings, ranked_ids, frozen_ids=frozen
            )
            candidate_elo = new_ratings[candidate_id]
            round_info["candidate_elo_after"] = candidate_elo

        rounds.append(round_info)
        if progress_cb:
            progress_cb(g_idx + 1, n_groups, candidate_elo,
                        _pctl(candidate_elo, pool_elos_static))
        time.sleep(0.2)

    percentile = _pctl(candidate_elo, pool_elos_static)
    final_rank = sum(1 for e in pool_elos_static if e > candidate_elo) + 1
    return {
        "candidate_id": candidate_id,
        "title": title,
        "elo": candidate_elo,
        "rank": final_rank,
        "of": len(pool_elos_static) + 1,
        "percentile": percentile,
        "rounds": rounds,
        "pool_elos": pool_elos_static,
    }
