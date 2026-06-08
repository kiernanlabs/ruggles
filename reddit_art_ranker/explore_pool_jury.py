"""One-off experiment: sample N submissions from a subreddit dump, run the
cloud LLM jury on them as a single group, and write the results + an HTML
report locally — WITHOUT touching any database.

The point is to eyeball, for a brand-new pool (e.g. learntodraw), what kinds of
submissions actually show up and how the jury — using that pool's framing and
criteria from reddit_art_ranker_cloud/shared/pools.py — categorizes and ranks
them. It deliberately reuses the cloud `rank_group` and pool definitions so we
are exercising the real production prompt, not a local copy.

Only the FIRST image of each post is sent to the jury (same as production:
fetch_pushshift._extract_image_url returns the first gallery image), so the
report reflects exactly what the model sees.

Usage (run from the repo root so the root .env with OPENROUTER_API_KEY loads):
    python -m reddit_art_ranker.explore_pool_jury \\
        reddit_art_ranker/subreddits25/learntodraw_submissions.zst \\
        --pool learntodraw --n 10 --seed 7 --open

    # Default dump path is inferred from the pool's subreddit, so this works too:
    python -m reddit_art_ranker.explore_pool_jury --pool learntodraw --open
"""

import argparse
import datetime as dt
import html
import json
import random
import sys
import webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .fetch_pushshift import (
    _as_float,
    _as_int,
    _extract_image_url,
    _head_check,
    _stream_lines,
)

# Import the REAL cloud jury + pool registry so we test production framing.
_CLOUD_DIR = Path(__file__).resolve().parent.parent / "reddit_art_ranker_cloud"
if str(_CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(_CLOUD_DIR))

from shared.config import LLM_MODEL  # noqa: E402
from shared.llm import (  # noqa: E402
    DEFAULT_CRITERIA,
    DEFAULT_FRAMING,
    SYSTEM_PROMPT_TEMPLATE,
    rank_group,
)
from shared.pools import get_pool  # noqa: E402

# Load the root .env (OPENROUTER_API_KEY) explicitly — don't rely on cwd.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

MODULE_DIR = Path(__file__).resolve().parent


# ── sampling ─────────────────────────────────────────────────────────────────
def reservoir_sample(dump_path: Path, subreddit: str, k: int, rng: random.Random,
                     min_score: int) -> list:
    """Stream the whole dump once and keep a uniform random sample of `k`
    usable image posts (reservoir sampling — bounds memory regardless of dump
    size). Returns a list of {post, image_url} dicts."""
    kept = []          # the reservoir
    seen_usable = 0    # count of eligible posts seen so far
    scanned = 0
    for line in _stream_lines(dump_path):
        scanned += 1
        if scanned % 50000 == 0:
            print(f"  scanned {scanned:>8,}  usable {seen_usable:>6,}")
        try:
            post = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (post.get("subreddit") or "").lower() != subreddit.lower():
            continue
        if post.get("over_18") or post.get("removed_by_category"):
            continue
        if _as_int(post.get("score")) < min_score:
            continue
        image_url = _extract_image_url(post)
        if not image_url:
            continue

        seen_usable += 1
        entry = {"post": post, "image_url": image_url}
        if len(kept) < k:
            kept.append(entry)
        else:
            j = rng.randint(0, seen_usable - 1)
            if j < k:
                kept[j] = entry
    print(f"\nScanned {scanned:,} records; {seen_usable:,} usable image posts; "
          f"sampled {len(kept)}.")
    return kept


def validate_to_n(candidates: list, n: int) -> list:
    """HEAD-check candidates in order and keep the first `n` whose image URL is
    live (old preview/signed URLs can be dead). Candidates are already random,
    so taking them in order preserves uniformity."""
    good = []
    for c in candidates:
        if len(good) >= n:
            break
        if _head_check(c["image_url"]):
            good.append(c)
        else:
            print(f"  dead image, skipping: {c['post'].get('id')}")
    return good


