"""Render a standalone HTML report of the current state of the rankings DB.

Shows every piece in the pool with thumbnail, ELO, n_comparisons, reddit score,
and the latest LLM ranking (if any comparisons have been logged). Regenerate
after each rank/insert run to refresh.

Usage:
    python -m reddit_art_ranker.report
    python -m reddit_art_ranker.report --subreddit Watercolor --open
"""

import argparse
import datetime as dt
import html
import json
import webbrowser
from pathlib import Path

from . import db
from .config import MODULE_DIR, SUBREDDIT

CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1100px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 24px; }
.banner { background: #fff5e6; border: 1px solid #ffd591; border-radius: 6px;
          padding: 10px 14px; margin: 16px 0; font-size: 14px; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #eee;
         vertical-align: top; }
th { background: #fafafa; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.04em; color: #666; }
td.num { font-variant-numeric: tabular-nums; text-align: right; }
.thumb { width: 140px; height: 140px; object-fit: cover; border-radius: 4px;
         background: #eee; display: block; }
.title { font-weight: 500; margin-bottom: 4px; }
.links a { font-size: 12px; color: #0366d6; text-decoration: none;
           margin-right: 10px; }
.links a:hover { text-decoration: underline; }
.rank-pill { display: inline-block; min-width: 28px; padding: 4px 8px;
             border-radius: 12px; background: #0366d6; color: white;
             font-weight: 600; text-align: center; }
.rationale { background: #f6f8fa; border-left: 3px solid #0366d6;
             padding: 12px 16px; border-radius: 4px; margin: 16px 0;
             font-size: 14px; line-height: 1.5; }
.section-h { margin-top: 32px; padding-bottom: 6px;
             border-bottom: 2px solid #eee; }
.candidate-row td { background: #f0f8ff; }
.excluded-row td { background: #fafafa; opacity: 0.75; }
.flag-badge { display: inline-block; background: #c0392b; color: white;
              font-size: 11px; padding: 2px 6px; border-radius: 8px;
              margin-left: 6px; font-weight: 600; }
.flag-warn { display: inline-block; background: #f39c12; color: white;
             font-size: 11px; padding: 2px 6px; border-radius: 8px;
             margin-left: 6px; font-weight: 600; }
.piece-rationale { font-size: 13px; color: #555; line-height: 1.4;
                   margin: 4px 0 6px; font-style: italic; }
"""


def _fmt_date(ts):
    if not ts:
        return "—"
    try:
        return dt.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "—"


def _piece_row(rank, p, rank_position=None, rationale=None):
    title = html.escape(p["title"] or "(no title)")
    permalink = html.escape(p["permalink"] or "")
    image_url = html.escape(p["image_url"] or "")
    rank_cell = (
        f'<span class="rank-pill">{rank_position}</span>'
        if rank_position is not None else "—"
    )
    permalink_link = (
        f'<a href="{permalink}" target="_blank">reddit</a>' if permalink else ""
    )
    classes = []
    if p["is_candidate"]:
        classes.append("candidate-row")
    flags = int(p.get("n_not_art_flags") or 0)
    if flags >= 2:
        classes.append("excluded-row")
    row_class = f' class="{" ".join(classes)}"' if classes else ""

    flag_html = ""
    if flags >= 2:
        flag_html = f'<span class="flag-badge">EXCLUDED · {flags} not-art flags</span>'
    elif flags == 1:
        flag_html = '<span class="flag-warn">1 not-art flag</span>'

    rationale_html = (
        f'<div class="piece-rationale">{html.escape(rationale)}</div>'
        if rationale else ""
    )
    return f"""
    <tr{row_class}>
      <td class="num">{rank}</td>
      <td>{rank_cell}</td>
      <td><a href="{image_url}" target="_blank">
            <img class="thumb" src="{image_url}" alt="" loading="lazy"></a></td>
      <td>
        <div class="title">{title}{flag_html}</div>
        {rationale_html}
        <div class="links">
          {permalink_link}
          <a href="{image_url}" target="_blank">image</a>
        </div>
      </td>
      <td class="num">{p["elo"]:.0f}</td>
      <td class="num">{p["n_comparisons"]}</td>
      <td class="num">{p["upvotes"] if p["upvotes"] is not None else "—"}</td>
      <td class="num">{p["num_comments"] if p["num_comments"] is not None else "—"}</td>
      <td class="num">{(f"{p['upvote_ratio']:.2f}") if p["upvote_ratio"] is not None else "—"}</td>
    </tr>"""


def render(subreddit: str) -> str:
    with db.connect() as conn:
        rows = [dict(r) for r in db.get_ratings(conn, subreddit)]
        comp_count = conn.execute(
            "SELECT COUNT(*) FROM comparisons WHERE subreddit = ?", (subreddit,)
        ).fetchone()[0]
        latest = conn.execute(
            """SELECT created_at, model, piece_ids_json, ranking_json, rationale,
                      per_piece_rationales_json, candidate_id
               FROM comparisons WHERE subreddit = ?
               ORDER BY id DESC LIMIT 1""",
            (subreddit,),
        ).fetchone()
        # full piece info for the latest comparison (we want titles/images)
        latest_pieces_by_id = {}
        if latest:
            ids = json.loads(latest["piece_ids_json"])
            placeholders = ",".join("?" * len(ids))
            for r in conn.execute(
                f"""SELECT p.*, r.elo, r.n_comparisons
                    FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
                    WHERE p.reddit_id IN ({placeholders})""",
                ids,
            ).fetchall():
                latest_pieces_by_id[r["reddit_id"]] = dict(r)

    if not rows:
        return f"<html><body><h1>No pieces in r/{subreddit}</h1></body></html>"

    no_llm_banner = ""
    if comp_count == 0:
        no_llm_banner = (
            '<div class="banner"><b>No LLM rankings have been run yet.</b> '
            "Every piece is at the 1500 ELO baseline and n_comparisons is 0, so "
            "the row order below is just SQLite's tie-break (insertion order) — "
            "<em>not</em> an actual ranking. Run "
            "<code>python -m reddit_art_ranker.rank</code> once the OpenAI "
            "account has quota.</div>"
        )

    leaderboard_rows = "".join(
        _piece_row(i + 1, r) for i, r in enumerate(rows)
    )

    latest_section = ""
    if latest:
        ranked_ids = json.loads(latest["ranking_json"])
        rationale = html.escape(latest["rationale"] or "")
        when = html.escape(latest["created_at"])
        model = html.escape(latest["model"])
        is_insert = " — candidate insertion" if latest["candidate_id"] else ""
        per_piece_by_id = {}
        if latest["per_piece_rationales_json"]:
            for item in json.loads(latest["per_piece_rationales_json"]):
                per_piece_by_id[item["piece_id"]] = item["rationale"]
        latest_rows_html = ""
        for pos, pid in enumerate(ranked_ids, 1):
            p = latest_pieces_by_id.get(pid)
            if not p:
                continue
            latest_rows_html += _piece_row(
                pos, p, rank_position=pos, rationale=per_piece_by_id.get(pid)
            )
        latest_section = f"""
        <h2 class="section-h">Latest LLM judgment{is_insert}</h2>
        <div class="meta">{when} · model {model}</div>
        <div class="rationale">{rationale or '(no rationale)'}</div>
        <table>
          <thead><tr>
            <th>#</th><th>LLM rank</th><th>Image</th><th>Title / links</th>
            <th>ELO</th><th>N</th><th>Score</th><th>Cmts</th><th>Ratio</th>
          </tr></thead>
          <tbody>{latest_rows_html}</tbody>
        </table>
        """

    pool_n = sum(1 for r in rows if not r["is_candidate"])
    cand_n = sum(1 for r in rows if r["is_candidate"])
    excluded_n = sum(1 for r in rows if (r.get("n_not_art_flags") or 0) >= 2)
    title_str = f"r/{subreddit} — art-ranker report"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title_str}</title>
<style>{CSS}</style></head><body>
<h1>{title_str}</h1>
<div class="meta">
  {pool_n} pool pieces ({excluded_n} excluded as not-art) · {cand_n} candidate(s)
  · {comp_count} LLM comparison(s) logged
  · generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>

{no_llm_banner}

{latest_section}

<h2 class="section-h">Full leaderboard (by ELO)</h2>
<table>
  <thead><tr>
    <th>#</th><th>Rank</th><th>Image</th><th>Title / links</th>
    <th>ELO</th><th>N</th><th>Score</th><th>Cmts</th><th>Ratio</th>
  </tr></thead>
  <tbody>{leaderboard_rows}</tbody>
</table>

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--out", type=Path,
                        default=Path(MODULE_DIR) / "report.html")
    parser.add_argument("--open", action="store_true",
                        help="Open the report in the default browser after writing")
    args = parser.parse_args()
    args.out.write_text(render(args.subreddit), encoding="utf-8")
    print(f"Wrote {args.out}")
    if args.open:
        webbrowser.open(args.out.as_uri())


if __name__ == "__main__":
    main()
