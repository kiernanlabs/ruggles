"""Insert a new candidate piece into an existing ranked pool.

Two-phase, mirroring the initial rank flow but per-piece:

  Phase 1 (random rounds): the candidate competes against 4 random anchors
  drawn fresh from the eligible pool each round. Gives a wide-range ballpark
  ELO without needing a prior estimate.

  Phase 2 (focused rounds): the candidate competes against 4 anchors sampled
  from a window of the closest-ELO pieces around the candidate's current ELO.
  The window shifts each round as the candidate's ELO updates, so later rounds
  refine within-cohort placement.

Only the candidate's ELO updates; anchor ratings stay frozen. Excluded
(not-art-flagged) pieces are never used as anchors.

Usage:
    python -m reddit_art_ranker.insert --image-url https://... --title "My piece"
    python -m reddit_art_ranker.insert --image-path ./mypainting.jpg --title "My piece"
    python -m reddit_art_ranker.insert --image-path ./x.jpg --title "x" --groups 8 --random-groups 4
"""

import argparse
import base64
import datetime as dt
import html as html_lib
import json
import mimetypes
import random
import sys
import time
import uuid
import webbrowser
from pathlib import Path

from . import db
from .config import (
    ELO_INITIAL,
    GROUP_SIZE,
    INSERTION_GROUPS,
    INSERTION_RANDOM_GROUPS,
    INSERTION_FOCUSED_WINDOW,
    LLM_MODEL,
    MODULE_DIR,
    SUBREDDIT,
)
from .elo import apply_group_ranking
from .llm import rank_group

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _path_to_data_uri(path: Path) -> str:
    """Read a local image file and return a data: URI suitable for the OpenAI vision API."""
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        # Common fallbacks since iPhone exports are .JPEG (uppercase)
        ext = path.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _ranked_pool(conn, subreddit: str) -> list:
    """Eligible pool (non-candidate, fewer than 2 not-art flags), ELO desc."""
    return conn.execute(
        """
        SELECT p.reddit_id, p.image_url, p.title, p.permalink, r.elo
        FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
        WHERE p.subreddit = ? AND p.is_candidate = 0
              AND r.n_not_art_flags < 2
        ORDER BY r.elo DESC
        """,
        (subreddit,),
    ).fetchall()


def _pick_random_anchors(pool: list, n_anchors: int) -> list:
    """N fresh random anchors from the pool (no replacement within a round)."""
    if len(pool) <= n_anchors:
        return list(pool)
    return random.sample(pool, n_anchors)


def _pick_focused_anchors(pool: list, candidate_elo: float, n_anchors: int,
                          window: int = INSERTION_FOCUSED_WINDOW) -> list:
    """N anchors sampled from the `window` pieces closest by ELO to the
    candidate. Each focused round draws a fresh random sample from this
    neighborhood, so the same comparison isn't repeated across rounds."""
    if len(pool) <= n_anchors:
        return list(pool)
    by_distance = sorted(pool, key=lambda r: abs(float(r["elo"]) - candidate_elo))
    neighborhood = by_distance[:max(window, n_anchors)]
    return random.sample(neighborhood, n_anchors)


def _percentile_rank(elo: float, ratings_sorted_desc: list) -> int:
    below = sum(1 for r in ratings_sorted_desc if r < elo)
    return int(round(100.0 * below / max(1, len(ratings_sorted_desc))))