# ── result assembly ──────────────────────────────────────────────────────────
def build_records(selected: list, result: dict) -> dict:
    """Merge the jury result back onto the source posts. `selected[i]`
    corresponds to original index i passed into rank_group."""
    rank_by_idx = {orig: pos + 1 for pos, orig in enumerate(result["order"])}
    rat_by_idx = {x["original_index"]: x["rationale"]
                  for x in result["per_piece_rationales"]}
    not_art = set(result["not_art_indices"])

    pieces = []
    for i, c in enumerate(selected):
        p = c["post"]
        created = _as_float(p.get("created_utc"))
        pieces.append({
            "original_index": i,
            "reddit_id": p.get("id"),
            "title": p.get("title") or "",
            "author": p.get("author"),
            "permalink": (f"https://reddit.com{p.get('permalink')}"
                          if p.get("permalink") else None),
            "image_url": c["image_url"],
            "score": _as_int(p.get("score")),
            "num_comments": _as_int(p.get("num_comments")),
            "is_gallery": bool(p.get("is_gallery")),
            "created_date": (
                dt.datetime.fromtimestamp(created, dt.timezone.utc).date().isoformat()
                if created else None),
            "is_not_art": i in not_art,
            "rank": rank_by_idx.get(i),
            "rationale": rat_by_idx.get(i, ""),
        })
    # ranked first (by jury rank), then the not-art pile
    pieces.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0))
    ranking = [p for p in pieces if not p["is_not_art"]]
    not_art = [p for p in pieces if p["is_not_art"]]

    # Upvote rank among the ranked (art) pieces — our crowd-appeal proxy. Each
    # piece carries its crowd rank and the divergence from the jury's rank so
    # the report shows where the jury and the crowd disagree.
    for pos, p in enumerate(sorted(ranking, key=lambda x: -x["score"])):
        p["upvote_rank"] = pos + 1
    for p in ranking:
        p["rank_delta"] = p["rank"] - p["upvote_rank"]  # +ve: jury ranked below crowd

    # Spearman rank correlation between jury rank and upvote rank.
    m = len(ranking)
    spearman = None
    if m > 1:
        d2 = sum(p["rank_delta"] ** 2 for p in ranking)
        spearman = round(1 - 6 * d2 / (m * (m * m - 1)), 3)

    return {
        "ranking": ranking,
        "not_art": not_art,
        "overall_rationale": result.get("rationale", ""),
        "spearman_vs_upvotes": spearman,
        "usage": result.get("usage"),
        "finish_reason": result.get("finish_reason"),
    }


# ── HTML report ──────────────────────────────────────────────────────────────
CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 1000px;
       margin: 24px auto; padding: 0 16px; color: #222; }
