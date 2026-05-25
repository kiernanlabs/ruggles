"""Run the same group of images through N different OpenRouter models in
parallel, then write a side-by-side HTML report.

Each model sees the SAME shuffled order of images so position bias is held
constant across models — any ranking differences are attributable to the
model itself, not which slot a piece was shown in.

Usage:
    # Random 5 pieces from the DB
    python -m reddit_art_ranker.compare_models \\
        --models openai/gpt-5.4-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash \\
        --sample 5

    # Specific reddit IDs from the DB
    python -m reddit_art_ranker.compare_models \\
        --models openai/gpt-5.4-mini,google/gemini-2.5-flash \\
        --piece-ids 1ael7fc,1kf6wm6,1nrifqm

    # Local image files (e.g. candidate pieces)
    python -m reddit_art_ranker.compare_models \\
        --models openai/gpt-5.4-mini,anthropic/claude-haiku-4.5 \\
        --image-paths reddit_art_ranker/IMG_2766.JPEG,reddit_art_ranker/IMG_2767.JPEG \\
        --piece-ids 1ael7fc,1kf6wm6,1nrifqm
"""

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import random
import sys
import time
import webbrowser
from pathlib import Path

from . import db
from .config import MODULE_DIR, SUBREDDIT
from .insert import _path_to_data_uri
from .llm import rank_group

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _spearman(xs: list, ys: list) -> float | None:
    """Spearman rank correlation, stdlib-only. Ignores any None in either."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs2, ys2 = zip(*pairs)

    def ranks(values):
        sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
        r = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while (j + 1 < len(values)
                   and values[sorted_idx[j + 1]] == values[sorted_idx[i]]):
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[sorted_idx[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(list(xs2)), ranks(list(ys2))
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = (sum((a - mx) ** 2 for a in rx)) ** 0.5
    dy = (sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / (dx * dy) if dx and dy else None


def _resolve_sources(piece_ids: list, image_paths: list, sample: int,
                     subreddit: str) -> list:
    """Return list of {key, label, image_url (or data URI), title, permalink}."""
    items = []
    if image_paths:
        for p in image_paths:
            path = Path(p)
            if not path.exists():
                raise SystemExit(f"File not found: {p}")
            items.append({
                "key": f"local:{path.name}",
                "label": path.name,
                "image_url": _path_to_data_uri(path),
                "title": path.name,
                "permalink": None,
                "is_local": True,
            })
    if piece_ids:
        with db.connect() as conn:
            placeholders = ",".join("?" * len(piece_ids))
            rows = conn.execute(
                f"SELECT reddit_id, image_url, title, permalink FROM pieces "
                f"WHERE reddit_id IN ({placeholders})",
                piece_ids,
            ).fetchall()
        by_id = {r["reddit_id"]: r for r in rows}
        for pid in piece_ids:
            if pid not in by_id:
                raise SystemExit(f"piece_id {pid} not found in DB")
            r = by_id[pid]
            items.append({
                "key": pid,
                "label": pid,
                "image_url": r["image_url"],
                "title": r["title"],
                "permalink": r["permalink"],
                "is_local": False,
            })
    if sample:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.reddit_id, p.image_url, p.title, p.permalink
                FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
                WHERE p.subreddit = ? AND p.is_candidate = 0
                      AND r.n_not_art_flags < 2
                ORDER BY RANDOM() LIMIT ?
                """,
                (subreddit, sample),
            ).fetchall()
        for r in rows:
            items.append({
                "key": r["reddit_id"],
                "label": r["reddit_id"],
                "image_url": r["image_url"],
                "title": r["title"],
                "permalink": r["permalink"],
                "is_local": False,
            })
    if len(items) < 2:
        raise SystemExit("Need at least 2 images to compare.")
    return items


def _run_one_model(model: str, image_urls: list) -> dict:
    """Call rank_group for one model, capture timing + errors."""
    t0 = time.time()
    try:
        result = rank_group(image_urls, model=model, shuffle=False)
        elapsed = time.time() - t0
        return {"model": model, "ok": True, "elapsed_s": elapsed, "result": result}
    except Exception as e:
        elapsed = time.time() - t0
        return {"model": model, "ok": False, "elapsed_s": elapsed,
                "error": f"{type(e).__name__}: {e}"}


CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1400px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 6px; }
h2 { margin-top: 32px; padding-bottom: 6px; border-bottom: 2px solid #eee; }
.meta { color: #666; font-size: 13px; margin-bottom: 24px; }
.piece-card { display: grid; grid-template-columns: 220px 1fr; gap: 16px;
              margin-bottom: 28px; padding: 12px; border: 1px solid #eee;
              border-radius: 6px; }
.piece-card img { width: 200px; height: 200px; object-fit: cover;
                   border-radius: 4px; background: #eee; }
.piece-title { font-weight: 600; margin-bottom: 6px; }
.piece-meta a { color: #0366d6; text-decoration: none; font-size: 12px;
                margin-right: 8px; }
table.ratings { width: 100%; border-collapse: collapse; margin-top: 8px;
                font-size: 14px; }
table.ratings th, table.ratings td { padding: 6px 8px; border-bottom: 1px solid #f0f0f0;
                                      vertical-align: top; text-align: left; }
table.ratings th { background: #fafafa; font-size: 12px; text-transform: uppercase;
                   color: #666; letter-spacing: 0.04em; }
.rank-pill { display: inline-block; min-width: 28px; padding: 3px 8px;
             border-radius: 12px; background: #0366d6; color: white;
             font-weight: 600; text-align: center; font-size: 13px; }
.rank-pill.flagged { background: #c0392b; }
.rationale { color: #555; line-height: 1.4; }
.model-card { padding: 12px 16px; border: 1px solid #eee; border-radius: 6px;
              margin-bottom: 12px; }
.model-name { font-family: ui-monospace, Menlo, Consolas, monospace;
              font-size: 14px; font-weight: 600; }
.model-timing { color: #666; font-size: 12px; float: right; }
.model-overall { color: #444; line-height: 1.5; margin-top: 6px; font-size: 14px; }
.error { background: #fff5f5; border: 1px solid #ffd6d6; padding: 10px 14px;
         border-radius: 4px; color: #c0392b; font-family: monospace; font-size: 12px; }
table.corr { border-collapse: collapse; margin-top: 8px; font-size: 13px; }
table.corr th, table.corr td { padding: 6px 10px; border: 1px solid #eee;
                                text-align: center; font-variant-numeric: tabular-nums; }
table.corr th { background: #fafafa; font-family: monospace; font-size: 11px; }
.consensus { background: #f0f8ff; padding: 4px 10px; border-radius: 4px;
             display: inline-block; font-size: 12px; color: #555; }
"""


def render_html(items: list, model_results: list) -> str:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    model_names = [m["model"] for m in model_results]

    # Build per-piece per-model ranking lookup
    # rankings[piece_key][model_name] = {"rank": int or None, "rationale": str, "flagged": bool}
    rankings = {item["key"]: {} for item in items}
    for mres in model_results:
        if not mres["ok"]:
            for item in items:
                rankings[item["key"]][mres["model"]] = {"error": mres["error"]}
            continue
        res = mres["result"]
        ranked_orig_indices = res["order"]
        rank_by_orig = {oi: pos + 1 for pos, oi in enumerate(ranked_orig_indices)}
        flagged_orig = set(res["not_art_indices"])
        rat_by_orig = {x["original_index"]: x["rationale"] for x in res["per_piece_rationales"]}
        for orig_idx, item in enumerate(items):
            rankings[item["key"]][mres["model"]] = {
                "rank": rank_by_orig.get(orig_idx),
                "rationale": rat_by_orig.get(orig_idx),
                "flagged": orig_idx in flagged_orig,
            }

    # Mean rank per piece (for consensus ordering of cards). Flagged/error = ignored.
    def mean_rank(piece_key):
        ranks = [v["rank"] for v in rankings[piece_key].values()
                 if isinstance(v, dict) and v.get("rank") is not None]
        return sum(ranks) / len(ranks) if ranks else 999

    items_sorted = sorted(items, key=lambda x: mean_rank(x["key"]))

    # Pairwise Spearman across models
    corr_rows_html = ""
    if len(model_results) >= 2 and all(m["ok"] for m in model_results):
        ordered_keys = [it["key"] for it in items]
        per_model_vec = {}
        for m in model_results:
            per_model_vec[m["model"]] = [
                rankings[k][m["model"]].get("rank") for k in ordered_keys
            ]
        corr_rows_html += "<table class='corr'><thead><tr><th></th>"
        for m in model_names:
            corr_rows_html += f"<th>{html.escape(m)}</th>"
        corr_rows_html += "</tr></thead><tbody>"
        for m1 in model_names:
            corr_rows_html += f"<tr><th>{html.escape(m1)}</th>"
            for m2 in model_names:
                if m1 == m2:
                    corr_rows_html += "<td>—</td>"
                else:
                    rho = _spearman(per_model_vec[m1], per_model_vec[m2])
                    corr_rows_html += f"<td>{rho:+.2f}</td>" if rho is not None else "<td>n/a</td>"
            corr_rows_html += "</tr>"
        corr_rows_html += "</tbody></table>"

    # Per-piece cards
    cards_html = ""
    for pos, item in enumerate(items_sorted, 1):
        permalink_html = (
            f"<a href='{html.escape(item['permalink'])}' target='_blank'>reddit</a> "
            if item["permalink"] else ""
        )
        mean_r = mean_rank(item["key"])
        consensus_html = (
            f"<span class='consensus'>mean rank across models: {mean_r:.1f}</span>"
            if mean_r != 999 else ""
        )
        title = html.escape(item["title"] or item["label"])
        rows_html = ""
        for m in model_names:
            entry = rankings[item["key"]].get(m, {})
            if "error" in entry:
                rows_html += (
                    f"<tr><td class='model-name'>{html.escape(m)}</td>"
                    f"<td colspan='2' class='error'>{html.escape(entry['error'][:200])}</td></tr>"
                )
                continue
            rank = entry.get("rank")
            flagged = entry.get("flagged")
            rationale = entry.get("rationale") or ""
            if flagged:
                rank_cell = "<span class='rank-pill flagged'>NOT ART</span>"
            elif rank is not None:
                rank_cell = f"<span class='rank-pill'>{rank}</span>"
            else:
                rank_cell = "—"
            rows_html += (
                f"<tr><td class='model-name'>{html.escape(m)}</td>"
                f"<td>{rank_cell}</td>"
                f"<td class='rationale'>{html.escape(rationale)}</td></tr>"
            )
        cards_html += f"""
        <div class='piece-card'>
            <div>
                <a href="{html.escape(item['image_url'])}" target='_blank'>
                    <img src="{html.escape(item['image_url'])}" alt="" loading='lazy'>
                </a>
                <div class='piece-meta' style='margin-top:6px;'>{permalink_html}</div>
                <div style='margin-top:6px;'>{consensus_html}</div>
            </div>
            <div>
                <div class='piece-title'>#{pos}. {title}</div>
                <table class='ratings'>
                    <thead><tr><th style='width:25%'>Model</th><th style='width:8%'>Rank</th><th>Rationale</th></tr></thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>
        </div>
        """

    # Per-model summary
    model_cards_html = ""
    for mres in model_results:
        m = html.escape(mres["model"])
        timing = f"{mres['elapsed_s']:.1f}s"
        if not mres["ok"]:
            model_cards_html += (
                f"<div class='model-card'>"
                f"<span class='model-name'>{m}</span>"
                f"<span class='model-timing'>{timing}</span>"
                f"<div class='error'>{html.escape(mres['error'])}</div></div>"
            )
            continue
        overall = html.escape(mres["result"]["rationale"] or "")
        flagged_count = len(mres["result"]["not_art_indices"])
        flag_note = f" · {flagged_count} flagged not-art" if flagged_count else ""
        usage = mres["result"].get("usage")
        finish = mres["result"].get("finish_reason")
        usage_note = ""
        if usage:
            usage_note = (f" · {usage['prompt_tokens']} in / "
                          f"{usage['completion_tokens']} out tokens")
            if finish == "length":
                usage_note += " <span style='color:#c0392b;font-weight:600;'>[hit max_tokens]</span>"
        model_cards_html += (
            f"<div class='model-card'>"
            f"<span class='model-name'>{m}</span>"
            f"<span class='model-timing'>{timing}{flag_note}{usage_note}</span>"
            f"<div class='model-overall'>{overall}</div></div>"
        )

    title_str = f"Multi-model comparison · {len(items)} pieces · {len(model_results)} models"
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{title_str}</title>
<style>{CSS}</style></head><body>
<h1>{title_str}</h1>
<div class='meta'>
  Generated {timestamp} · pieces ordered by mean rank across all models (best first).
</div>

<h2>Models</h2>
{model_cards_html}

<h2>Pairwise ranking agreement (Spearman ρ)</h2>
{corr_rows_html or "<p style='color:#999;font-size:13px;'>Needs ≥2 successful runs.</p>"}

<h2>Per-piece breakdown</h2>
{cards_html}

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True,
                        help="Comma-separated OpenRouter model slugs (e.g. "
                             "openai/gpt-5.4-mini,anthropic/claude-haiku-4.5)")
    parser.add_argument("--piece-ids", default="",
                        help="Comma-separated reddit_ids from the DB")
    parser.add_argument("--image-paths", default="",
                        help="Comma-separated local file paths")
    parser.add_argument("--sample", type=int, default=0,
                        help="Pull N random eligible pieces from DB")
    parser.add_argument("--subreddit", default=SUBREDDIT,
                        help="(for --sample) subreddit to draw from")
    parser.add_argument("--out", type=Path,
                        default=Path(MODULE_DIR) / "model_comparison.html")
    parser.add_argument("--open", action="store_true",
                        help="Open the report in the default browser after writing")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    piece_ids = [p.strip() for p in args.piece_ids.split(",") if p.strip()]
    image_paths = [p.strip() for p in args.image_paths.split(",") if p.strip()]

    items = _resolve_sources(piece_ids, image_paths, args.sample, args.subreddit)
    print(f"Resolved {len(items)} pieces:")
    for it in items:
        print(f"  · {it['label']:40s}  {(it['title'] or '')[:50]}")
    print(f"\nCalling {len(models)} models in parallel:")
    for m in models:
        print(f"  · {m}")
    print()

    # Pre-shuffle once so all models see the same input order. Position bias
    # then affects all models equally.
    indices = list(range(len(items)))
    random.shuffle(indices)
    shuffled_urls = [items[i]["image_url"] for i in indices]
    items_in_shuffled_order = [items[i] for i in indices]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as ex:
        futures = {ex.submit(_run_one_model, m, shuffled_urls): m for m in models}
        results = []
        for fut in concurrent.futures.as_completed(futures):
            mres = fut.result()
            if mres["ok"]:
                u = mres["result"].get("usage") or {}
                fr = mres["result"].get("finish_reason") or ""
                length_note = "  [HIT MAX_TOKENS]" if fr == "length" else ""
                tok_note = (f"in={u.get('prompt_tokens','?'):>5} "
                            f"out={u.get('completion_tokens','?'):>5}")
                status = f"OK   {tok_note}{length_note}"
            else:
                status = f"ERR ({mres['error'][:80]})"
            print(f"  {mres['model']:45s} {mres['elapsed_s']:>6.1f}s  {status}")
            results.append(mres)

    # Preserve user-provided model order in the report
    results.sort(key=lambda r: models.index(r["model"]))

    html_str = render_html(items_in_shuffled_order, results)
    args.out.write_text(html_str, encoding="utf-8")
    print(f"\nWrote {args.out}")
    if args.open:
        webbrowser.open(args.out.as_uri())


if __name__ == "__main__":
    main()