def insert(
    image_url: str,
    subreddit: str,
    title: str,
    n_groups: int,
    model: str,
    candidate_id: str | None = None,
    random_groups: int = INSERTION_RANDOM_GROUPS,
    focused_window: int = INSERTION_FOCUSED_WINDOW,
) -> dict:
    """Insert a candidate into the ranked pool. `image_url` may be either a
    remote URL or a base64 data URI.

    Phase 1: first `random_groups` rounds draw 4 fresh random anchors per round.
    Phase 2: remaining rounds draw 4 random anchors from the `focused_window`
    pieces closest to the candidate's current ELO."""
    candidate_id = candidate_id or f"cand_{uuid.uuid4().hex[:10]}"

    with db.connect() as conn:
        pool = _ranked_pool(conn, subreddit)
        if len(pool) < GROUP_SIZE - 1:
            raise RuntimeError(
                f"Need at least {GROUP_SIZE - 1} ranked pieces in r/{subreddit}; "
                f"found {len(pool)}. Run fetch + rank first."
            )

        db.upsert_piece(
            conn,
            {
                "reddit_id": candidate_id,
                "subreddit": subreddit,
                "title": title,
                "author": None,
                "permalink": None,
                "image_url": image_url,
                "upvotes": None,
                "num_comments": None,
                "upvote_ratio": None,
                "awards": None,
                "created_utc": None,
            },
            is_candidate=True,
        )

        ratings_row = conn.execute(
            "SELECT elo FROM ratings WHERE reddit_id = ?", (candidate_id,)
        ).fetchone()
        candidate_elo = float(ratings_row["elo"])

    focused_groups = max(0, n_groups - random_groups)
    print(f"Inserting candidate '{title}' (id={candidate_id}) into r/{subreddit} "
          f"({len(pool)} eligible pieces).")
    print(f"  Phase 1: {random_groups} random rounds (wide-range, fresh anchors each)")
    print(f"  Phase 2: {focused_groups} focused rounds (anchors from "
          f"{focused_window}-piece window around candidate ELO)")
    print(f"  Each round: candidate + {GROUP_SIZE - 1} anchors\n")

    rounds = []  # rich per-round detail for caller / reporting

    for g_idx in range(n_groups):
        with db.connect() as conn:
            pool = _ranked_pool(conn, subreddit)

        if g_idx < random_groups:
            phase = "random"
            anchors = _pick_random_anchors(pool, GROUP_SIZE - 1)
        else:
            phase = "focused"
            anchors = _pick_focused_anchors(
                pool, candidate_elo, GROUP_SIZE - 1, window=focused_window,
            )
        random.shuffle(anchors)

        # Tag anchors with their pre-insertion rank position (1-based, by ELO)
        elo_sorted = sorted(pool, key=lambda r: -float(r["elo"]))
        rank_by_id = {r["reddit_id"]: i + 1 for i, r in enumerate(elo_sorted)}

        group_ids = [candidate_id] + [a["reddit_id"] for a in anchors]
        image_urls = [image_url] + [a["image_url"] for a in anchors]
        local_ratings = {candidate_id: candidate_elo}
        for a in anchors:
            local_ratings[a["reddit_id"]] = float(a["elo"])

        try:
            result = rank_group(image_urls, model=model)
        except Exception as e:
            print(f"  round {g_idx + 1}/{n_groups} FAILED: {e}")
            continue

        ranked_ids = [group_ids[orig_idx] for orig_idx in result["order"]]
        not_art_ids = [group_ids[orig_idx] for orig_idx in result["not_art_indices"]]
        per_piece = [
            {"piece_id": group_ids[item["original_index"]], "rationale": item["rationale"]}
            for item in result["per_piece_rationales"]
        ]
        rat_by_id = {x["piece_id"]: x["rationale"] for x in per_piece}
        frozen = {a["reddit_id"] for a in anchors}

        with db.connect() as conn:
            db.record_comparison(
                conn, model=model, subreddit=subreddit,
                piece_ids=group_ids, ranking=ranked_ids,
                rationale=result["rationale"],
                candidate_id=candidate_id,
                per_piece_rationales=per_piece,
            )
            for pid in not_art_ids:
                db.increment_not_art_flag(conn, pid)

        # Per-round rich detail
        round_info = {
            "round": g_idx + 1,
            "phase": phase,
            "anchors": [
                {
                    "reddit_id": a["reddit_id"],
                    "title": a["title"],
                    "permalink": a["permalink"],
                    "image_url": a["image_url"],
                    "anchor_pre_elo": float(a["elo"]),
                    "anchor_pre_rank": rank_by_id.get(a["reddit_id"]),
                    "rationale_this_round": rat_by_id.get(a["reddit_id"]),
                    "placed_position": (ranked_ids.index(a["reddit_id"]) + 1
                                        if a["reddit_id"] in ranked_ids else None),
                    "flagged_not_art": a["reddit_id"] in not_art_ids,
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

        if candidate_id in not_art_ids:
            print(f"  round {g_idx + 1}/{n_groups} [{phase}]: LLM flagged candidate "
                  f"as NOT ART (no ELO update)")
        elif candidate_id in ranked_ids:
            new_ratings, comp_counts = apply_group_ranking(
                local_ratings, ranked_ids, frozen_ids=frozen
            )
            candidate_elo = new_ratings[candidate_id]
            with db.connect() as conn:
                db.update_rating(conn, candidate_id, candidate_elo, comp_counts[candidate_id])
            cand_position = ranked_ids.index(candidate_id) + 1
            print(f"  round {g_idx + 1}/{n_groups} [{phase}]: candidate finished "
                  f"{cand_position}/{len(ranked_ids)} -> ELO {candidate_elo:.1f}")
            round_info["candidate_elo_after"] = candidate_elo

        rounds.append(round_info)
        time.sleep(0.4)

    with db.connect() as conn:
        all_ratings = [float(r["elo"]) for r in _ranked_pool(conn, subreddit)]
    percentile = _percentile_rank(candidate_elo, all_ratings)
    final_rank = sum(1 for r in all_ratings if r > candidate_elo) + 1
    print(f"\n  → Final ELO: {candidate_elo:.1f}  "
          f"(rank {final_rank}/{len(all_ratings) + 1}, ~{percentile}th percentile)")

    return {
        "candidate_id": candidate_id,
        "title": title,
        "elo": candidate_elo,
        "rank": final_rank,
        "of": len(all_ratings) + 1,
        "percentile": percentile,
        "rounds": rounds,
    }


_REPORT_CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 980px;
       margin: 32px auto; padding: 0 20px; color: #222; line-height: 1.5; }
h1 { font-size: 28px; margin: 0 0 6px; }
h2 { margin: 36px 0 12px; font-size: 20px; }
.meta { color: #888; font-size: 12px; margin-bottom: 28px; }

/* Hero */
.hero { display: grid; grid-template-columns: 360px 1fr; gap: 28px;
        margin-bottom: 28px; align-items: start; }
.hero img.candidate { width: 360px; max-height: 480px; object-fit: contain;
                      border-radius: 6px; background: #f5f5f5; display: block;
                      box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.headline { font-size: 18px; color: #444; margin: 4px 0 14px; }
.big-number { font-size: 72px; font-weight: 700; line-height: 1;
              color: #0366d6; font-variant-numeric: tabular-nums; }
.big-number sup { font-size: 28px; font-weight: 500; margin-left: 2px;
                  vertical-align: top; top: 12px; position: relative; }
.subline { color: #666; font-size: 14px; margin-bottom: 18px; }
.pctile-bar { margin-top: 18px; height: 30px; background: #f0f0f0;
              border-radius: 15px; position: relative;
              box-shadow: inset 0 1px 3px rgba(0,0,0,0.05); }
.pctile-bar .gradient { position: absolute; inset: 0; border-radius: 15px;
                        background: linear-gradient(90deg, #c0392b, #f39c12, #f1c40f, #2ecc71);
                        opacity: 0.18; }
.pctile-bar .marker { position: absolute; top: -8px; bottom: -8px; width: 6px;
                      border-radius: 3px; background: #222;
                      box-shadow: 0 2px 6px rgba(0,0,0,0.3); }
.pctile-bar .scale-label { position: absolute; bottom: -18px; font-size: 11px;
                           color: #888; transform: translateX(-50%); }

/* Feedback */
.feedback {
    background: #f6f8fa; border-left: 4px solid #0366d6; border-radius: 4px;
    padding: 18px 22px; margin: 16px 0 24px; font-size: 15px; line-height: 1.6;
}
.feedback .label { font-size: 11px; color: #666; text-transform: uppercase;
                   letter-spacing: 0.06em; margin-bottom: 6px; }

/* Tier sections */
.tier-section { margin-bottom: 36px; }
.tier-section h2 { display: flex; align-items: baseline; gap: 10px; }
.tier-section h2 .count { font-size: 13px; color: #888; font-weight: 400; }
.tier-card-grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
.tier-card { display: grid; grid-template-columns: 140px 1fr; gap: 16px;
             border: 1px solid #eee; border-radius: 6px;
             padding: 14px; align-items: start; }
.tier-card img.thumb { width: 140px; height: 140px; object-fit: cover;
                       border-radius: 4px; background: #eee; display: block; }
.tier-card .title { font-weight: 600; font-size: 15px; margin-bottom: 6px; }
.tier-card .title a { color: #222; text-decoration: none; }
.tier-card .title a:hover { color: #0366d6; }
.tier-card .pctile-line { font-size: 13px; color: #666; margin-bottom: 8px;
                          display: flex; align-items: center; gap: 10px; }
.tier-card .pctile-pill { background: #f0f0f0; padding: 2px 10px;
                          border-radius: 10px; font-weight: 600;
                          font-variant-numeric: tabular-nums; }
.tier-card .pctile-pill.you { background: #fff8e1; color: #b08000; }
.tier-card .pctile-pill.much-better { background: #d4edda; color: #155724; }
.tier-card .pctile-pill.bit-better { background: #e8f5e9; color: #1b5e20; }
.tier-card .pctile-pill.similar { background: #fff3cd; color: #856404; }
.tier-card .pctile-pill.much-worse { background: #f8d7da; color: #721c24; }
.tier-card .rationale { font-size: 13px; color: #555; line-height: 1.5;
                        font-style: italic; }

/* Details / collapsible */
details.tech-detail { margin-top: 40px; border-top: 1px solid #eee;
                      padding-top: 20px; }
details.tech-detail summary { cursor: pointer; font-size: 14px; color: #666;
                              padding: 6px 0; user-select: none; }
details.tech-detail summary:hover { color: #0366d6; }
.round-card { border: 1px solid #eee; border-radius: 6px;
              padding: 14px 18px; margin-bottom: 14px; font-size: 13px; }
.round-head { display: flex; justify-content: space-between; align-items: baseline;
              margin-bottom: 8px; }
.round-head h3 { margin: 0; font-size: 14px; }
.phase-pill { font-size: 10px; padding: 2px 7px; border-radius: 10px;
              color: white; margin-left: 8px; font-weight: 600; }
.phase-random { background: #8e44ad; }
.phase-focused { background: #16a085; }
table.group { width: 100%; border-collapse: collapse; font-size: 12px;
              margin-top: 8px; }
table.group td { padding: 6px 8px; border-bottom: 1px solid #f5f5f5;
                 vertical-align: top; }
table.group td:first-child { width: 36px; text-align: center; }
table.group td.thumb-cell { width: 70px; }
table.group img.mini { width: 60px; height: 60px; object-fit: cover;
                       border-radius: 3px; display: block; }
table.group tr.candidate-row td { background: #fff8e1; }
.rank-pill { display: inline-block; min-width: 24px; padding: 3px 7px;
             border-radius: 10px; background: #0366d6; color: white;
             font-weight: 600; text-align: center; font-size: 12px; }
.rank-pill.flagged { background: #c0392b; }
.overall-rat { font-size: 11px; color: #777; line-height: 1.4;
               background: #fafafa; padding: 6px 10px; border-radius: 4px;
               margin-top: 8px; font-style: italic; }
"""


def _make_elo_svg(history: list[tuple[int, float]], phase_split: int) -> str:
    """Simple SVG line chart of candidate ELO across rounds.

    history is [(round_idx, elo), ...] starting with (0, ELO_INITIAL).
    phase_split is the round number at which random→focused transitions
    (drawn as a vertical reference line)."""
    if len(history) < 2:
        return ""
    w, h = 800, 120
    pad_l, pad_r, pad_t, pad_b = 50, 20, 14, 24
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    xs = [pt[0] for pt in history]
    ys = [pt[1] for pt in history]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    y_pad = max(20, (y_max - y_min) * 0.15)
    y_min, y_max = y_min - y_pad, y_max + y_pad

    def sx(x): return pad_l + (x - x_min) / max(1, x_max - x_min) * plot_w
    def sy(y): return pad_t + (1 - (y - y_min) / max(1, y_max - y_min)) * plot_h

    path = " ".join(
        f"{'M' if i == 0 else 'L'} {sx(x):.1f} {sy(y):.1f}"
        for i, (x, y) in enumerate(history)
    )
    points = "".join(
        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="#0366d6"/>'
        for x, y in history
    )
    labels = "".join(
        f'<text x="{sx(x):.1f}" y="{sy(y) - 8:.1f}" font-size="10" '
        f'text-anchor="middle" fill="#444">{y:.0f}</text>'
        for x, y in history
    )
    # X-axis tick labels
    x_ticks = "".join(
        f'<text x="{sx(x):.1f}" y="{h - 6}" font-size="10" '
        f'text-anchor="middle" fill="#888">R{x if x > 0 else "start"}</text>'
        for x in xs
    )
    # Phase divider
    divider = ""
    if 0 < phase_split <= max(xs):
        x_div = sx(phase_split - 0.5)
        divider = (
            f'<line x1="{x_div:.1f}" y1="{pad_t}" x2="{x_div:.1f}" '
            f'y2="{pad_t + plot_h}" stroke="#999" stroke-width="1" '
            f'stroke-dasharray="4 3"/>'
            f'<text x="{x_div:.1f}" y="{pad_t - 2}" font-size="10" '
            f'text-anchor="middle" fill="#888">phase boundary</text>'
        )
    # Y-axis labels
    y_axis = (
        f'<text x="{pad_l - 8}" y="{sy(y_max) + 4}" font-size="10" '
        f'text-anchor="end" fill="#888">{y_max:.0f}</text>'
        f'<text x="{pad_l - 8}" y="{sy(y_min) + 4}" font-size="10" '
        f'text-anchor="end" fill="#888">{y_min:.0f}</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" '
        f'fill="white" stroke="#ddd"/>'
        f'{divider}'
        f'<path d="{path}" fill="none" stroke="#0366d6" stroke-width="2"/>'
        f'{points}{labels}{x_ticks}{y_axis}</svg>'
    )


def _esc(s) -> str:
    return html_lib.escape(str(s) if s is not None else "")


def _synthesize_feedback(piece_title: str, rationales: list, model: str) -> str:
    """One LLM call: consolidate per-round rationales into 2-3 plain sentences
    aimed at the artist. Returns synthesized text or "" if the call fails."""
    if not rationales:
        return ""
    try:
        # Import here to avoid circular import at module load
        from .llm import _client
        client = _client()
        bullets = "\n".join(f"- {r}" for r in rationales)
        prompt = (
            f"Below are independent critique observations of a single watercolor "
            f"piece, made across multiple evaluation rounds:\n\n{bullets}\n\n"
            "Write 2-3 plain-English sentences for the artist who made this "
            "piece. Synthesize the recurring themes — what's working and what "
            "could be improved. Speak directly to the artist about their piece. "
            "Be honest but encouraging. Don't reference 'rounds', 'evaluations', "
            "or 'observations' — just talk about the painting. Don't repeat the "
            "piece's title."
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        return f"(Feedback synthesis failed: {e})"


def _pctl_of_elo(elo: float, pool_elos: list) -> int:
    if not pool_elos:
        return 0
    below = sum(1 for e in pool_elos if e < elo)
    return int(round(100.0 * below / len(pool_elos)))


def _pick_comparison_tiers(result: dict, pool_elos: list) -> dict:
    """Bucket the candidate's anchors into tiers by percentile.

    Returns {'much_better': [...], 'similar': [...], 'much_worse': [...]}
    where each list is up to 3 unique anchors (de-duped by reddit_id).
    Each anchor dict gets an added 'percentile' field."""
    cand_pctl = result["percentile"]

    # Collect all unique anchors with their best rationale
    seen = {}
    for rd in result["rounds"]:
        for a in rd["anchors"]:
            if a.get("flagged_not_art"):
                continue
            aid = a["reddit_id"]
            if aid not in seen:
                seen[aid] = {**a, "round": rd["round"], "phase": rd["phase"]}
    anchors = list(seen.values())
    for a in anchors:
        a["percentile"] = _pctl_of_elo(a["anchor_pre_elo"], pool_elos)

    # Tier thresholds adapt to where the candidate sits
    much_better = sorted(
        [a for a in anchors if a["percentile"] >= min(85, cand_pctl + 30)],
        key=lambda a: -a["percentile"],
    )[:3]
    much_worse = sorted(
        [a for a in anchors if a["percentile"] <= max(15, cand_pctl - 30)],
        key=lambda a: a["percentile"],
    )[:3]
    similar = sorted(
        [a for a in anchors
         if a["reddit_id"] not in {x["reddit_id"] for x in much_better + much_worse}
         and abs(a["percentile"] - cand_pctl) <= 15],
        key=lambda a: abs(a["percentile"] - cand_pctl),
    )[:3]
    return {"much_better": much_better, "similar": similar, "much_worse": much_worse}


def _verdict_headline(pctl: int) -> tuple[str, str]:
    """Return (big-line, subline) phrasing tailored to the percentile."""
    if pctl >= 90:
        return ("Your piece is in the top tier.",
                "Stronger craft and execution than the vast majority of submissions.")
    if pctl >= 75:
        return ("Your piece is well above average.",
                "More resolved than most of the pool — clearly above the middle.")
    if pctl >= 55:
        return ("Your piece is above the median.",
                "Better than more than half of the submissions shown.")
    if pctl >= 40:
        return ("Your piece sits around the middle.",
                "Comparable craft to a typical submission in this pool.")
    if pctl >= 20:
        return ("Your piece is below average.",
                "Some clear strengths but room to grow vs the broader pool.")
    return ("Your piece is in the lower tier.",
            "There's a lot of growth opportunity — see the comparisons below.")


def _render_insertion_report(result: dict, image_url: str, subreddit: str,
                              out_dir: Path, model: str = "openai/gpt-5.4-mini",
                              pool_elos: list | None = None) -> str:
    """Generate a single self-contained HTML report aimed at a new user
    asking "is my art any good?" — percentile-first, with consolidated
    feedback and comparison samples across tiers. The technical per-round
    detail is collapsed into a details/summary section at the bottom.

    The candidate image (typically a data URI from a local file insertion)
    is embedded inline ONCE via JS deduplication."""
    candidate_thumb = ""  # placeholder; JS swaps the data URI in at load
    embed_candidate_inline = bool(image_url) and image_url.startswith("data:")

    pctl = result.get("percentile", 0)

    # Consolidated artist-facing feedback (1 extra LLM call)
    cand_rationales = [
        rd["candidate"]["rationale_this_round"]
        for rd in result["rounds"]
        if rd.get("candidate", {}).get("rationale_this_round")
    ]
    print(f"  Synthesizing consolidated feedback via {model}...")
    feedback = _synthesize_feedback(result["title"], cand_rationales, model)

    # Comparison tiers (anchors bucketed by percentile)
    tiers = _pick_comparison_tiers(result, pool_elos or [])

    # Headline copy keyed to where the piece landed
    headline, subline = _verdict_headline(pctl)

    # Percentile bar with scale labels
    pctl_bar_html = f"""
    <div class="pctile-bar" title="Percentile in the {result['of']}-piece pool">
      <div class="gradient"></div>
      <div class="marker" style="left:calc({pctl}% - 3px);"></div>
      <div class="scale-label" style="left:0%;">bottom</div>
      <div class="scale-label" style="left:50%;">median</div>
      <div class="scale-label" style="left:100%;">top</div>
    </div>
    """

    # Feedback block
    feedback_html = ""
    if feedback:
        feedback_html = f"""
        <div class="feedback">
          <div class="label">What the jury said about your piece</div>
          {_esc(feedback)}
        </div>"""

    # Tier section renderer
    def _tier_html(label: str, anchors: list, pill_cls: str, intro: str = "") -> str:
        if not anchors:
            return ""
        cards = []
        for a in anchors:
            thumb = _esc(a.get("image_url") or "")
            title = _esc((a.get("title") or a["reddit_id"])[:80])
            permalink = _esc(a.get("permalink") or "")
            rationale = _esc(a.get("rationale_this_round") or "")
            pctl_label = f"{a['percentile']}<sup>th</sup> %ile"
            link_html = (f'<a href="{permalink}" target="_blank">{title}</a>'
                         if permalink else title)
            cards.append(f"""
            <div class="tier-card">
              <a href="{thumb}" target="_blank">
                <img class="thumb" src="{thumb}" alt="" loading="lazy">
              </a>
              <div>
                <div class="title">{link_html}</div>
                <div class="pctile-line">
                  <span class="pctile-pill {pill_cls}">{pctl_label}</span>
                  <span>vs. your <span class="pctile-pill you">{pctl}<sup>th</sup></span></span>
                </div>
                <div class="rationale">"{rationale}"</div>
              </div>
            </div>""")
        intro_html = (f'<p style="color:#666;font-size:13px;margin:6px 0 12px;">'
                      f'{_esc(intro)}</p>' if intro else "")
        return f"""
        <section class="tier-section">
          <h2>{_esc(label)}
            <span class="count">{len(anchors)} from your evaluation rounds</span>
          </h2>
          {intro_html}
          <div class="tier-card-grid">{"".join(cards)}</div>
        </section>"""

    much_better_html = _tier_html(
        "Much better than yours", tiers["much_better"], "much-better",
        "These pieces are noticeably stronger — here's what the jury liked about each."
    )
    similar_html = _tier_html(
        "About the same as yours", tiers["similar"], "similar",
        "These pieces landed near your percentile — the jury saw comparable strengths and weaknesses."
    )
    much_worse_html = _tier_html(
        "Much weaker than yours", tiers["much_worse"], "much-worse",
        "These pieces scored well below yours — for contrast with what the jury thought your piece does better."
    )

    # ===== Technical detail (collapsed) =====
    history = [(0, ELO_INITIAL)]
    for rd in result["rounds"]:
        if "candidate_elo_after" in rd:
            history.append((rd["round"], rd["candidate_elo_after"]))
    phase_split = 0
    for rd in result["rounds"]:
        if rd.get("phase") == "focused":
            phase_split = rd["round"]
            break
    elo_chart_svg = _make_elo_svg(history, phase_split)

    round_cards = []
    prev_elo = ELO_INITIAL
    for rd in result["rounds"]:
        phase = rd.get("phase", "?")
        phase_cls = f"phase-{phase}" if phase in ("random", "focused") else "phase-random"
        post_elo = rd.get("candidate_elo_after")
        pctl_change_html = ""
        if post_elo is not None and pool_elos:
            after_pctl = _pctl_of_elo(post_elo, pool_elos)
            prev_pctl = _pctl_of_elo(prev_elo, pool_elos)
            delta = after_pctl - prev_pctl
            sign = "+" if delta > 0 else ""
            pctl_change_html = (
                f'<span style="font-size:11px;color:#888;">'
                f'{prev_pctl}<sup>th</sup> → {after_pctl}<sup>th</sup> %ile '
                f'({sign}{delta})</span>'
            )
            prev_elo = post_elo

        # Build group rows (candidate + anchors), in LLM-ranked order
        rat_by_id = {}
        cand = rd["candidate"]
        if cand.get("rationale_this_round"):
            rat_by_id[result["candidate_id"]] = cand["rationale_this_round"]
        for a in rd["anchors"]:
            if a.get("rationale_this_round"):
                rat_by_id[a["reddit_id"]] = a["rationale_this_round"]

        # Order pieces by placed_position; flagged-not-art at bottom.
        # Candidate's image_url is left blank in the HTML markup; JS injects
        # the inline data URI once for all marked elements at load time.
        all_pieces = [
            {"id": result["candidate_id"], "title": result["title"],
             "image_url": "", "permalink": None,
             "rank": cand.get("placed_position"),
             "flagged": cand.get("flagged_not_art", False),
             "is_candidate": True}
        ] + [
            {"id": a["reddit_id"], "title": a.get("title") or a["reddit_id"],
             "image_url": a.get("image_url"), "permalink": a.get("permalink"),
             "rank": a.get("placed_position"),
             "flagged": a.get("flagged_not_art", False),
             "is_candidate": False,
             "anchor_elo": a.get("anchor_pre_elo"),
             "anchor_rank": a.get("anchor_pre_rank")}
            for a in rd["anchors"]
        ]
        ranked = sorted(
            [p for p in all_pieces if not p["flagged"]],
            key=lambda p: p["rank"] if p["rank"] is not None else 9999
        )
        flagged = [p for p in all_pieces if p["flagged"]]

        rows_html = ""
        for p in ranked + flagged:
            cls = ' class="candidate-row"' if p["is_candidate"] else ""
            permalink_html = (
                f' · <a href="{_esc(p["permalink"])}" target="_blank">reddit</a>'
                if p.get("permalink") else ""
            )
            anchor_info = ""
            if not p["is_candidate"]:
                anchor_info = (
                    f' <span style="color:#888;font-size:11px;">'
                    f'(anchor pre-ELO {p["anchor_elo"]:.0f})</span>'
                )
            title_html = (
                f'<b>{_esc(p["title"])}</b> <span style="color:#888;font-weight:normal;">'
                f'(CANDIDATE)</span>'
                if p["is_candidate"]
                else _esc(p["title"][:80] if p["title"] else p["id"])
            )
            if p["flagged"]:
                rank_cell = '<span class="rank-pill flagged">NOT ART</span>'
                rationale_cell = '<em style="color:#999;">flagged as not-art</em>'
            else:
                rank_cell = f'<span class="rank-pill">{p["rank"]}</span>'
                rationale_cell = _esc(rat_by_id.get(p["id"], ""))
            img_attr = (
                'data-candidate-img alt=""'
                if p["is_candidate"]
                else f'src="{_esc(p["image_url"] or "")}" alt="" loading="lazy"'
            )
            rows_html += f"""
                <tr{cls}>
                  <td>{rank_cell}</td>
                  <td class="thumb-cell">
                    <img class="mini" {img_attr}>
                  </td>
                  <td class="group-title">{title_html}{anchor_info}{permalink_html}</td>
                  <td>{rationale_cell}</td>
                </tr>"""

        round_cards.append(f"""
        <div class="round-card">
          <div class="round-head">
            <h3>Round {rd['round']}<span class="phase-pill {phase_cls}">{phase}</span></h3>
            {pctl_change_html}
          </div>
          <table class="group"><tbody>{rows_html}</tbody></table>
          <div class="overall-rat">{_esc(rd.get('overall_rationale') or '(no overall rationale)')}</div>
        </div>""")

    title_str = f"How does your piece compare? · {result['title']}"
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(title_str)}</title>
<style>{_REPORT_CSS}</style></head><body>

<h1>{_esc(result['title'])}</h1>
<div class="meta">
  Compared against {result['of']} watercolor pieces from r/{_esc(subreddit)} ·
  generated {timestamp}
</div>

<div class="hero">
  <div>
    <a id="candidate-link" target="_blank">
      <img class="candidate" data-candidate-img alt="">
    </a>
  </div>
  <div>
    <div class="headline">{_esc(headline)}</div>
    <div class="big-number">{pctl}<sup>th</sup></div>
    <div class="subline">percentile · {_esc(subline)}</div>
    {pctl_bar_html}
  </div>
</div>

{feedback_html}

{much_better_html}

{similar_html}

{much_worse_html}

<details class="tech-detail">
  <summary>See the detailed round-by-round evaluations
  ({len(result['rounds'])} rounds)</summary>
  <p style="font-size:12px;color:#888;">
    Each round, your piece was shown to the jury alongside 4 reference pieces.
    The first 4 rounds used random reference pieces (to get a wide-range
    ballpark); the next 4 used pieces near your tentative percentile (to
    refine the placement).
  </p>
  <div style="margin: 16px 0; padding: 12px; background: #fafafa;
              border: 1px solid #eee; border-radius: 4px;">
    <div style="font-size:11px;color:#888;margin-bottom:6px;">
      Percentile movement across rounds
    </div>
    {elo_chart_svg}
  </div>
  {"".join(round_cards)}
</details>

<script>
// Embed candidate image once; JS swaps it into every <img data-candidate-img>
// to avoid Chrome file:// restrictions and 9x bloat.
const __CAND_SRC__ = {json.dumps(image_url if embed_candidate_inline else "")};
if (__CAND_SRC__) {{
  document.querySelectorAll('[data-candidate-img]').forEach(el => {{
    el.src = __CAND_SRC__;
  }});
  const link = document.getElementById('candidate-link');
  if (link) link.href = __CAND_SRC__;
}}
</script>

</body></html>
"""


def _reconstruct_result_from_db(candidate_id: str, subreddit: str
                                 ) -> tuple[dict, str, str, list]:
    """Rebuild a `result` dict (matching insert() return shape) from stored
    comparisons. Returns (result, candidate_image_url, model, pool_elos).

    `candidate_elo_after` is replayed from ELO_INITIAL using the anchor ELOs
    AT THE TIME OF THE INSERTION. Since we don't store anchor-elo-snapshots,
    we use the current ratings table — close but not exact if the pool has
    been reranked since."""
    with db.connect() as conn:
        cand_row = conn.execute(
            "SELECT * FROM pieces WHERE reddit_id = ?", (candidate_id,)
        ).fetchone()
        if not cand_row:
            raise SystemExit(f"candidate_id {candidate_id} not in DB")
        cand = dict(cand_row)
        comps = conn.execute(
            "SELECT * FROM comparisons WHERE candidate_id = ? ORDER BY id",
            (candidate_id,),
        ).fetchall()
        if not comps:
            raise SystemExit(f"no comparisons found for {candidate_id}")

        # Pool stats for percentile
        pool_rows = conn.execute(
            """SELECT r.elo FROM ratings r JOIN pieces p ON p.reddit_id = r.reddit_id
               WHERE p.subreddit = ? AND p.is_candidate = 0 AND r.n_not_art_flags < 2""",
            (subreddit,),
        ).fetchall()
        pool_elos = [float(r["elo"]) for r in pool_rows]

        # Final candidate elo
        rating_row = conn.execute(
            "SELECT elo FROM ratings WHERE reddit_id = ?", (candidate_id,)
        ).fetchone()
        final_elo = float(rating_row["elo"]) if rating_row else ELO_INITIAL

    # Build rounds + replay ELO. Pull anchor ELOs at request time.
    rounds = []
    candidate_elo = ELO_INITIAL
    with db.connect() as conn:
        for idx, c in enumerate(comps):
            piece_ids = json.loads(c["piece_ids_json"])
            ranking = json.loads(c["ranking_json"])
            per_piece = (json.loads(c["per_piece_rationales_json"])
                         if c["per_piece_rationales_json"] else [])
            rat_by_id = {x["piece_id"]: x.get("rationale") for x in per_piece}
            anchor_ids = [p for p in piece_ids if p != candidate_id]
            placeholders = ",".join("?" * len(anchor_ids))
            anchor_rows = conn.execute(
                f"""SELECT p.*, r.elo FROM pieces p
                    JOIN ratings r ON r.reddit_id = p.reddit_id
                    WHERE p.reddit_id IN ({placeholders})""",
                anchor_ids,
            ).fetchall()
            anchors_by_id = {r["reddit_id"]: dict(r) for r in anchor_rows}

            anchors_info = []
            for aid in anchor_ids:
                a = anchors_by_id.get(aid, {})
                anchors_info.append({
                    "reddit_id": aid,
                    "title": a.get("title"),
                    "permalink": a.get("permalink"),
                    "image_url": a.get("image_url"),
                    "anchor_pre_elo": float(a.get("elo", ELO_INITIAL)),
                    "anchor_pre_rank": None,
                    "rationale_this_round": rat_by_id.get(aid),
                    "placed_position": (ranking.index(aid) + 1
                                        if aid in ranking else None),
                    "flagged_not_art": aid not in ranking,
                })

            # Replay ELO using current anchor ELOs (approximation)
            if candidate_id in ranking:
                local_ratings = {candidate_id: candidate_elo}
                for a in anchors_info:
                    local_ratings[a["reddit_id"]] = a["anchor_pre_elo"]
                frozen = {a["reddit_id"] for a in anchors_info}
                new_r, _ = apply_group_ranking(local_ratings, ranking, frozen_ids=frozen)
                candidate_elo = new_r[candidate_id]
                elo_after = candidate_elo
            else:
                elo_after = candidate_elo  # no update if flagged

            phase = ("random" if idx < INSERTION_RANDOM_GROUPS else "focused")

            round_info = {
                "round": idx + 1,
                "phase": phase,
                "anchors": anchors_info,
                "candidate": {
                    "rationale_this_round": rat_by_id.get(candidate_id),
                    "placed_position": (ranking.index(candidate_id) + 1
                                        if candidate_id in ranking else None),
                    "flagged_not_art": candidate_id not in ranking,
                },
                "overall_rationale": c["rationale"] or "",
                "candidate_elo_after": elo_after,
            }
            rounds.append(round_info)

    rank = sum(1 for e in pool_elos if e > final_elo) + 1
    below = sum(1 for e in pool_elos if e < final_elo)
    pctl = int(round(100.0 * below / max(1, len(pool_elos))))
    result = {
        "candidate_id": candidate_id,
        "title": cand["title"] or candidate_id,
        "elo": final_elo,
        "rank": rank,
        "of": len(pool_elos) + 1,
        "percentile": pctl,
        "rounds": rounds,
    }
    # Most common model across the candidate's comparisons = the one used
    model = (comps[0]["model"] if comps else "openai/gpt-5.4-mini")
    return result, cand["image_url"] or "", model, pool_elos


def print_round_report(result: dict) -> None:
    """Pretty-print all per-round detail for a single inserted candidate."""
    title = result["title"]
    print("\n" + "=" * 90)
    print(f"  {title}  →  final ELO {result['elo']:.1f}  "
          f"(rank {result['rank']}/{result['of']}, ~{result['percentile']}th percentile)")
    print("=" * 90)

    for rd in result["rounds"]:
        phase = rd.get("phase", "?")
        print(f"\n  --- Round {rd['round']} [{phase}] ---")
        cand = rd["candidate"]
        cand_pos = cand["placed_position"]
        flag = " [FLAGGED NOT ART]" if cand["flagged_not_art"] else ""
        print(f"  CANDIDATE placed {cand_pos}/{1 + len(rd['anchors'])}{flag}")
        if cand["rationale_this_round"]:
            print(f"    rationale: {cand['rationale_this_round']}")
        print(f"  Anchors evaluated against:")
        for a in rd["anchors"]:
            pos = a["placed_position"]
            flag = " [FLAGGED NOT ART]" if a["flagged_not_art"] else ""
            print(f"    · pre-rank #{a['anchor_pre_rank']:>3} (ELO {a['anchor_pre_elo']:.0f}) "
                  f"placed {pos}/{1 + len(rd['anchors'])}{flag}: \"{(a['title'] or '')[:60]}\"")
            print(f"      {a['permalink']}")
            if a["rationale_this_round"]:
                print(f"      rationale: {a['rationale_this_round']}")
        print(f"  Overall: {rd['overall_rationale']}")

    print("\n  CONCATENATED CANDIDATE RATIONALES (across all rounds):")
    for i, rd in enumerate(result["rounds"], 1):
        rat = rd["candidate"].get("rationale_this_round") or "(none — flagged not-art)"
        print(f"    [{i}] {rat}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image-url", help="Remote image URL")
    src.add_argument("--image-path", type=Path, help="Local image file path")
    src.add_argument("--html-from-db", metavar="CANDIDATE_ID",
                     help="Skip the LLM run; reconstruct an HTML report for an "
                          "existing candidate_id from stored comparisons")
    parser.add_argument("--title", default="Candidate submission")
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--groups", type=int, default=INSERTION_GROUPS,
                        help="Total rounds")
    parser.add_argument("--random-groups", type=int, default=INSERTION_RANDOM_GROUPS,
                        help="First N rounds sample anchors at random from the "
                             "entire pool (Phase 1); remaining rounds sample "
                             "from a window around the candidate's current ELO "
                             "(Phase 2)")
    parser.add_argument("--focused-window", type=int, default=INSERTION_FOCUSED_WINDOW,
                        help="Size of the rating-similarity window for Phase 2")
    parser.add_argument("--model", default=LLM_MODEL)
    parser.add_argument("--html-out", type=Path, default=None,
                        help="Custom path for the HTML insertion report "
                             "(default: insertions/<candidate_id>.html)")
    parser.add_argument("--no-html", action="store_true",
                        help="Skip writing the HTML insertion report")
    parser.add_argument("--open", action="store_true",
                        help="Open the HTML report in the default browser")
    args = parser.parse_args()

    if args.html_from_db:
        result, image_url, model_used, pool_elos = _reconstruct_result_from_db(
            args.html_from_db, args.subreddit
        )
    else:
        if args.image_path:
            if not args.image_path.exists():
                raise SystemExit(f"File not found: {args.image_path}")
            image_url = _path_to_data_uri(args.image_path)
        else:
            image_url = args.image_url

        result = insert(image_url, args.subreddit, args.title, args.groups,
                        args.model, random_groups=args.random_groups,
                        focused_window=args.focused_window)
        print_round_report(result)
        # Pull pool_elos for the report's percentile bands
        with db.connect() as conn:
            pool_rows = conn.execute(
                """SELECT r.elo FROM ratings r JOIN pieces p
                   ON p.reddit_id = r.reddit_id WHERE p.subreddit = ?
                   AND p.is_candidate = 0 AND r.n_not_art_flags < 2""",
                (args.subreddit,),
            ).fetchall()
        pool_elos = [float(r["elo"]) for r in pool_rows]
        model_used = args.model

    if not args.no_html:
        insertions_dir = Path(MODULE_DIR) / "insertions"
        insertions_dir.mkdir(exist_ok=True)
        out_path = args.html_out or (insertions_dir / f"{result['candidate_id']}.html")
        html_str = _render_insertion_report(
            result, image_url, args.subreddit, insertions_dir,
            model=model_used, pool_elos=pool_elos,
        )
        out_path.write_text(html_str, encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"\nWrote insertion report: {out_path}  ({size_kb:.0f} KB)")
        if args.open:
            webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
