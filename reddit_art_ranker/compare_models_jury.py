"""One-off experiment: rank the SAME sampled images across several models and
several small group-ballots, then compare the models side by side.

Design (so it matches how the cloud actually ranks — small groups, not one big
list):
  - Sample N images from a subreddit dump (same sampler/seed as
    explore_pool_jury, so --seed reproduces the identical set).
  - Split them into groups of `--group-size` via `--partitions` independent
    random shuffles. With N=10, size=5, partitions=2 that yields 4 ballots of
    5, each image judged exactly twice.
  - For EACH model, run the cloud jury (real pool framing/criteria) on every
    ballot, then aggregate each image's within-ballot positions into an average
    ranking (mean normalized rank, 0=best .. 1=worst).
  - Render one HTML report: a model-vs-model comparison table plus per-model
    detail (every ballot, its ranking, and rationales).

No database is touched. Outputs land in reddit_art_ranker/experiments/.

Usage (run from repo root so the root .env loads):
    python -m reddit_art_ranker.compare_models_jury --pool learntodraw \\
        --n 10 --seed 7 --group-size 5 --partitions 2 --open
"""

import argparse
import datetime as dt
import html
import json
import random
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Reuse the sampler + cloud wiring from the sibling experiment script (importing
# it also puts reddit_art_ranker_cloud on sys.path and loads the root .env).
from .explore_pool_jury import reservoir_sample, validate_to_n  # noqa: E402
from .fetch_pushshift import _as_int  # noqa: E402

from shared.llm import (  # noqa: E402
    DEFAULT_CRITERIA,
    DEFAULT_FRAMING,
    SYSTEM_PROMPT_TEMPLATE,
    rank_group,
)
from shared.pools import get_pool  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parent

DEFAULT_MODELS = [
    "google/gemini-3.1-flash-lite",
    "minimax/minimax-m3",
    "qwen/qwen3.6-flash",
]


# ── ballot construction ──────────────────────────────────────────────────────
def make_ballots(n_items: int, group_size: int, partitions: int,
                 rng: random.Random) -> list:
    """Return a list of ballots (each a list of global item indices). Each
    partition is an independent shuffle chunked into groups of `group_size`,
    so every item appears exactly once per partition (≈`partitions` times
    total). Leftover items form a smaller final group."""
    ballots = []
    for _ in range(partitions):
        idx = list(range(n_items))
        rng.shuffle(idx)
        for i in range(0, n_items, group_size):
            chunk = idx[i:i + group_size]
            if len(chunk) >= 2:  # a ballot of 1 can't be ranked meaningfully
                ballots.append(chunk)
    return ballots


# ── per-model run ────────────────────────────────────────────────────────────
def run_model(model: str, images: list, ballots: list, pool) -> dict:
    """Run `model` over every ballot and aggregate. `images[i]` is the source
    record; ballots hold global indices into `images`."""
    agg = {im["reddit_id"]: {"norms": [], "ranks": [], "rationales": [],
                             "not_art": 0, "appearances": 0}
           for im in images}
    ballot_results = []

    for bi, ballot in enumerate(ballots):
        members = [images[i]["reddit_id"] for i in ballot]
        for gid in members:
            agg[gid]["appearances"] += 1
        urls = [images[i]["image_url"] for i in ballot]
        try:
            res = rank_group(urls, model=model, shuffle=True,
                             jury_subject=pool.jury_subject,
                             framing=pool.framing, criteria=pool.criteria)
        except Exception as e:  # noqa: BLE001 — record and keep going
            print(f"    ballot {bi}: ERROR {e}")
            ballot_results.append({"ballot": bi, "members": members,
                                   "error": str(e)})
            continue

        order = res["order"]              # local indices, best -> worst (art only)
        n_ranked = len(order)
        rat = {x["original_index"]: x["rationale"]
               for x in res["per_piece_rationales"]}
        for pos, local in enumerate(order):
            gid = images[ballot[local]]["reddit_id"]
            norm = pos / (n_ranked - 1) if n_ranked > 1 else 0.0
            agg[gid]["norms"].append(norm)
            agg[gid]["ranks"].append(pos + 1)
            agg[gid]["rationales"].append(
                {"ballot": bi, "rank": pos + 1, "of": n_ranked,
                 "text": rat.get(local, "")})
        for local in res["not_art_indices"]:
            agg[images[ballot[local]]["reddit_id"]]["not_art"] += 1

        ballot_results.append({
            "ballot": bi,
            "members": members,
            "order": [images[ballot[l]]["reddit_id"] for l in order],
            "not_art": [images[ballot[l]]["reddit_id"] for l in res["not_art_indices"]],
            "overall_rationale": res.get("rationale", ""),
            "rationale_by_id": {images[ballot[l]]["reddit_id"]: rat.get(l, "")
                                for l in order},
        })

    for d in agg.values():
        d["mean_norm"] = (sum(d["norms"]) / len(d["norms"])) if d["norms"] else None

    # Average ranking: best (lowest mean_norm) first; never-ranked (all not-art
    # / all errored) sink to the bottom.
    ordered_ids = sorted(
        agg, key=lambda g: (agg[g]["mean_norm"] is None,
                            agg[g]["mean_norm"] if agg[g]["mean_norm"] is not None else 9))
    for r, gid in enumerate(ordered_ids):
        agg[gid]["avg_rank"] = r + 1
    return {"per_image": agg, "ballots": ballot_results, "order": ordered_ids}


# ── stats ────────────────────────────────────────────────────────────────────
def spearman(rank_a: dict, rank_b: dict, ids: list):
    m = len(ids)
    if m < 2:
        return None
    d2 = sum((rank_a[i] - rank_b[i]) ** 2 for i in ids)
    return round(1 - 6 * d2 / (m * (m * m - 1)), 3)


# ── HTML ─────────────────────────────────────────────────────────────────────
CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1180px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 4px; } h2 { margin: 34px 0 10px; } h3 { margin: 20px 0 6px; }
.meta { color: #666; font-size: 13px; margin: 2px 0; }
.banner { background: #f0f7ff; border: 1px solid #cfe3ff; border-radius: 6px;
          padding: 10px 14px; margin: 14px 0; font-size: 14px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th, td { border-bottom: 1px solid #eee; padding: 8px 10px; text-align: left;
         vertical-align: middle; font-size: 13px; }
th { background: #fafafa; font-size: 11px; text-transform: uppercase;
     letter-spacing: .04em; color: #666; }
td.num, th.num { text-align: center; font-variant-numeric: tabular-nums; }
.thumb { width: 64px; height: 64px; object-fit: cover; border-radius: 4px;
         background: #eee; display: block; }
.thumb.lg { width: 120px; height: 120px; }
.cell { display: flex; flex-direction: column; align-items: center; }
.rk { font-weight: 700; font-size: 15px; }
.sub { color: #777; font-size: 11px; }
.agree { background: #e6f4ea; } .up { background: #fdece6; } .down { background: #eef2fe; }
.title { font-weight: 600; } .title a { color: #111; text-decoration: none; }
.title a:hover { text-decoration: underline; }
details { margin: 10px 0; } summary { cursor: pointer; font-weight: 600; }
.ballot { border: 1px solid #eee; border-radius: 6px; padding: 10px; margin: 10px 0; }
.row { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0;
       border-bottom: 1px solid #f3f3f3; }
.row .body { flex: 1; min-width: 0; font-size: 13px; }
.pill { display: inline-block; min-width: 22px; padding: 2px 7px; border-radius: 10px;
        background: #0366d6; color: #fff; font-weight: 600; text-align: center;
        margin-right: 6px; }
pre.prompt { white-space: pre-wrap; background: #f6f8fa; border: 1px solid #e1e4e8;
             border-radius: 6px; padding: 10px; font-size: 12.5px; line-height: 1.45; }
.err { color: #b00020; font-size: 12px; }
"""


def _delta_cls(delta: int) -> str:
    return "agree" if delta == 0 else ("up" if delta > 0 else "down")


def render_html(images: list, runs: dict, meta: dict, system_prompt: str,
                upvote_rank: dict, model_spearman: dict, pair_rho: list) -> str:
    by_id = {im["reddit_id"]: im for im in images}
    models = meta["models"]

    head = (
        f'<h1>Model comparison — {html.escape(meta["pool_label"])}</h1>'
        f'<div class="meta">{meta["n"]} images · {len(meta["ballots"])} ballots '
        f'of ≤{meta["group_size"]} ({meta["partitions"]} partitions → each image '
        f'judged ~{meta["partitions"]}×) · seed {meta["seed"]} · '
        f'generated {html.escape(meta["generated"])}</div>'
        f'<div class="meta">Average ranking = mean normalized within-ballot rank '
        f'(0 = always best, 1 = always worst). “crowd” = upvote order.</div>'
    )

    # Per-model Spearman vs upvotes + inter-model agreement.
    corr = '<div class="banner"><strong>Spearman ρ vs. upvote order</strong> '
    corr += " · ".join(f'{html.escape(m)}: {model_spearman.get(m)}' for m in models)
    if pair_rho:
        corr += '<br><strong>Model-vs-model ρ:</strong> ' + " · ".join(
            f'{html.escape(a)} ↔ {html.escape(b)}: {r}' for a, b, r in pair_rho)
    corr += '</div>'

    # ── comparison table (rows = images sorted by upvote order) ──
    rows = sorted(images, key=lambda im: upvote_rank[im["reddit_id"]])
    header_cells = "".join(f'<th class="num">{html.escape(m)}</th>' for m in models)
    body = ""
    for im in rows:
        gid = im["reddit_id"]
        title = html.escape((im["title"] or "(untitled)")[:60])
        link = (f'<a href="{html.escape(im["permalink"])}" target="_blank">{title}</a>'
                if im["permalink"] else title)
        cells = ""
        for m in models:
            d = runs[m]["per_image"][gid]
            ar = d["avg_rank"]
            delta = ar - upvote_rank[gid]
            raw = ",".join(str(r) for r in d["ranks"]) or "—"
            na = f' · {d["not_art"]}×not-art' if d["not_art"] else ""
            mn = f'{d["mean_norm"]:.2f}' if d["mean_norm"] is not None else "—"
            cells += (f'<td class="num {_delta_cls(delta)}">'
                      f'<div class="rk">#{ar}</div>'
                      f'<div class="sub">norm {mn} · ballots {raw}{na}</div></td>')
        body += (
            f'<tr><td><div style="display:flex;gap:8px;align-items:center">'
            f'<img class="thumb" src="{html.escape(im["image_url"])}" loading="lazy">'
            f'<div><div class="title">{link}</div>'
            f'<div class="sub">▲{im["score"]} · crowd #{upvote_rank[gid]}</div></div>'
            f'</div></td>{cells}</tr>'
        )
    table = (f'<h2>Side-by-side average ranking</h2><table><thead><tr>'
             f'<th>Image</th>{header_cells}</tr></thead><tbody>{body}</tbody></table>')

    # ── per-model detail ──
    details = "<h2>Per-model detail</h2>"
    for m in models:
        run = runs[m]
        # aggregated ordering
        agg_list = ""
        for gid in run["order"]:
            im = by_id[gid]
            d = run["per_image"][gid]
            mn = f'{d["mean_norm"]:.2f}' if d["mean_norm"] is not None else "—"
            agg_list += (
                f'<div class="row">'
                f'<span class="pill">{d["avg_rank"]}</span>'
                f'<img class="thumb" src="{html.escape(im["image_url"])}" loading="lazy">'
                f'<div class="body"><b>{html.escape((im["title"] or "")[:70])}</b> '
                f'<span class="sub">norm {mn} · ballot ranks {",".join(map(str,d["ranks"])) or "—"} '
                f'· ▲{im["score"]}</span></div></div>')

        ballots_html = ""
        for b in run["ballots"]:
            if "error" in b:
                ballots_html += (f'<div class="ballot">Ballot {b["ballot"]+1}: '
                                 f'<span class="err">ERROR: {html.escape(b["error"])}</span></div>')
                continue
            inner = ""
            for pos, gid in enumerate(b["order"]):
                im = by_id[gid]
                inner += (
                    f'<div class="row"><span class="pill">{pos+1}</span>'
                    f'<img class="thumb" src="{html.escape(im["image_url"])}" loading="lazy">'
                    f'<div class="body"><b>{html.escape((im["title"] or "")[:60])}</b><br>'
                    f'{html.escape(b["rationale_by_id"].get(gid,""))}</div></div>')
            for gid in b["not_art"]:
                im = by_id[gid]
                inner += (f'<div class="row"><span class="pill" style="background:#b00020">NA</span>'
                          f'<img class="thumb" src="{html.escape(im["image_url"])}" loading="lazy">'
                          f'<div class="body"><b>{html.escape((im["title"] or "")[:60])}</b> '
                          f'<span class="sub">flagged not art</span></div></div>')
            ballots_html += (f'<div class="ballot"><b>Ballot {b["ballot"]+1}</b> '
                             f'<span class="sub">{html.escape(b["overall_rationale"])}</span>'
                             f'{inner}</div>')

        details += (
            f'<details open><summary>{html.escape(m)} — ρ vs upvotes '
            f'{model_spearman.get(m)}</summary>'
            f'<h3>Aggregated ranking</h3>{agg_list}'
            f'<h3>Ballots</h3>{ballots_html}</details>')

    prompt_block = ('<details><summary>System prompt (pool framing + criteria)</summary>'
                    f'<pre class="prompt">{html.escape(system_prompt)}</pre></details>')

    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<title>Model comparison — {html.escape(meta["pool_label"])}</title>'
            f'<style>{CSS}</style></head><body>'
            f'{head}{corr}{prompt_block}{table}{details}</body></html>')


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump_path", type=Path, nargs="?")
    ap.add_argument("--pool", default="learntodraw")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--group-size", type=int, default=5)
    ap.add_argument("--partitions", type=int, default=2,
                    help="Independent shuffles; each image is judged once per partition")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--oversample", type=float, default=2.0)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="Comma-separated OpenRouter model slugs")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    pool = get_pool(args.pool)
    dump_path = args.dump_path or (
        MODULE_DIR / "subreddits25" / f"{pool.subreddit}_submissions.zst")
    if not dump_path.exists():
        raise SystemExit(f"Dump file not found: {dump_path}")
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    # Sample the SAME images explore_pool_jury would for this seed.
    rng = random.Random(args.seed)
    pool_size = max(args.n, int(args.n * args.oversample))
    print(f"Sampling {pool_size} candidates from {dump_path.name}...")
    candidates = reservoir_sample(dump_path, pool.subreddit, pool_size, rng, args.min_score)
    rng.shuffle(candidates)
    selected = validate_to_n(candidates, args.n)
    if not selected:
        raise SystemExit("No live images found.")
    images = []
    for c in selected:
        p = c["post"]
        images.append({
            "reddit_id": p.get("id"),
            "title": p.get("title") or "",
            "permalink": (f"https://reddit.com{p.get('permalink')}"
                          if p.get("permalink") else None),
            "image_url": c["image_url"],
            "score": _as_int(p.get("score")),
        })
    print(f"Using {len(images)} live images.")

    # Ballots: fixed for all models (separate rng so it's stable per seed).
    brng = random.Random((args.seed or 0) * 1009 + 17)
    ballots = make_ballots(len(images), args.group_size, args.partitions, brng)
    print(f"{len(ballots)} ballots: {[len(b) for b in ballots]}")

    runs = {}
    for m in models:
        print(f"Running {m} over {len(ballots)} ballots...")
        runs[m] = run_model(m, images, ballots, pool)

    # Upvote rank reference + correlations.
    upvote_rank = {gid: i + 1 for i, (gid, _) in enumerate(
        sorted(((im["reddit_id"], im["score"]) for im in images),
               key=lambda kv: -kv[1]))}
    ids = [im["reddit_id"] for im in images]
    model_spearman = {m: spearman({g: runs[m]["per_image"][g]["avg_rank"] for g in ids},
                                  upvote_rank, ids) for m in models}
    pair_rho = []
    for i in range(len(models)):
        for j in range(i + 1, len(models)):
            a, b = models[i], models[j]
            pair_rho.append((a, b, spearman(
                {g: runs[a]["per_image"][g]["avg_rank"] for g in ids},
                {g: runs[b]["per_image"][g]["avg_rank"] for g in ids}, ids)))

    framing = pool.framing or DEFAULT_FRAMING.format(subject=pool.jury_subject)
    criteria = pool.criteria or DEFAULT_CRITERIA
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(framing=framing, criteria=criteria)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = MODULE_DIR / "experiments" / f"{pool.id}_modelcmp_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "pool_id": pool.id, "pool_label": pool.label, "models": models,
        "n": len(images), "seed": args.seed, "group_size": args.group_size,
        "partitions": args.partitions, "ballots": ballots, "generated": stamp,
        "dump_path": str(dump_path),
    }
    (out_dir / "results.json").write_text(json.dumps({
        "meta": meta, "system_prompt": system_prompt, "images": images,
        "upvote_rank": upvote_rank, "model_spearman": model_spearman,
        "pair_rho": pair_rho,
        "runs": {m: {"order": runs[m]["order"], "per_image": runs[m]["per_image"],
                     "ballots": runs[m]["ballots"]} for m in models},
    }, indent=2), encoding="utf-8")
    report = out_dir / "report.html"
    report.write_text(render_html(images, runs, meta, system_prompt,
                                  upvote_rank, model_spearman, pair_rho),
                      encoding="utf-8")

    print(f"\nWrote:\n  {out_dir/'results.json'}\n  {report}")
    print("Spearman vs upvotes:", model_spearman)
    if args.open:
        import webbrowser
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
