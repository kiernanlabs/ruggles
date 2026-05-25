"""Render a single-file HTML report of the rankings DB.

One report.html file with all data embedded as JSON. Clicking any thumbnail
opens a modal showing every comparison that piece was in — model used, the
4 other pieces in the group with thumbnails + rationales, and the LLM's full
ranking. No per-piece HTML files, no HTTP server, no async fetch.

Candidate-image data URIs are extracted once to assets/<id>.jpg so the HTML
file itself stays compact.

Usage:
    python -m reddit_art_ranker.report
    python -m reddit_art_ranker.report --subreddit Watercolor --open
"""

import argparse
import base64
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


CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1100px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 24px; }
.banner { background: #fff5e6; border: 1px solid #ffd591; border-radius: 6px;
          padding: 10px 14px; margin: 16px 0; font-size: 14px; }
table.leaderboard { width: 100%; border-collapse: collapse; margin-top: 12px; }
table.leaderboard th, table.leaderboard td {
    text-align: left; padding: 10px 8px; border-bottom: 1px solid #eee;
    vertical-align: top;
}
table.leaderboard th { background: #fafafa; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.04em; color: #666; }
td.num { font-variant-numeric: tabular-nums; text-align: right; }
.thumb { width: 140px; height: 140px; object-fit: cover; border-radius: 4px;
         background: #eee; display: block; cursor: pointer;
         transition: transform 0.1s, box-shadow 0.1s; }
.thumb:hover { transform: scale(1.03); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.title { font-weight: 500; margin-bottom: 4px; }
.title a { color: #222; text-decoration: none; cursor: pointer; }
.title a:hover { color: #0366d6; text-decoration: underline; }
.links a { font-size: 12px; color: #0366d6; text-decoration: none;
           margin-right: 10px; }
.links a:hover { text-decoration: underline; }
.rank-pill { display: inline-block; min-width: 28px; padding: 4px 8px;
             border-radius: 12px; background: #0366d6; color: white;
             font-weight: 600; text-align: center; }
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

/* Modal */
.modal-backdrop { display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.6); z-index: 100;
    overflow-y: auto; padding: 40px 20px; }
.modal-backdrop.open { display: block; }
.modal { background: white; max-width: 1100px; margin: 0 auto;
    border-radius: 8px; padding: 24px 28px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.3); }
.modal-close { float: right; font-size: 24px; color: #999; cursor: pointer;
    background: none; border: none; padding: 0; line-height: 1; }
.modal-close:hover { color: #222; }
.detail-header { display: grid; grid-template-columns: 280px 1fr; gap: 24px;
    margin-bottom: 24px; align-items: start; }
.detail-thumb { width: 280px; max-height: 400px; object-fit: contain;
    border-radius: 6px; background: #f0f0f0; }
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
    margin-top: 12px; }
.stat-box { padding: 8px 12px; background: #f6f8fa; border-radius: 4px; }
.stat-label { font-size: 11px; color: #666; text-transform: uppercase;
    letter-spacing: 0.04em; }
.stat-val { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.comparison-card { border: 1px solid #eee; border-radius: 6px;
    padding: 12px 16px; margin-bottom: 14px; }
.comparison-head { font-size: 12px; color: #444; margin-bottom: 8px; }
.comparison-head .model {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    font-weight: 600; color: #222; }
.candidate-tag { display: inline-block; background: #0366d6; color: white;
    font-size: 10px; padding: 2px 6px; border-radius: 4px; margin-left: 6px; }
table.group { width: 100%; border-collapse: collapse; font-size: 13px; }
table.group td { padding: 6px 8px; border-bottom: 1px solid #f5f5f5;
    vertical-align: top; }
table.group td:first-child { width: 50px; text-align: center; }
table.group td.thumb-cell { width: 80px; }
table.group img.mini { width: 70px; height: 70px; object-fit: cover;
    border-radius: 3px; display: block; cursor: pointer; }
table.group img.mini:hover { transform: scale(1.05); }
table.group tr.this-piece td { background: #fff8e1; font-weight: 500; }
table.group .group-title a { color: #0366d6; text-decoration: none;
    cursor: pointer; }
table.group .group-title a:hover { text-decoration: underline; }
.overall-rat { font-size: 12px; color: #555; line-height: 1.4;
    background: #f6f8fa; padding: 8px 12px; border-radius: 4px;
    margin-top: 8px; font-style: italic; }
"""


JS = """
const __DATA__ = JSON.parse(document.getElementById('art-data').textContent);
const __CAND_IMGS__ = JSON.parse(document.getElementById('cand-imgs').textContent);
const piecesById = Object.fromEntries(__DATA__.pieces.map(p => [p.reddit_id, p]));
const compsById = Object.fromEntries(__DATA__.comparisons.map(c => [c.id, c]));

// Backfill candidate image_urls from the de-duped data URI map. Pieces with
// data: URI images store them once in __CAND_IMGS__ rather than inline N
// times in the embedded JSON; modal renderer + leaderboard both resolve via
// this map.
for (const p of __DATA__.pieces) {
  if (__CAND_IMGS__[p.reddit_id]) p.image_url = __CAND_IMGS__[p.reddit_id];
}

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

function statBox(label, val) {
  return `<div class="stat-box"><div class="stat-label">${esc(label)}</div>
          <div class="stat-val">${esc(val)}</div></div>`;
}

function buildModal(pieceId) {
  const p = piecesById[pieceId];
  if (!p) return '<div>Piece not found.</div>';
  const compIds = __DATA__.comp_index[pieceId] || [];
  const comps = compIds.map(id => compsById[id]).filter(Boolean);

  let flagBadge = '';
  if (p.n_not_art_flags >= 2) {
    flagBadge = `<span class="flag-badge">EXCLUDED · ${p.n_not_art_flags} not-art flags</span>`;
  } else if (p.n_not_art_flags === 1) {
    flagBadge = '<span class="flag-warn">1 not-art flag</span>';
  }
  const permaHtml = p.permalink
    ? `· <a href="${esc(p.permalink)}" target="_blank">view on reddit</a>` : '';
  const stats = [
    statBox('ELO', p.elo.toFixed(0)),
    statBox('Comparisons', p.n_comparisons),
    statBox('Seen in', `${comps.length} groups`),
    statBox('Upvotes', p.upvotes != null ? p.upvotes : '—'),
    statBox('Comments', p.num_comments != null ? p.num_comments : '—'),
    statBox('Upvote ratio',
      p.upvote_ratio != null ? p.upvote_ratio.toFixed(2) : '—'),
  ].join('');

  const header = `
    <div class="detail-header">
      <a href="${esc(p.image_url)}" target="_blank">
        <img class="detail-thumb" src="${esc(p.image_url)}" alt="">
      </a>
      <div>
        <h2 style="margin-top:0">${esc(p.title || '(no title)')} ${flagBadge}</h2>
        <div class="meta">
          reddit_id: <code>${esc(pieceId)}</code> · r/${esc(__DATA__.subreddit)}
          ${permaHtml}
        </div>
        <div class="stat-grid">${stats}</div>
      </div>
    </div>
  `;

  let cardsHtml = '';
  if (!comps.length) {
    cardsHtml = '<p style="color:#999;">This piece has not appeared in any comparison yet.</p>';
  } else {
    for (const c of comps) {
      const ratByPid = Object.fromEntries(
        (c.per_piece_rationales || []).map(x => [x.piece_id, x.rationale]));
      const nonRanked = (c.piece_ids || [])
        .filter(pid => !c.ranking.includes(pid));
      const candTag = c.candidate_id
        ? '<span class="candidate-tag">CANDIDATE INSERTION</span>' : '';

      let rows = '';
      c.ranking.forEach((pid, idx) => {
        const other = piecesById[pid] || {};
        const isThis = (pid === pieceId);
        const cls = isThis ? ' class="this-piece"' : '';
        const titleText = esc((other.title || pid).slice(0, 80));
        const titleHtml = isThis
          ? `<b>${titleText}</b> <span style="color:#888;font-weight:normal;">(this piece)</span>`
          : `<a onclick="openDetail('${esc(pid)}'); return false;">${titleText}</a>`;
        const onclick = isThis ? '' : `onclick="openDetail('${esc(pid)}')"`;
        rows += `
          <tr${cls}>
            <td><span class="rank-pill">${idx + 1}</span></td>
            <td class="thumb-cell">
              <img class="mini" src="${esc(other.image_url || '')}"
                   ${onclick} loading="lazy" alt="">
            </td>
            <td class="group-title">${titleHtml}</td>
            <td>${esc(ratByPid[pid] || '')}</td>
          </tr>`;
      });
      nonRanked.forEach(pid => {
        const other = piecesById[pid] || {};
        const isThis = (pid === pieceId);
        const cls = isThis ? ' class="this-piece"' : '';
        const titleText = esc((other.title || pid).slice(0, 80));
        const titleHtml = isThis
          ? `<b>${titleText}</b> <span style="color:#888;font-weight:normal;">(this piece)</span>`
          : `<a onclick="openDetail('${esc(pid)}'); return false;">${titleText}</a>`;
        const onclick = isThis ? '' : `onclick="openDetail('${esc(pid)}')"`;
        rows += `
          <tr${cls}>
            <td><span class="rank-pill" style="background:#c0392b;">NOT ART</span></td>
            <td class="thumb-cell">
              <img class="mini" src="${esc(other.image_url || '')}"
                   ${onclick} loading="lazy" alt="">
            </td>
            <td class="group-title">${titleHtml}</td>
            <td><em style="color:#999;">flagged as not-art in this group</em></td>
          </tr>`;
      });

      cardsHtml += `
        <div class="comparison-card">
          <div class="comparison-head">
            <span class="model">${esc(c.model)}</span>
            · ${esc(c.created_at)} ${candTag}
          </div>
          <table class="group"><tbody>${rows}</tbody></table>
          <div class="overall-rat">${esc(c.rationale || '(no overall rationale)')}</div>
        </div>`;
    }
  }

  return header + `<h3 class="section-h">All comparisons (${comps.length})</h3>` + cardsHtml;
}

function openDetail(pieceId) {
  const backdrop = document.getElementById('modal-backdrop');
  const modal = document.getElementById('modal-content');
  modal.innerHTML = buildModal(pieceId);
  backdrop.classList.add('open');
  // Scroll modal to top on open
  backdrop.scrollTop = 0;
}

function closeDetail() {
  document.getElementById('modal-backdrop').classList.remove('open');
}

// Click backdrop (but not modal itself) to close
document.addEventListener('DOMContentLoaded', () => {
  const backdrop = document.getElementById('modal-backdrop');
  backdrop.addEventListener('click', e => {
    if (e.target === backdrop) closeDetail();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDetail();
  });
  // Wire all thumbnails / title links with data-piece-id
  document.querySelectorAll('[data-piece-id]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      openDetail(el.dataset.pieceId);
    });
  });
  // Fill in candidate thumbnails from the de-duped data URI map
  document.querySelectorAll('[data-cand-id]').forEach(el => {
    const src = __CAND_IMGS__[el.dataset.candId];
    if (src) el.src = src;
  });
});
"""


def _extract_data_uris(pieces: list, assets_dir: Path) -> dict:
    """For each piece whose image_url is a data: URI, decode to a file in
    assets_dir. Returns {reddit_id: filename}."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    extracted = {}
    for p in pieces:
        url = p.get("image_url") or ""
        if not url.startswith("data:"):
            continue
        try:
            header, b64 = url.split(",", 1)
        except ValueError:
            continue
        ext = "jpg"
        if "image/png" in header:
            ext = "png"
        elif "image/webp" in header:
            ext = "webp"
        fname = f"{p['reddit_id']}.{ext}"
        path = assets_dir / fname
        if not path.exists():
            try:
                path.write_bytes(base64.b64decode(b64))
            except Exception:
                continue
        extracted[p["reddit_id"]] = fname
    return extracted


def _piece_row(rank: int, p: dict, cand_data_uris: dict) -> str:
    title = html.escape(p["title"] or "(no title)")
    permalink = html.escape(p["permalink"] or "")
    raw_url = p["image_url"] or ""
    image_url = html.escape(raw_url)
    piece_id = html.escape(p["reddit_id"])
    is_cand_inline = p["reddit_id"] in cand_data_uris

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

    permalink_link = (
        f'<a href="{permalink}" target="_blank">reddit</a>' if permalink else ""
    )

    # Candidate images come from the de-duped JS map; everyone else uses raw URL
    if is_cand_inline:
        img_attrs = f'data-cand-id="{piece_id}" data-piece-id="{piece_id}"'
        image_link_target = "#"  # JS-resolved
    else:
        img_attrs = f'src="{image_url}" data-piece-id="{piece_id}"'
        image_link_target = image_url

    return f"""
    <tr{row_class}>
      <td class="num">{rank}</td>
      <td>
        <img class="thumb" alt="" loading="lazy" {img_attrs}
             title="See all comparisons">
      </td>
      <td>
        <div class="title">
          <a data-piece-id="{piece_id}">{title}</a>{flag_html}
        </div>
        <div class="links">
          <a data-piece-id="{piece_id}">detail</a>
          {permalink_link}
          <a href="{image_link_target}" target="_blank">image</a>
        </div>
      </td>
      <td class="num">{p["elo"]:.0f}</td>
      <td class="num">{p["n_comparisons"]}</td>
      <td class="num">{p["upvotes"] if p["upvotes"] is not None else "—"}</td>
      <td class="num">{p["num_comments"] if p["num_comments"] is not None else "—"}</td>
      <td class="num">{(f"{p['upvote_ratio']:.2f}") if p["upvote_ratio"] is not None else "—"}</td>
    </tr>"""


def render(subreddit: str, assets_dir: Path | None = None) -> str:
    with db.connect() as conn:
        leaderboard_rows = [dict(r) for r in db.get_ratings(conn, subreddit)]
        comp_count = conn.execute(
            "SELECT COUNT(*) FROM comparisons WHERE subreddit = ?", (subreddit,)
        ).fetchone()[0]
        # All pieces (including candidates) for the modal data
        all_pieces_rows = conn.execute(
            """
            SELECT p.*, r.elo, r.n_comparisons, r.n_not_art_flags
            FROM pieces p JOIN ratings r ON r.reddit_id = p.reddit_id
            WHERE p.subreddit = ?
            """,
            (subreddit,),
        ).fetchall()
        all_pieces = [dict(r) for r in all_pieces_rows]

        # All comparisons (for modal data + index)
        comp_rows = conn.execute(
            """
            SELECT id, created_at, model, piece_ids_json, ranking_json,
                   rationale, per_piece_rationales_json, candidate_id
            FROM comparisons WHERE subreddit = ?
            ORDER BY id DESC
            """,
            (subreddit,),
        ).fetchall()

    if not leaderboard_rows:
        return f"<html><body><h1>No pieces in r/{subreddit}</h1></body></html>"

    # Candidate image_urls are typically data: URIs (extracted at insert time
    # from a local file). Each is ~1MB. Inlining them N times across the
    # leaderboard markup and embedded modal JSON would balloon the report.
    # Instead: collect them into a single JS map, render placeholder <img>
    # tags everywhere, and have JS swap in the right data URI at load time.
    cand_data_uris = {
        p["reddit_id"]: (p["image_url"] or "")
        for p in all_pieces
        if (p["image_url"] or "").startswith("data:")
    }
    # Strip data URIs from the embedded JSON pieces so we don't duplicate.
    # The JS will backfill image_url from __CAND_IMGS__ at load time.
    for p in all_pieces:
        if p["reddit_id"] in cand_data_uris:
            p["image_url"] = ""
    for r in leaderboard_rows:
        if r["reddit_id"] in cand_data_uris:
            r["image_url"] = ""

    # Build the embedded JSON payload for the modal
    pieces_payload = [
        {
            "reddit_id": p["reddit_id"],
            "title": p["title"],
            "image_url": p["image_url"],
            "permalink": p["permalink"],
            "upvotes": p["upvotes"],
            "num_comments": p["num_comments"],
            "upvote_ratio": (float(p["upvote_ratio"])
                             if p["upvote_ratio"] is not None else None),
            "elo": float(p["elo"]),
            "n_comparisons": int(p["n_comparisons"]),
            "n_not_art_flags": int(p["n_not_art_flags"]),
            "is_candidate": int(p["is_candidate"]),
        }
        for p in all_pieces
    ]

    comparisons_payload = []
    comp_index: dict = {}
    for c in comp_rows:
        piece_ids = json.loads(c["piece_ids_json"])
        ranking = json.loads(c["ranking_json"])
        per_piece = (json.loads(c["per_piece_rationales_json"])
                     if c["per_piece_rationales_json"] else [])
        comparisons_payload.append({
            "id": c["id"],
            "created_at": c["created_at"],
            "model": c["model"],
            "piece_ids": piece_ids,
            "ranking": ranking,
            "rationale": c["rationale"] or "",
            "per_piece_rationales": per_piece,
            "candidate_id": c["candidate_id"],
        })
        for pid in piece_ids:
            comp_index.setdefault(pid, []).append(c["id"])

    data_json = json.dumps(
        {
            "subreddit": subreddit,
            "pieces": pieces_payload,
            "comparisons": comparisons_payload,
            "comp_index": comp_index,
        },
        ensure_ascii=False, separators=(",", ":"),
    )

    no_llm_banner = ""
    if comp_count == 0:
        no_llm_banner = (
            '<div class="banner"><b>No LLM rankings have been run yet.</b> '
            "Every piece is at the 1500 ELO baseline; row order is just SQLite's "
            "insertion order (no actual ranking).</div>"
        )

    rows_html = "".join(_piece_row(i + 1, r, cand_data_uris)
                        for i, r in enumerate(leaderboard_rows))
    cand_imgs_json = json.dumps(cand_data_uris).replace("</", "<\\/")
    pool_n = sum(1 for r in leaderboard_rows if not r["is_candidate"])
    cand_n = sum(1 for r in leaderboard_rows if r["is_candidate"])
    excluded_n = sum(1 for r in leaderboard_rows if (r.get("n_not_art_flags") or 0) >= 2)
    title_str = f"r/{subreddit} — art-ranker report"

    # Escape </script> sequence inside the JSON payload to be HTML-safe
    safe_data_json = data_json.replace("</", "<\\/")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title_str}</title>
<style>{CSS}</style></head><body>
<h1>{title_str}</h1>
<div class="meta">
  {pool_n} pool pieces ({excluded_n} excluded as not-art) · {cand_n} candidate(s)
  · {comp_count} LLM comparison(s) logged
  · generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
  · <em>click any thumbnail or title to see all comparisons that piece was in</em>
</div>

{no_llm_banner}

<h2 class="section-h">Full leaderboard (by ELO)</h2>
<table class="leaderboard">
  <thead><tr>
    <th>#</th><th>Image</th><th>Title / links</th>
    <th>ELO</th><th>N</th><th>Score</th><th>Cmts</th><th>Ratio</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>

<div class="modal-backdrop" id="modal-backdrop">
  <div class="modal">
    <button class="modal-close" onclick="closeDetail()" title="Close (esc)">×</button>
    <div id="modal-content"></div>
  </div>
</div>

<script id="art-data" type="application/json">{safe_data_json}</script>
<script id="cand-imgs" type="application/json">{cand_imgs_json}</script>
<script>{JS}</script>

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

    html_str = render(args.subreddit)
    args.out.write_text(html_str, encoding="utf-8")
    size_mb = args.out.stat().st_size / 1024 / 1024
    print(f"Wrote {args.out}  ({size_mb:.1f} MB)")

    if args.open:
        webbrowser.open(args.out.as_uri())


if __name__ == "__main__":
    main()
