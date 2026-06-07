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


def comparison_tiers(result: dict, pool_elos: list) -> dict:
    """Bucket the unique anchors the candidate faced into much_better / similar /
    much_worse (<=3 each), each annotated with its percentile + best rationale."""
    cand_pctl = result["percentile"]
    seen = {}
    for rd in result["rounds"]:
        for a in rd.get("anchors", []):
            if a.get("flagged_not_art"):
                continue
            aid = a["reddit_id"]
            if aid not in seen:
                seen[aid] = {**a, "round": rd["round"], "phase": rd["phase"]}
    anchors = list(seen.values())
    for a in anchors:
        a["percentile"] = _pctl_of_elo(a["anchor_pre_elo"], pool_elos)

    much_better = sorted(
        [a for a in anchors if a["percentile"] >= min(85, cand_pctl + 30)],
        key=lambda a: -a["percentile"],
    )[:3]
    much_worse = sorted(
        [a for a in anchors if a["percentile"] <= max(15, cand_pctl - 30)],
        key=lambda a: a["percentile"],
    )[:3]
    chosen = {x["reddit_id"] for x in much_better + much_worse}
    similar = sorted(
        [a for a in anchors
         if a["reddit_id"] not in chosen and abs(a["percentile"] - cand_pctl) <= 15],
        key=lambda a: abs(a["percentile"] - cand_pctl),
    )[:3]

    def _slim(a):
        return {
            "title": a.get("title") or a["reddit_id"],
            "permalink": a.get("permalink"),
            "image_url": a.get("image_url"),
            "percentile": a["percentile"],
            "rationale": a.get("rationale_this_round") or "",
        }

    return {
        "much_better": [_slim(a) for a in much_better],
        "similar": [_slim(a) for a in similar],
        "much_worse": [_slim(a) for a in much_worse],
    }


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
