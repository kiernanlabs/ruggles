"""Generate an HTML side-by-side comparison of the new gpt-5-4 evaluations
(from a CSV produced by evaluate_images.py) against evaluations currently
stored in the ruggles_artworks_prod DynamoDB table.

Stored rows are matched by image_url — so if the same image has been evaluated
multiple times, all stored evaluations are shown stacked in the "Stored" column.
"""
import html
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

CSV_PATH = Path("reports/evaluation_gpt-5-4_20260418_145755.csv")
OUT_PATH = Path("reports/comparison_gpt-5-4_vs_stored.html")
TABLE_NAME = "ruggles_artworks_prod"
NEW_MODEL_LABEL = "gpt-5-4"

CRITERIA = [
    ("proportion_and_structure", "Proportion & Structure", "proportion"),
    ("line_quality", "Line Quality", "line_quality"),
    ("value_and_light", "Value & Light", "value_light"),
    ("detail_and_texture", "Detail & Texture", "detail_texture"),
    ("composition_and_perspective", "Composition & Perspective", "composition_perspective"),
    ("form_and_volume", "Form & Volume", "form_volume"),
    ("mood_and_expression", "Mood & Expression", "mood_expression"),
    ("overall_realism", "Overall Realism", "overall_realism"),
]


def _to_native(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, list):
        return [_to_native(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    return v


def load_stored_by_url() -> dict:
    """Return {image_url: [stored_item, ...]} for all artworks, newest-first."""
    table = boto3.resource(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    ).Table(TABLE_NAME)

    items = []
    kw = {
        "IndexName": "by_created_at",
        "KeyConditionExpression": Key("entity_type").eq("artwork"),
        "ScanIndexForward": False,
    }
    while True:
        r = table.query(**kw)
        items.extend(_to_native(x) for x in r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    by_url = defaultdict(list)
    for it in items:
        by_url[it.get("image_url", "")].append(it)
    for url in by_url:
        by_url[url].sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return dict(by_url)


def _parse_tips(cell) -> list:
    if cell is None or (isinstance(cell, float) and math.isnan(cell)):
        return []
    s = str(cell).strip()
    return [t.strip() for t in s.split(";") if t.strip()] if s else []


def _score_or_none(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _stored_avg(stored_item: dict) -> float | None:
    """Average of non-zero per-criterion scores for a stored item."""
    scores = []
    for _, _, flat in CRITERIA:
        s = stored_item.get(f"{flat}_score")
        if s:
            scores.append(s)
    return sum(scores) / len(scores) if scores else None


def build_criteria_rows(csv_row: pd.Series, stored_list: list) -> list:
    rows = []
    for pretty_key, title, flat in CRITERIA:
        stored_entries = []
        for s in stored_list:
            score = s.get(f"{flat}_score")
            rat = s.get(f"{flat}_rationale") or ""
            tips = s.get(f"{flat}_tips") or []
            if (score or 0) or rat or tips:
                stored_entries.append({
                    "score": score,
                    "rationale": rat,
                    "tips": tips,
                    "description": s.get("description") or "",
                    "created_at": (s.get("created_at") or "")[:10],
                    "evaluation_version": s.get("evaluation_version") or "",
                })

        new_score = _score_or_none(csv_row.get(f"new_{pretty_key}_score"))
        new_rat = csv_row.get(f"new_{pretty_key}_rationale")
        if isinstance(new_rat, float) and math.isnan(new_rat):
            new_rat = ""
        new_tips = _parse_tips(csv_row.get(f"new_{pretty_key}_tips"))

        has_new = (new_score or 0) or (new_rat or "").strip() or new_tips
        if not stored_entries and not has_new:
            continue

        rows.append({
            "title": title,
            "stored_entries": stored_entries,
            "new": {"score": new_score, "rationale": new_rat or "", "tips": new_tips},
        })
    return rows


def esc(s) -> str:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return ""
    return html.escape(str(s))


def render_diff_badge(stored_score, new_score) -> str:
    if stored_score is None or new_score is None:
        return '<span class="diff none">—</span>'
    d = new_score - stored_score
    cls = "positive" if d > 0 else ("negative" if d < 0 else "zero")
    sign = "+" if d > 0 else ""
    return f'<span class="diff {cls}">{sign}{d}</span>'


def render_tips(tips: list) -> str:
    if not tips:
        return '<p class="empty">No tips</p>'
    return "<ul>" + "".join(f"<li>{esc(t)}</li>" for t in tips) + "</ul>"


def render_stored_entry(e: dict, show_diff_against_new: int | None = None) -> str:
    meta_parts = []
    if e["description"]:
        meta_parts.append(f'Description: <i>&ldquo;{esc(e["description"])}&rdquo;</i>')
    if e["created_at"]:
        meta_parts.append(f'Evaluated: {esc(e["created_at"])}')
    if e["evaluation_version"]:
        meta_parts.append(f'Version: {esc(e["evaluation_version"])}')
    meta_html = f'<div class="stored-meta">{" · ".join(meta_parts)}</div>' if meta_parts else ""

    score_html = esc(e["score"]) if e["score"] is not None else "—"
    diff_html = ""
    if show_diff_against_new is not None and e["score"] is not None:
        diff_html = render_diff_badge(e["score"], show_diff_against_new)

    rat_html = esc(e["rationale"]) or '<span class="empty">No rationale</span>'
    return f"""
    <div class="stored-entry">
      {meta_html}
      <div class="cell-head"><span class="label">Stored</span><span class="score">{score_html}</span>{diff_html}</div>
      <p class="rationale">{rat_html}</p>
      {render_tips(e["tips"])}
    </div>"""


def render_stored_cell(entries: list, new_score) -> str:
    if not entries:
        return '<div class="cell existing"><p class="empty">No stored evaluation for this image.</p></div>'
    # When multiple entries, show an individual diff on each relative to the new score.
    show_individual = new_score is not None and len(entries) > 1
    inner = "".join(
        render_stored_entry(e, show_diff_against_new=new_score if show_individual else None)
        for e in entries
    )
    return f'<div class="cell existing">{inner}</div>'


def render_new_cell(new: dict) -> str:
    score = esc(new["score"]) if new["score"] is not None else "—"
    rat = esc(new["rationale"]) or '<span class="empty">No rationale</span>'
    return f"""
    <div class="cell new">
      <div class="cell-head"><span class="label">{esc(NEW_MODEL_LABEL)}</span><span class="score">{score}</span></div>
      <p class="rationale">{rat}</p>
      {render_tips(new["tips"])}
    </div>"""


def render_criterion(c: dict) -> str:
    # Top-level diff badge compares the MOST RECENT stored entry vs new (only when exactly 1 stored).
    header_diff = ""
    if len(c["stored_entries"]) == 1:
        header_diff = render_diff_badge(c["stored_entries"][0]["score"], c["new"]["score"])
    stored_cell = render_stored_cell(c["stored_entries"], c["new"]["score"])
    new_cell = render_new_cell(c["new"])
    return f"""
  <section class="criterion">
    <h3>{esc(c['title'])} {header_diff}</h3>
    <div class="row">
      {stored_cell}
      {new_cell}
    </div>
  </section>"""


def render_avg_line(stored_list: list, new_avg) -> str:
    parts = []
    for s in stored_list:
        avg = _stored_avg(s)
        if avg is None:
            continue
        label_bits = []
        if s.get("description"):
            label_bits.append(f'&ldquo;{esc(s["description"])}&rdquo;')
        if s.get("created_at"):
            label_bits.append(esc(s["created_at"][:10]))
        label = " ".join(label_bits) or "stored"
        parts.append(f"<span>Stored {label}: <b>{avg:.1f}</b></span>")
    if pd.notna(new_avg):
        parts.append(f'<span>{esc(NEW_MODEL_LABEL)}: <b>{float(new_avg):.1f}</b></span>')
    if not parts:
        return ""
    return '<p class="avg">Average raw score — ' + " · ".join(parts) + "</p>"


def render_artwork(row: pd.Series, stored_list: list) -> str:
    title = esc(row.get("title", "Untitled"))
    artist = esc(row.get("artist_name", "Unknown"))
    sketch = esc(row.get("sketch_type", ""))
    created = esc(str(row.get("created_at", ""))[:10])
    generated_title = esc(row.get("generated_title", ""))
    image_url = esc(row.get("image_url", "") or (stored_list[0].get("image_url") if stored_list else ""))

    criteria_html = "\n".join(render_criterion(c) for c in build_criteria_rows(row, stored_list))
    avg_line = render_avg_line(stored_list, row.get("new_average_raw_score"))
    stored_count_note = ""
    if len(stored_list) > 1:
        stored_count_note = f'<p class="multi-note">{len(stored_list)} stored evaluations found for this image.</p>'

    return f"""
<article class="artwork">
  <header>
    <h2>{title}</h2>
    <p class="meta">Artist: <b>{artist}</b> · Type: <i>{sketch}</i> · Created: {created}</p>
    {f'<p class="gen">New title suggestion ({esc(NEW_MODEL_LABEL)}): <i>{generated_title}</i></p>' if generated_title else ''}
    {stored_count_note}
  </header>
  <div class="art-body">
    <img src="{image_url}" alt="{title}" loading="lazy">
    <div class="art-eval">
      {avg_line}
      {criteria_html}
    </div>
  </div>
</article>"""


CSS = """
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, Segoe UI, sans-serif; color: #222; margin: 0; background: #fafafa; }
main { max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }
header.page h1 { margin: 0 0 .25rem; font-size: 1.8rem; }
header.page p { color: #555; margin: 0; }
article.artwork { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px; margin: 1.5rem 0; overflow: hidden; }
article.artwork > header { padding: 1.25rem 1.5rem .5rem; border-bottom: 1px solid #eee; }
article.artwork h2 { margin: 0 0 .25rem; font-size: 1.3rem; }
.meta, .gen, .multi-note { color: #555; margin: .2rem 0; font-size: 0.92rem; }
.multi-note { color: #8a6d00; background: #fff7d6; display: inline-block; padding: .15rem .5rem; border-radius: 4px; }
.art-body { display: grid; grid-template-columns: 320px 1fr; gap: 1.5rem; padding: 1.25rem 1.5rem 1.75rem; align-items: start; }
.art-body img { width: 100%; border-radius: 8px; border: 1px solid #e3e3e3; position: sticky; top: 1rem; }
.avg { background: #f4f7ff; border: 1px solid #dce3f7; padding: .5rem .75rem; border-radius: 6px; margin: 0 0 1rem; display: flex; flex-wrap: wrap; gap: .25rem 1rem; }
.criterion { border-top: 1px solid #eee; padding: 1rem 0 .25rem; }
.criterion:first-of-type { border-top: none; padding-top: .25rem; }
.criterion h3 { margin: 0 0 .75rem; font-size: 1.08rem; display: flex; align-items: center; gap: .5rem; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.cell { border: 1px solid #e6e6e6; border-radius: 8px; padding: .75rem .85rem; background: #fcfcfc; }
.cell.new { background: #f7fff4; border-color: #d5ecd1; }
.cell-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: .35rem; gap: .5rem; }
.cell-head .label { font-weight: 600; color: #666; font-size: .85rem; text-transform: uppercase; letter-spacing: .02em; }
.cell-head .score { font-weight: 700; font-size: 1.15rem; margin-left: auto; }
.rationale { margin: .25rem 0 .5rem; font-size: .95rem; }
.cell ul { margin: .25rem 0 0; padding-left: 1.2rem; }
.cell li { margin: .2rem 0; font-size: .9rem; }
.stored-entry { padding-bottom: .75rem; }
.stored-entry + .stored-entry { border-top: 1px dashed #ddd; margin-top: .75rem; padding-top: .75rem; }
.stored-meta { font-size: .82rem; color: #777; margin-bottom: .3rem; }
.diff { display: inline-block; font-weight: 700; font-size: .85rem; padding: 2px 8px; border-radius: 99px; }
.diff.positive { background: #dff5d8; color: #1b6b1b; }
.diff.negative { background: #ffe1e1; color: #9b1b1b; }
.diff.zero { background: #ececec; color: #555; }
.diff.none { background: transparent; color: #999; font-weight: normal; }
.empty { color: #aaa; font-style: italic; }
@media (max-width: 760px) { .art-body { grid-template-columns: 1fr; } .row { grid-template-columns: 1fr; } .art-body img { position: static; } }
"""


def main() -> int:
    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}", file=sys.stderr)
        return 1
    df = pd.read_csv(CSV_PATH)
    by_url = load_stored_by_url()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_stored_matches = 0
    bodies = []
    for _, row in df.iterrows():
        stored_list = by_url.get(row.get("image_url", ""), [])
        total_stored_matches += len(stored_list)
        bodies.append(render_artwork(row, stored_list))

    body = "\n".join(bodies)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Evaluation Comparison — {esc(NEW_MODEL_LABEL)} vs stored</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
    <header class="page">
      <h1>Evaluation Comparison: {esc(NEW_MODEL_LABEL)} vs stored</h1>
      <p>{len(df)} artworks · {total_stored_matches} stored evaluation{'s' if total_stored_matches != 1 else ''} matched by image URL · generated {generated_at}. Stored evaluations come from DynamoDB (the <code>description</code> field often hints at which model was used, e.g. "o3 re-eval" vs "Standard evaluation v0").</p>
    </header>
    {body}
  </main>
</body>
</html>
"""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")
    print(f"  {len(df)} artworks, {total_stored_matches} stored matches by image_url")
    return 0


if __name__ == "__main__":
    sys.exit(main())
