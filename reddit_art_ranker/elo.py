"""ELO update math, parameterized for group rankings."""

from .config import ELO_K


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def apply_group_ranking(
    ratings: dict,
    ranked_piece_ids: list,
    frozen_ids: set | None = None,
    k: float = ELO_K,
) -> dict:
    """Decompose a group ranking into pairwise outcomes and apply ELO updates.

    All updates use the pre-group ratings (no sequential drift within the group).
    Frozen pieces keep their rating but still contribute to others' updates.
    Returns the new ratings dict and per-piece comparison counts.
    """
    frozen_ids = frozen_ids or set()
    pre = {pid: ratings[pid] for pid in ranked_piece_ids}
    deltas = {pid: 0.0 for pid in ranked_piece_ids}
    comparisons = {pid: 0 for pid in ranked_piece_ids}

    for i, winner in enumerate(ranked_piece_ids):
        for loser in ranked_piece_ids[i + 1 :]:
            ew = expected_score(pre[winner], pre[loser])
            el = 1.0 - ew
            deltas[winner] += k * (1.0 - ew)
            deltas[loser] += k * (0.0 - el)
            comparisons[winner] += 1
            comparisons[loser] += 1

    new_ratings = {}
    for pid in ranked_piece_ids:
        if pid in frozen_ids:
            new_ratings[pid] = pre[pid]
        else:
            new_ratings[pid] = pre[pid] + deltas[pid]
    return new_ratings, comparisons
