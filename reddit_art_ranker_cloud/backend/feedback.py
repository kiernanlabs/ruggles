"""Post-insertion enrichment: synthesize artist-facing feedback and bucket the
anchors a candidate faced into better / similar / worse comparison tiers.

Ported and trimmed from the original module's insert.py report code. The output
is plain JSON the frontend renders — no server-side HTML.
"""

from shared.llm import _client


def synthesize_feedback(rationales: list, model: str) -> str:
    """One LLM call: fold the per-round rationales into 2-3 plain sentences
    for the artist. Returns "" if it fails (non-fatal)."""
    rationales = [r for r in rationales if r]
    if not rationales:
        return ""
    try:
        client = _client()
        bullets = "\n".join(f"- {r}" for r in rationales)
        prompt = (
            f"Below are independent critique observations of a single art piece, "
            f"made across multiple evaluation rounds:\n\n{bullets}\n\n"
            "Write 2-3 plain-English sentences for the artist who made this piece. "
            "Synthesize the recurring themes — what's working and what could be "
            "improved. Speak directly to the artist. Be honest but encouraging. "
            "Don't reference 'rounds', 'evaluations', or 'observations'."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return ""


def _pctl_of_elo(elo: float, pool_elos: list) -> int:
    if not pool_elos:
        return 0
    below = sum(1 for e in pool_elos if e < elo)
    return int(round(100.0 * below / len(pool_elos)))


def candidate_comparisons(result: dict, pool_elos: list, candidate_id: str,
                          candidate_image_url: str, candidate_title: str,
                          model: str) -> list:
    """Turn each insertion round into a self-contained comparison record, shaped
    like the published pool comparisons the Scoreboard Explorer renders: the
    candidate + the anchors it faced, each with image, rank-in-group, the round's
    rationale, and its standing in the overall pool. Error rounds are skipped.

    Self-contained (members embedded) because the candidate has no PIECE row to
    join against, and so the detail endpoint is a single Get with no fan-out.
    """
    out = []
    for rd in result.get("rounds", []):
        if rd.get("error"):
            continue
        cand = rd.get("candidate", {})
        cand_elo_after = rd.get("candidate_elo_after")
        members = [{
            "piece_id": candidate_id,
            "is_candidate": True,
            "title": candidate_title or "Your piece",
            "author": None,
            "image_url": candidate_image_url,
            "permalink": None,
            "rank_in_set": cand.get("placed_position"),
            "flagged_not_art": bool(cand.get("flagged_not_art")),
            "overall_percentile": (_pctl_of_elo(cand_elo_after, pool_elos)
                                   if cand_elo_after is not None else None),
            "overall_rank": None,
            "rationale": cand.get("rationale_this_round") or "",
        }]
        for a in rd.get("anchors", []):
            members.append({
                "piece_id": a.get("reddit_id"),
                "is_candidate": False,
                "title": a.get("title") or a.get("reddit_id"),
                "author": a.get("author"),
                "image_url": a.get("image_url"),
                "permalink": a.get("permalink"),
                "rank_in_set": a.get("placed_position"),
                "flagged_not_art": bool(a.get("flagged_not_art")),
                "overall_percentile": _pctl_of_elo(float(a.get("anchor_pre_elo", 0)),
                                                   pool_elos),
                "overall_rank": a.get("anchor_pre_rank"),
                "rationale": a.get("rationale_this_round") or "",
            })
        # Group ranking order: ranked pieces first, not-art / unranked last.
        members.sort(key=lambda m: (m["rank_in_set"] is None, m["rank_in_set"] or 0))
        pcts = [m["overall_percentile"] for m in members
                if m["overall_percentile"] is not None]
        out.append({
            "comparison_id": rd["round"],
            "phase": rd.get("phase"),
            "size": len(members),
            "model": model,
            "rationale": rd.get("overall_rationale") or "",
            "candidate_rank": cand.get("placed_position"),
            "candidate_flagged_not_art": bool(cand.get("flagged_not_art")),
            "avg_pctl": round(sum(pcts) / len(pcts), 1) if pcts else None,
            "min_pctl": min(pcts) if pcts else None,
            "max_pctl": max(pcts) if pcts else None,
            "members": members,
        })
    return out


def verdict_headline(pctl: int) -> dict:
    if pctl >= 90:
        h, s = ("Your piece is in the top tier.",
                "Stronger craft than the vast majority of submissions.")
    elif pctl >= 75:
        h, s = ("Your piece is well above average.",
                "More resolved than most of the pool.")
    elif pctl >= 55:
        h, s = ("Your piece is above the median.",
                "Better than more than half of the submissions.")
    elif pctl >= 40:
        h, s = ("Your piece sits around the middle.",
                "Comparable craft to a typical submission.")
    elif pctl >= 20:
        h, s = ("Your piece is below average.",
                "Some clear strengths but room to grow.")
    else:
        h, s = ("Your piece is in the lower tier.",
                "Lots of growth opportunity — see the comparisons.")
    return {"headline": h, "subline": s}