h1 { margin: 0 0 4px; } h2 { margin: 32px 0 8px; }
.meta { color: #666; font-size: 13px; margin-bottom: 8px; }
details.prompt { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px;
                 padding: 10px 14px; margin: 12px 0 24px; font-size: 13px; }
details.prompt pre { white-space: pre-wrap; margin: 8px 0 0; line-height: 1.45; }
.banner { background: #f0f7ff; border: 1px solid #cfe3ff; border-radius: 6px;
          padding: 10px 14px; margin: 12px 0; font-size: 14px; }
.piece { display: flex; gap: 16px; padding: 16px 0; border-bottom: 1px solid #eee; }
.piece img { width: 200px; height: 200px; object-fit: cover; border-radius: 6px;
             background: #eee; flex: none; }
.body { flex: 1; min-width: 0; }
.rank-pill { display: inline-block; min-width: 26px; padding: 3px 9px; border-radius: 12px;
             background: #0366d6; color: #fff; font-weight: 600; text-align: center;
             margin-right: 8px; }
.rank-pill.na { background: #b00020; }
.title { font-weight: 600; margin: 2px 0 6px; }
.title a { color: #111; text-decoration: none; } .title a:hover { text-decoration: underline; }
.stats { color: #666; font-size: 12.5px; margin-bottom: 8px; }
.stats span { margin-right: 12px; }
.rationale { font-size: 14px; line-height: 1.5; }
.tag { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 10px;
       background: #eee; color: #555; margin-left: 6px; }
.crowd { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 10px;
         margin-left: 6px; font-weight: 600; }
.crowd.agree { background: #e6f4ea; color: #1a7f37; }
.crowd.up { background: #fdece6; color: #b54708; }     /* crowd liked it more than jury */
.crowd.down { background: #e8eefc; color: #1f4bd8; }   /* jury liked it more than crowd */
.corr { font-size: 14px; font-weight: 600; }
"""


def _piece_html(p: dict, na: bool = False) -> str:
    pill = (f'<span class="rank-pill na">N/A</span>' if na
            else f'<span class="rank-pill">{p["rank"]}</span>')
    title = html.escape(p["title"] or "(untitled)")
    title_html = (f'<a href="{html.escape(p["permalink"])}" target="_blank">{title}</a>'
                  if p["permalink"] else title)
    gallery = '<span class="tag">gallery → 1st image</span>' if p["is_gallery"] else ""
    crowd = ""
    if not na and p.get("upvote_rank") is not None:
        delta = p["rank_delta"]
        if delta == 0:
            cls, txt = "agree", f'crowd #{p["upvote_rank"]} · matches jury'
        elif delta > 0:
            # jury rank number higher than crowd's → crowd liked it more
            cls, txt = "up", f'crowd #{p["upvote_rank"]} · crowd +{delta} vs jury'
        else:
            cls, txt = "down", f'crowd #{p["upvote_rank"]} · jury +{-delta} vs crowd'
        crowd = f'<span class="crowd {cls}">{txt}</span>'
    stats = (
        f'<span>▲ {p["score"]}</span><span>💬 {p["num_comments"]}</span>'
        f'<span>{p["created_date"] or ""}</span>'
        f'<span>u/{html.escape(p["author"] or "?")}</span>'
    )
    rationale = html.escape(p["rationale"] or ("— flagged not art —" if na else ""))
    return (
        f'<div class="piece">'
        f'<img src="{html.escape(p["image_url"])}" loading="lazy" '
        f'alt="{html.escape(p["reddit_id"] or "")}">'
        f'<div class="body"><div class="title">{pill}{title_html}{gallery}</div>'
        f'<div class="stats">{stats}{crowd}</div>'
        f'<div class="rationale">{rationale}</div></div></div>'
    )


def render_html(records: dict, meta: dict, system_prompt: str) -> str:
    head = (
        f'<h1>Jury exploration — {html.escape(meta["pool_label"])}</h1>'
        f'<div class="meta">model <code>{html.escape(meta["model"])}</code> · '
        f'{meta["n"]} submissions · seed {meta["seed"]} · '
        f'min-score {meta["min_score"]} · generated {html.escape(meta["generated"])}</div>'
    )
    rho = records.get("spearman_vs_upvotes")
    if rho is not None:
        head += (f'<div class="banner"><span class="corr">Jury vs. upvotes: '
                 f'Spearman ρ = {rho:+.2f}</span> '
                 f'(1.00 = jury ranking perfectly matches upvote order; '
                 f'0 = unrelated). Per-piece "crowd #N" badges below show where '
                 f'the jury and the crowd disagree.</div>')
    usage = records.get("usage") or {}
    head += (f'<div class="meta">tokens: {usage.get("total_tokens","?")} '
             f'(prompt {usage.get("prompt_tokens","?")}, '
             f'completion {usage.get("completion_tokens","?")}) · '
             f'finish_reason {records.get("finish_reason")}</div>')
    prompt_block = (
        '<details class="prompt"><summary>System prompt sent to the jury '
        '(pool framing + criteria)</summary>'
        f'<pre>{html.escape(system_prompt)}</pre></details>'
    )
    overall = (f'<div class="banner"><strong>Overall rationale:</strong> '
               f'{html.escape(records["overall_rationale"])}</div>'
               if records["overall_rationale"] else "")

    ranked = "".join(_piece_html(p) for p in records["ranking"])
    not_art_section = ""
    if records["not_art"]:
        not_art_section = (
            f'<h2>Flagged not art ({len(records["not_art"])})</h2>'
            + "".join(_piece_html(p, na=True) for p in records["not_art"])
        )
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>Jury exploration — {html.escape(meta["pool_label"])}</title>'
        f'<style>{CSS}</style></head><body>'
        f'{head}{prompt_block}{overall}'
        f'<h2>Ranking — best to worst ({len(records["ranking"])})</h2>{ranked}'
        f'{not_art_section}</body></html>'
    )


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump_path", type=Path, nargs="?",
                    help="Path to <Subreddit>_submissions.zst "
                         "(default: inferred from the pool under subreddits25/)")
    ap.add_argument("--pool", default="learntodraw",
                    help="Pool id from reddit_art_ranker_cloud/shared/pools.py")
    ap.add_argument("--n", type=int, default=10, help="Submissions to rank")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for a reproducible sample (default: random)")
    ap.add_argument("--min-score", type=int, default=0,
                    help="Drop posts below this score (0 = fully representative)")
    ap.add_argument("--model", default=LLM_MODEL)
    ap.add_argument("--oversample", type=float, default=2.0,
                    help="Sample n*this candidates so dead image URLs don't leave us short")
    ap.add_argument("--open", action="store_true", help="Open the report when done")
    args = ap.parse_args()

    pool = get_pool(args.pool)  # validates pool id
    dump_path = args.dump_path or (
        MODULE_DIR / "subreddits25" / f"{pool.subreddit}_submissions.zst")
    if not dump_path.exists():
        raise SystemExit(f"Dump file not found: {dump_path}")

    rng = random.Random(args.seed)
    pool_size = max(args.n, int(args.n * args.oversample))
    print(f"Sampling {pool_size} candidates from {dump_path.name} "
          f"(pool={pool.id}, subreddit={pool.subreddit})...")
    candidates = reservoir_sample(dump_path, pool.subreddit, pool_size, rng,
                                  args.min_score)
    rng.shuffle(candidates)

    print(f"Validating image URLs (need {args.n})...")
    selected = validate_to_n(candidates, args.n)
    if len(selected) < args.n:
        print(f"WARNING: only {len(selected)}/{args.n} live images; "
              f"raise --oversample for a full set.")
    if not selected:
        raise SystemExit("No live images found — nothing to rank.")

    # Build the exact system prompt this pool produces, for the report + record.
    framing = pool.framing or DEFAULT_FRAMING.format(subject=pool.jury_subject)
    criteria = pool.criteria or DEFAULT_CRITERIA
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(framing=framing, criteria=criteria)

    print(f"Running the jury on {len(selected)} pieces with {args.model}...")
    image_urls = [c["image_url"] for c in selected]
    # shuffle=False: `selected` is already random, and we want original_index to
    # line up with our list so the report maps cleanly.
    result = rank_group(image_urls, model=args.model, shuffle=False,
                        jury_subject=pool.jury_subject,
                        framing=pool.framing, criteria=pool.criteria)
    records = build_records(selected, result)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = MODULE_DIR / "experiments" / f"{pool.id}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "pool_id": pool.id, "pool_label": pool.label, "subreddit": pool.subreddit,
        "model": args.model, "n": len(selected), "seed": args.seed,
        "min_score": args.min_score, "generated": stamp,
        "dump_path": str(dump_path),
    }
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "system_prompt": system_prompt,
                    "records": records, "raw_result": result}, indent=2),
        encoding="utf-8")
    report_path = out_dir / "report.html"
    report_path.write_text(render_html(records, meta, system_prompt), encoding="utf-8")

    print(f"\nWrote:\n  {out_dir / 'results.json'}\n  {report_path}")
    print(f"Ranked {len(records['ranking'])}, flagged not-art {len(records['not_art'])}.")
    if args.open:
        webbrowser.open(report_path.resolve().as_uri())


if __name__ == "__main__":
    main()
