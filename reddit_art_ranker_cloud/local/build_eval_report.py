"""Render eval_sample_results.json into a standalone HTML report:

  * per-piece cards (image, cloud rank/percentile, Streamlit avg score,
    consolidated jury feedback);
  * a comparison section relating the cloud insertion ranking to the
    Streamlit per-piece aggregate GPT score (scatter + rank table + Pearson
    & Spearman correlation).

Run from reddit_art_ranker_cloud/:  python -m local.build_eval_report
"""

import html
import json
import os

HERE = os.path.dirname(__file__)
IN_PATH = os.path.join(HERE, "eval_sample_results.json")
OUT_PATH = os.path.join(HERE, "eval_sample_report.html")


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _rank(vals):
    """Average-rank (handles ties) for Spearman."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs, ys):
    if len(xs) < 2:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _scatter_svg(pts, w=620, h=420, pad=60):
    """pts: list of (streamlit_avg 0-20, cloud_percentile 0-100, label)."""
    if not pts:
        return "<p>No data to plot.</p>"
    x0, x1 = 0, 20      # streamlit avg score domain
    y0, y1 = 0, 100     # cloud percentile domain
    iw, ih = w - 2 * pad, h - 2 * pad

    def sx(v):
        return pad + (v - x0) / (x1 - x0) * iw

    def sy(v):
        return h - pad - (v - y0) / (y1 - y0) * ih

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" '
             f'style="max-width:{w}px;font-family:inherit">']
    # axes
    parts.append(f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" '
                 'stroke="#999"/>')
    parts.append(f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" '
                 'stroke="#999"/>')
    # gridlines + labels
    for gx in range(0, 21, 5):
        X = sx(gx)
        parts.append(f'<line x1="{X}" y1="{pad}" x2="{X}" y2="{h-pad}" '
                     'stroke="#eee"/>')
        parts.append(f'<text x="{X}" y="{h-pad+18}" font-size="11" '
                     f'text-anchor="middle" fill="#666">{gx}</text>')
    for gy in range(0, 101, 20):
        Y = sy(gy)
        parts.append(f'<line x1="{pad}" y1="{Y}" x2="{w-pad}" y2="{Y}" '
                     'stroke="#eee"/>')
        parts.append(f'<text x="{pad-10}" y="{Y+4}" font-size="11" '
                     f'text-anchor="end" fill="#666">{gy}</text>')
    parts.append(f'<text x="{pad+iw/2}" y="{h-12}" font-size="13" '
                 'text-anchor="middle" fill="#333">Streamlit aggregate GPT '
                 'score (avg of criteria, 1–20)</text>')
    parts.append(f'<text x="18" y="{pad+ih/2}" font-size="13" '
                 f'text-anchor="middle" fill="#333" '
                 f'transform="rotate(-90 18 {pad+ih/2})">Cloud jury percentile '
                 '(learntodraw)</text>')
    # points
    for ax, ay, label in pts:
        if ax is None:
            continue
        parts.append(
            f'<circle cx="{sx(ax):.1f}" cy="{sy(ay):.1f}" r="6" '
            f'fill="#4a7" fill-opacity="0.65" stroke="#275" stroke-width="1">'
            f'<title>{html.escape(label)}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def main():
    with open(IN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    results = data["results"]

    # Correlation between Streamlit avg score and cloud percentile (scored only).
    scored = [r for r in results
              if not r["not_art"] and r["streamlit_avg_score"] is not None]
    xs = [r["streamlit_avg_score"] for r in scored]
    ys = [r["cloud_percentile"] for r in scored]
    pear = _pearson(xs, ys)
    spear = _spearman(xs, ys)
    pts = [(r["streamlit_avg_score"], r["cloud_percentile"],
            f'{r["title"]} — st {r["streamlit_avg_score"]}, pctl '
            f'{r["cloud_percentile"]}') for r in scored]

    def esc(s):
        return html.escape(str(s)) if s is not None else ""

    rows = ""
    for r in results:
        na = " 🚫" if r["not_art"] else ""
        st_avg = r["streamlit_avg_score"]
        st_pct_of_20 = f"{st_avg}/20" if st_avg is not None else "—"
        rows += f"""<tr>
          <td>{r['cloud_rank']}<span class="muted">/{r['cloud_of']}</span></td>
          <td>{r['cloud_percentile']}</td>
          <td>{st_pct_of_20}</td>
          <td class="ttl">{esc(r['title'])}{na}</td>
          <td class="muted">{esc(r['artist'])}</td>
          <td class="muted">{r['cloud_elo']}</td>
        </tr>"""

    cards = ""
    for r in results:
        crit = r.get("streamlit_per_criterion") or {}
        crit_rows = "".join(
            f"<li><span>{esc(k.replace('_score','').replace('_',' ').title())}"
            f"</span><b>{esc(v)}</b></li>"
            for k, v in crit.items())
        na_badge = ('<span class="badge na">Not scored (read as casual/'
                    'unfinished)</span>' if r["not_art"] else "")
        head = r.get("headline") or {}
        rationales = "".join(f"<li>{esc(x)}</li>" for x in r.get("round_rationales", []))
        st_avg = r["streamlit_avg_score"]
        cards += f"""
        <div class="card">
          <div class="img"><a href="{esc(r['image_url'])}" target="_blank">
            <img src="{esc(r['image_url'])}" loading="lazy" alt=""></a></div>
          <div class="body">
            <h3>{esc(r['title'])}</h3>
            <div class="sub">by {esc(r['artist'])} · {esc(r['sketch_type'])}</div>
            {na_badge}
            <div class="stats">
              <div class="stat"><span class="k">Cloud rank</span>
                <span class="v">{r['cloud_rank']}<small>/{r['cloud_of']}</small></span></div>
              <div class="stat"><span class="k">Percentile</span>
                <span class="v">{r['cloud_percentile']}<small>th</small></span></div>
              <div class="stat"><span class="k">ELO</span>
                <span class="v">{r['cloud_elo']}</span></div>
              <div class="stat"><span class="k">Streamlit avg</span>
                <span class="v">{st_avg if st_avg is not None else '—'}<small>/20</small></span></div>
            </div>
            <div class="verdict"><b>{esc(head.get('headline',''))}</b>
              {esc(head.get('subline',''))}</div>
            <div class="fb"><h4>Consolidated jury feedback</h4>
              <p>{esc(r['feedback']) or '<em>—</em>'}</p></div>
            <details><summary>Per-round jury notes ({len(r.get('round_rationales',[]))})</summary>
              <ul class="rats">{rationales}</ul></details>
            <details><summary>Streamlit per-criterion GPT scores</summary>
              <ul class="crit">{crit_rows}</ul></details>
          </div>
        </div>"""

    corr_txt = (
        f"Pearson r = <b>{pear:+.2f}</b>, Spearman ρ = <b>{spear:+.2f}</b> "
        f"(n = {len(scored)} scored pieces)"
        if pear is not None else "Not enough scored pieces to correlate.")

    interp = ""
    if spear is not None:
        mag = abs(spear)
        strength = ("a strong" if mag >= 0.6 else "a moderate" if mag >= 0.3
                    else "a weak" if mag >= 0.1 else "essentially no")
        direction = "positive" if spear > 0 else "negative"
        interp = (f"The two methods show {strength} {direction} rank "
                  f"relationship. The Streamlit score is an absolute per-criterion "
                  f"GPT rubric (1–20, judged in isolation); the cloud "
                  f"percentile is a <em>relative</em> standing earned by head-to-head "
                  f"jury comparisons against {data['pool_size']-1} learntodraw "
                  f"pieces. Divergence is expected where a piece scores well on the "
                  f"rubric but competes against a tough peer pool (or vice-versa).")

    errs = data.get("errors") or []
    err_html = ""
    if errs:
        err_html = ('<div class="errs"><h3>Errors</h3><ul>' +
                    "".join(f"<li>{esc(e.get('title'))}: {esc(e.get('error'))}</li>"
                            for e in errs) + "</ul></div>")

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Streamlit sample × cloud learntodraw ranking</title>
<style>
  :root{{--ink:#1d2127;--muted:#6b7280;--line:#e5e7eb;--accent:#2f7;}}
  *{{box-sizing:border-box}}
  body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    color:var(--ink);margin:0;background:#fafafa;line-height:1.5}}
  .wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 80px}}
  h1{{font-size:24px;margin:0 0 4px}}
  .lede{{color:var(--muted);margin:0 0 24px}}
  .panel{{background:#fff;border:1px solid var(--line);border-radius:12px;
    padding:20px 24px;margin:0 0 28px}}
  .summary{{display:flex;flex-wrap:wrap;gap:24px;margin:0 0 8px}}
  .summary div{{font-size:13px;color:var(--muted)}}
  .summary b{{display:block;font-size:20px;color:var(--ink)}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}}
  th{{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;
    letter-spacing:.04em}}
  td.ttl{{font-weight:600}} .muted{{color:var(--muted)}}
  .corr{{font-size:15px;margin:0 0 6px}} .interp{{color:var(--muted);font-size:14px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
    gap:20px}}
  .card{{background:#fff;border:1px solid var(--line);border-radius:12px;
    overflow:hidden;display:flex;flex-direction:column}}
  .card .img{{background:#f3f4f6;aspect-ratio:4/3;overflow:hidden;display:flex;
    align-items:center;justify-content:center}}
  .card .img img{{width:100%;height:100%;object-fit:cover}}
  .card .body{{padding:14px 16px 16px}}
  .card h3{{margin:0;font-size:16px}}
  .card .sub{{color:var(--muted);font-size:12px;margin:2px 0 10px}}
  .stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 12px}}
  .stat{{background:#f7f8fa;border-radius:8px;padding:6px 8px;text-align:center}}
  .stat .k{{display:block;font-size:10px;color:var(--muted);text-transform:uppercase;
    letter-spacing:.03em}}
  .stat .v{{font-size:18px;font-weight:700}} .stat small{{font-size:11px;color:var(--muted);font-weight:400}}
  .verdict{{font-size:13px;margin:0 0 10px}}
  .fb h4{{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;
    color:var(--muted)}}
  .fb p{{margin:0;font-size:14px}}
  details{{margin-top:10px;font-size:13px}} summary{{cursor:pointer;color:var(--muted)}}
  ul.rats,ul.crit{{margin:8px 0 0;padding-left:18px}} ul.rats li{{margin:3px 0}}
  ul.crit{{list-style:none;padding:0}} ul.crit li{{display:flex;justify-content:space-between;
    border-bottom:1px solid var(--line);padding:3px 0}}
  .badge{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;margin:0 0 8px}}
  .badge.na{{background:#fde8e8;color:#9b1c1c}}
  .errs{{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:16px 20px}}
</style></head><body><div class="wrap">
  <h1>Streamlit artwork sample &times; cloud <code>learntodraw</code> ranking</h1>
  <p class="lede">{data['n_sample']} randomly sampled pieces (seed {data['seed']})
    from the original Ruggles Streamlit database, each run through the standard
    cloud insertion route &mdash; {data['insertion_groups']} jury rounds on
    <code>{esc(data['model'])}</code> against {data['pool_size']-1} learntodraw
    anchors.</p>

  <div class="panel"><div class="summary">
    <div><b>{data['n_sample']}</b>pieces sampled</div>
    <div><b>{len(results)}</b>ranked</div>
    <div><b>{data['pool_size']-1}</b>pool anchors</div>
    <div><b>{data['insertion_groups']}</b>jury rounds each</div>
    <div><b>{data['elapsed_sec']}s</b>total runtime</div>
  </div></div>

  <div class="panel">
    <h2 style="margin:0 0 12px;font-size:18px">Streamlit GPT score vs. cloud jury percentile</h2>
    <p class="corr">{corr_txt}</p>
    <p class="interp">{interp}</p>
    {_scatter_svg(pts)}
  </div>

  <div class="panel">
    <h2 style="margin:0 0 12px;font-size:18px">Ranking table (best cloud rank first)</h2>
    <table><thead><tr>
      <th>Cloud rank</th><th>Percentile</th><th>Streamlit avg</th>
      <th>Title</th><th>Artist</th><th>ELO</th>
    </tr></thead><tbody>{rows}</tbody></table>
  </div>

  {err_html}

  <h2 style="font-size:18px;margin:0 0 16px">Per-piece results</h2>
  <div class="cards">{cards}</div>
</div></body></html>"""

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"Wrote {OUT_PATH}")
    if pear is not None:
        print(f"Pearson r={pear:+.3f}  Spearman rho={spear:+.3f}  n={len(scored)}")


if __name__ == "__main__":
    main()
