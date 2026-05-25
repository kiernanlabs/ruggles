"""Generate an HTML report comparing two snapshotted model runs.

Surfaces: summary metrics (Spearman, engagement correlations, not-art counts),
biggest rank movers (with thumbnails + each model's representative per-piece
rationale), consensus picks (both models loved / hated), and not-art flagging
differences.

Usage:
    python -m reddit_art_ranker.compare_report --run-a 1 --run-b 2
    python -m reddit_art_ranker.compare_report --run-a 1 --run-b 2 --open
"""

import argparse
import datetime as dt
import html
import json
import sys
import webbrowser
from pathlib import Path

from . import db
from .config import MODULE_DIR, SUBREDDIT

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _spearman(xs: list, ys: list) -> float | None:
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
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = (sum((a - mx) ** 2 for a in rx)) ** 0.5
    dy = (sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / (dx * dy) if dx and dy else None


def _load_run(conn, run_id: int) -> tuple[dict, dict]:
    meta = conn.execute("SELECT * FROM model_runs WHERE id = ?", (run_id,)).fetchone()
    if not meta:
        raise SystemExit(f"run_id {run_id} not found")
    ratings = {}
    for r in conn.execute(
        "SELECT reddit_id, elo, n_comparisons, n_not_art_flags "
        "FROM model_run_ratings WHERE run_id = ?", (run_id,),
    ).fetchall():
        ratings[r["reddit_id"]] = {
            "elo": float(r["elo"]),
            "n_comparisons": int(r["n_comparisons"]),
            "n_not_art_flags": int(r["n_not_art_flags"]),
        }
    return dict(meta), ratings


def _last_rationale_by_model(conn, reddit_id: str, model: str) -> tuple[str, str]:
    """Return (rationale_text, comparison_timestamp) for the most recent
    comparison of `model` that included this piece and assigned it a rationale.
    Returns ("", "") if none."""
    pattern = f'%"{reddit_id}"%'
    rows = conn.execute(
        """
        SELECT created_at, per_piece_rationales_json
        FROM comparisons
        WHERE model = ? AND piece_ids_json LIKE ?
        ORDER BY id DESC
        """,
        (model, pattern),
    ).fetchall()
    for r in rows:
        if not r["per_piece_rationales_json"]:
            continue
        try:
            items = json.loads(r["per_piece_rationales_json"])
        except json.JSONDecodeError:
            continue
        for item in items:
            if item.get("piece_id") == reddit_id and item.get("rationale"):
                return item["rationale"], r["created_at"]
    return "", ""


CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1200px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 6px; }
h2 { margin-top: 32px; padding-bottom: 6px; border-bottom: 2px solid #eee; }
.meta { color: #666; font-size: 13px; margin-bottom: 24px; }
.model-key { display: flex; gap: 24px; margin-bottom: 24px; flex-wrap: wrap; }
.model-key .chip { padding: 10px 14px; border-radius: 6px;
                   background: #f6f8fa; }
.model-key .chip .label { font-size: 11px; color: #666; text-transform: uppercase;
                          letter-spacing: 0.04em; }
.model-key .chip .name { font-family: ui-monospace, Menlo, Consolas, monospace;
                         font-weight: 600; font-size: 15px; }
.chip.A { border-left: 4px solid #8e44ad; }
.chip.B { border-left: 4px solid #16a085; }

table.metrics { border-collapse: collapse; margin-top: 12px; font-size: 14px; }
table.metrics th, table.metrics td { padding: 8px 14px; border-bottom: 1px solid #eee;
                                      text-align: left; }
table.metrics th { background: #fafafa; font-size: 12px; text-transform: uppercase;
                   color: #666; letter-spacing: 0.04em; }
table.metrics td.num { font-variant-numeric: tabular-nums; text-align: right; }
.delta.pos { color: #16a085; font-weight: 600; }
.delta.neg { color: #c0392b; font-weight: 600; }

.mover-grid { display: grid; grid-template-columns: 220px 1fr; gap: 18px;
              margin-bottom: 22px; padding: 14px; border: 1px solid #eee;
              border-radius: 6px; }
.mover-thumb { width: 200px; height: 200px; object-fit: cover; border-radius: 4px;
               background: #eee; display: block; }
.mover-title { font-weight: 600; margin-bottom: 4px; font-size: 16px; }
.mover-meta { color: #555; font-size: 12px; margin-bottom: 12px; }
.mover-meta a { color: #0366d6; text-decoration: none; margin-right: 10px; }
.rank-row { display: grid; grid-template-columns: 80px 80px 80px 1fr; gap: 14px;
            margin-bottom: 10px; align-items: center; font-size: 13px; }
.rank-box { background: #f6f8fa; padding: 8px 10px; border-radius: 4px;
            text-align: center; font-variant-numeric: tabular-nums; }
.rank-box .label { font-size: 10px; color: #666; text-transform: uppercase; }
.rank-box .val { font-size: 17px; font-weight: 600; }
.rank-box.A { border-left: 3px solid #8e44ad; }
.rank-box.B { border-left: 3px solid #16a085; }
.rationale-row { padding: 8px 12px; border-radius: 4px; margin-top: 6px;
                 font-size: 13px; line-height: 1.4; }
.rationale-row.A { background: #f8f4fb; border-left: 3px solid #8e44ad; }
.rationale-row.B { background: #f0faf6; border-left: 3px solid #16a085; }
.rationale-row .label { font-family: ui-monospace, Menlo, Consolas, monospace;
                        font-size: 11px; color: #666; margin-bottom: 4px; }
.consensus-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
                  gap: 12px; margin-top: 12px; }
.consensus-card { padding: 8px; border: 1px solid #eee; border-radius: 4px; }
.consensus-card img { width: 100%; aspect-ratio: 1; object-fit: cover;
                      border-radius: 3px; background: #eee; }
.consensus-card .ranks { font-size: 11px; color: #666; margin-top: 6px;
                         font-variant-numeric: tabular-nums; }
.consensus-card .title { font-size: 12px; margin-top: 2px;
                         display: -webkit-box; -webkit-line-clamp: 2;
                         -webkit-box-orient: vertical; overflow: hidden; }
.exclusion-list { font-size: 13px; margin-top: 12px; }
.exclusion-list tr td { padding: 6px 10px; border-bottom: 1px solid #f0f0f0; }
.exclusion-list img.mini { width: 50px; height: 50px; object-fit: cover;
                           border-radius: 3px; vertical-align: middle; }
"""


def _fmt_delta(d: int) -> str:
    if d > 0:
        return f"<span class='delta neg'>+{d}</span>"
    elif d < 0:
        return f"<span class='delta pos'>{d}</span>"
    return f"{d}"


def _fmt_rho(rho: float | None) -> str:
    return f"{rho:+.3f}" if rho is not None else "n/a"


def render(meta_a: dict, rat_a: dict, meta_b: dict, rat_b: dict,
           pieces: dict, conn, top_n: int) -> str:
    eligible = [
        pid for pid in pieces
        if pid in rat_a and pid in rat_b
        and rat_a[pid]["n_not_art_flags"] < 2
        and rat_b[pid]["n_not_art_flags"] < 2
    ]
    excl_a = {pid for pid in pieces if pid in rat_a and rat_a[pid]["n_not_art_flags"] >= 2}
    excl_b = {pid for pid in pieces if pid in rat_b and rat_b[pid]["n_not_art_flags"] >= 2}

    # Correlations
    elos_a = [rat_a[pid]["elo"] for pid in eligible]
    elos_b = [rat_b[pid]["elo"] for pid in eligible]
    upvotes = [pieces[pid]["upvotes"] for pid in eligible]
    ratios = [pieces[pid]["upvote_ratio"] for pid in eligible]
    comments = [pieces[pid]["num_comments"] for pid in eligible]

    rho_models = _spearman(elos_a, elos_b)
    rho_a_up = _spearman(elos_a, upvotes)
    rho_b_up = _spearman(elos_b, upvotes)
    rho_a_ratio = _spearman(elos_a, ratios)
    rho_b_ratio = _spearman(elos_b, ratios)
    rho_a_cmt = _spearman(elos_a, comments)
    rho_b_cmt = _spearman(elos_b, comments)

    # Rank by model (among eligible)
    rank_a = {pid: i + 1 for i, pid in enumerate(
        sorted(eligible, key=lambda p: -rat_a[p]["elo"]))}
    rank_b = {pid: i + 1 for i, pid in enumerate(
        sorted(eligible, key=lambda p: -rat_b[p]["elo"]))}

    movers = [(pid, rank_a[pid], rank_b[pid], rank_b[pid] - rank_a[pid])
              for pid in eligible]
    movers.sort(key=lambda x: -abs(x[3]))
    top_movers = movers[:top_n]

    # Consensus: pieces in top-10 of both rankings
    top10_a = set(sorted(eligible, key=lambda p: -rat_a[p]["elo"])[:10])
    top10_b = set(sorted(eligible, key=lambda p: -rat_b[p]["elo"])[:10])
    consensus_top = sorted(top10_a & top10_b,
                           key=lambda p: (rank_a[p] + rank_b[p]) / 2)
    bottom10_a = set(sorted(eligible, key=lambda p: rat_a[p]["elo"])[:10])
    bottom10_b = set(sorted(eligible, key=lambda p: rat_b[p]["elo"])[:10])
    consensus_bottom = sorted(bottom10_a & bottom10_b,
                              key=lambda p: -(rank_a[p] + rank_b[p]) / 2)

    # Not-art exclusion differences
    only_b_excluded = sorted(
        [pid for pid in (excl_b - excl_a)],
        key=lambda p: -(pieces[p]["upvotes"] or 0),
    )
    only_a_excluded = sorted(
        [pid for pid in (excl_a - excl_b)],
        key=lambda p: -(pieces[p]["upvotes"] or 0),
    )

    # Build sections
    name_a = html.escape(meta_a["model"])
    name_b = html.escape(meta_b["model"])

    metrics_table = f"""
    <table class="metrics">
      <thead><tr>
        <th>Metric</th>
        <th class="num">Model A<br><small>{name_a}</small></th>
        <th class="num">Model B<br><small>{name_b}</small></th>
        <th class="num">Δ (B − A)</th>
      </tr></thead>
      <tbody>
        <tr><td>Pieces excluded as not-art (≥2 flags)</td>
            <td class="num">{len(excl_a)}</td>
            <td class="num">{len(excl_b)}</td>
            <td class="num">{_fmt_delta(len(excl_b) - len(excl_a))}</td></tr>
        <tr><td>Spearman ρ vs upvotes</td>
            <td class="num">{_fmt_rho(rho_a_up)}</td>
            <td class="num">{_fmt_rho(rho_b_up)}</td>
            <td class="num">{_fmt_rho((rho_b_up or 0) - (rho_a_up or 0))}</td></tr>
        <tr><td>Spearman ρ vs upvote_ratio</td>
            <td class="num">{_fmt_rho(rho_a_ratio)}</td>
            <td class="num">{_fmt_rho(rho_b_ratio)}</td>
            <td class="num">{_fmt_rho((rho_b_ratio or 0) - (rho_a_ratio or 0))}</td></tr>
        <tr><td>Spearman ρ vs num_comments</td>
            <td class="num">{_fmt_rho(rho_a_cmt)}</td>
            <td class="num">{_fmt_rho(rho_b_cmt)}</td>
            <td class="num">{_fmt_rho((rho_b_cmt or 0) - (rho_a_cmt or 0))}</td></tr>
        <tr><td colspan="4" style="text-align:center; background:#f6f8fa;">
            <b>Model agreement (A vs B):</b>
            Spearman ρ = <code>{_fmt_rho(rho_models)}</code>
            · {len(eligible)} pieces eligible in both
        </td></tr>
      </tbody>
    </table>
    """

    # Top movers — load rationale samples lazily for this set
    mover_cards = []
    for pid, ra, rb, delta in top_movers:
        p = pieces[pid]
        rat_a_text, rat_a_when = _last_rationale_by_model(conn, pid, meta_a["model"])
        rat_b_text, rat_b_when = _last_rationale_by_model(conn, pid, meta_b["model"])

        title = html.escape(p["title"] or "(no title)")
        permalink = html.escape(p["permalink"] or "")
        image_url = html.escape(p["image_url"] or "")
        permalink_html = (
            f'<a href="{permalink}" target="_blank">reddit</a>' if permalink else ""
        )

        if delta < 0:
            verdict = (f"B liked it <b>{abs(delta)}</b> spots more than A")
        elif delta > 0:
            verdict = (f"A liked it <b>{abs(delta)}</b> spots more than B")
        else:
            verdict = "same rank"

        rat_a_html = (
            f'<div class="rationale-row A">'
            f'<div class="label">A · {name_a}'
            f'{" · " + html.escape(rat_a_when[:19]) if rat_a_when else ""}</div>'
            f'{html.escape(rat_a_text) if rat_a_text else "<em style=color:#999>no rationale captured</em>"}'
            f'</div>'
        )
        rat_b_html = (
            f'<div class="rationale-row B">'
            f'<div class="label">B · {name_b}'
            f'{" · " + html.escape(rat_b_when[:19]) if rat_b_when else ""}</div>'
            f'{html.escape(rat_b_text) if rat_b_text else "<em style=color:#999>no rationale captured</em>"}'
            f'</div>'
        )
        upvotes_str = f"{p['upvotes']} upvotes" if p["upvotes"] is not None else "—"
        mover_cards.append(f"""
        <div class="mover-grid">
            <div>
                <a href="{image_url}" target="_blank">
                    <img class="mover-thumb" src="{image_url}" alt="" loading="lazy">
                </a>
            </div>
            <div>
                <div class="mover-title">{title}</div>
                <div class="mover-meta">
                    <code>{html.escape(pid)}</code> · {upvotes_str} ·
                    {permalink_html}
                    · <em>{verdict}</em>
                </div>
                <div class="rank-row">
                    <div class="rank-box A"><div class="label">A rank</div>
                        <div class="val">{ra}</div></div>
                    <div class="rank-box B"><div class="label">B rank</div>
                        <div class="val">{rb}</div></div>
                    <div class="rank-box"><div class="label">Δ</div>
                        <div class="val">{_fmt_delta(delta)}</div></div>
                    <div style="font-size:11px;color:#888;">
                        of {len(eligible)} eligible pieces
                    </div>
                </div>
                {rat_a_html}
                {rat_b_html}
            </div>
        </div>
        """)

    movers_html = "".join(mover_cards)

    def _consensus_cards(piece_ids: list, label: str) -> str:
        if not piece_ids:
            return f"<p style='color:#999;'>No pieces in {label}.</p>"
        cards = []
        for pid in piece_ids:
            p = pieces[pid]
            cards.append(f"""
            <div class="consensus-card">
                <a href="{html.escape(p['permalink'] or '#')}" target="_blank">
                    <img src="{html.escape(p['image_url'] or '')}" alt="" loading="lazy">
                </a>
                <div class="ranks">A:#{rank_a[pid]} · B:#{rank_b[pid]} ·
                    {p['upvotes'] or 0}↑</div>
                <div class="title">{html.escape((p['title'] or '')[:60])}</div>
            </div>""")
        return f'<div class="consensus-grid">{"".join(cards)}</div>'

    consensus_top_html = _consensus_cards(consensus_top, "top-10 intersection")
    consensus_bottom_html = _consensus_cards(consensus_bottom, "bottom-10 intersection")

    def _exclusion_rows(piece_ids: list, model_label: str, limit: int = 20) -> str:
        if not piece_ids:
            return f"<p style='color:#999;'>(none)</p>"
        rows = []
        for pid in piece_ids[:limit]:
            p = pieces[pid]
            rows.append(f"""
            <tr>
                <td><img class="mini" src="{html.escape(p['image_url'] or '')}" alt=""></td>
                <td><code>{html.escape(pid)}</code></td>
                <td>{p['upvotes'] or 0}</td>
                <td><a href="{html.escape(p['permalink'] or '#')}" target="_blank">
                    {html.escape((p['title'] or '')[:80])}</a></td>
            </tr>""")
        suffix = (f"<p style='color:#999;font-size:12px;'>… and "
                  f"{len(piece_ids) - limit} more</p>" if len(piece_ids) > limit else "")
        return f"""<table class="exclusion-list">
        <thead><tr><th></th><th>id</th><th>upvotes</th><th>title</th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table>{suffix}"""

    only_b_html = _exclusion_rows(only_b_excluded, "B")
    only_a_html = _exclusion_rows(only_a_excluded, "A")

    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    title_str = f"Model comparison · {name_a} vs {name_b}"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title_str}</title>
<style>{CSS}</style></head><body>
<h1>{title_str}</h1>
<div class="meta">
  Generated {timestamp} · subreddit r/{html.escape(meta_a['subreddit'])} ·
  {len(eligible)} pieces eligible in both runs (excluded by either: {len(excl_a | excl_b)})
</div>

<div class="model-key">
  <div class="chip A">
    <div class="label">Model A</div>
    <div class="name">{name_a}</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">
      run #{meta_a['id']} · {html.escape(meta_a['label'] or '')} ·
      excl={len(excl_a)}
    </div>
  </div>
  <div class="chip B">
    <div class="label">Model B</div>
    <div class="name">{name_b}</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">
      run #{meta_b['id']} · {html.escape(meta_b['label'] or '')} ·
      excl={len(excl_b)}
    </div>
  </div>
</div>

<h2>Summary metrics</h2>
{metrics_table}

<h2>Where they agree — top 10 ∩ top 10</h2>
{consensus_top_html}

<h2>Where they agree — bottom 10 ∩ bottom 10</h2>
{consensus_bottom_html}

<h2>Biggest disagreements (top {len(top_movers)} rank movers)</h2>
<p style="font-size:13px;color:#666;">
Negative Δ = B (gemini) liked it more than A. Positive Δ = A (gpt) liked it more.
Each piece shows both models' representative rationale (most recent comparison
that included this piece).
</p>
{movers_html}

<h2>Excluded by {name_b} but not {name_a}</h2>
<p style="font-size:13px;color:#666;">{len(only_b_excluded)} pieces.
These triggered ≥2 not-art flags from {name_b} but {name_a} kept them in the pool.
Sorted by upvotes (highest first).</p>
{only_b_html}

<h2>Excluded by {name_a} but not {name_b}</h2>
<p style="font-size:13px;color:#666;">{len(only_a_excluded)} pieces.</p>
{only_a_html}

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-a", type=int, required=True)
    parser.add_argument("--run-b", type=int, required=True)
    parser.add_argument("--top", type=int, default=25,
                        help="How many biggest rank movers to show")
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--out", type=Path,
                        default=Path(MODULE_DIR) / "compare_report.html")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    with db.connect() as conn:
        meta_a, rat_a = _load_run(conn, args.run_a)
        meta_b, rat_b = _load_run(conn, args.run_b)
        pieces = {r["reddit_id"]: dict(r) for r in conn.execute(
            """SELECT reddit_id, title, upvotes, num_comments, upvote_ratio,
                      permalink, image_url FROM pieces WHERE subreddit = ?""",
            (args.subreddit,),
        ).fetchall()}
        html_str = render(meta_a, rat_a, meta_b, rat_b, pieces, conn, args.top)

    args.out.write_text(html_str, encoding="utf-8")
    size_kb = args.out.stat().st_size / 1024
    print(f"Wrote {args.out}  ({size_kb:.0f} KB)")
    if args.open:
        webbrowser.open(args.out.as_uri())


if __name__ == "__main__":
    main()
