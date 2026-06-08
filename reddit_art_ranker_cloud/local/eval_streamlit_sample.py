"""One-off analysis: take N random artworks from the original Streamlit app's
DynamoDB table (ruggles_artworks_prod, Cloudinary-hosted images + per-criterion
GPT scores) and run each through the *standard cloud insertion route* against the
`learntodraw` pool — exactly as backend/handlers.worker_handler does — then dump
the results to JSON for the HTML report builder.

The candidate image_url passed to the jury is the public Cloudinary URL, so no
S3 round-trip is needed (the worker only uses S3 because consumer uploads land
there; the insertion algorithm just needs a URL the LLM can fetch).

Run from reddit_art_ranker_cloud/:  python -m local.eval_streamlit_sample
"""

import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")
except Exception:
    pass

from shared.config import INSERTION_GROUPS, LLM_MODEL
from shared.pools import get_pool
from backend import ddb, feedback
from backend.insert import insert

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STREAMLIT_TABLE = "ruggles_artworks_prod"
POOL_ID = "learntodraw"
N_SAMPLE = 20
SEED = 42
MODEL = os.getenv("LLM_MODEL", LLM_MODEL)
MAX_WORKERS = 5
OUT_PATH = os.path.join(os.path.dirname(__file__), "eval_sample_results.json")

# Core criteria are always scored; the realism four only for full-realism pieces.
# Mirrors how streamlit_app.py averages scores for its leaderboard.
CORE_SCORES = ["proportion_score", "line_quality_score",
               "form_volume_score", "mood_expression_score"]
REALISM_SCORES = ["value_light_score", "detail_texture_score",
                  "composition_perspective_score", "overall_realism_score"]


def _num(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def fetch_streamlit_artworks() -> list[dict]:
    t = boto3.resource("dynamodb").Table(STREAMLIT_TABLE)
    items, kwargs = [], dict(
        IndexName="by_created_at",
        KeyConditionExpression=Key("entity_type").eq("artwork"),
        ScanIndexForward=False,
    )
    while True:
        r = t.query(**kwargs)
        items.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return items


def streamlit_aggregate(item: dict) -> dict:
    """Average score (1-20) the same way the Streamlit leaderboard does:
    4 core criteria always, +4 realism criteria for full-realism pieces."""
    sketch = item.get("sketch_type", "full realism")
    keys = list(CORE_SCORES)
    if sketch == "full realism":
        keys += REALISM_SCORES
    vals = [_num(item[k]) for k in keys if k in item and _num(item[k])]
    avg = round(sum(vals) / len(vals), 2) if vals else None
    return {"avg_score": avg, "n_criteria": len(vals), "sketch_type": sketch,
            "per_criterion": {k: _num(item.get(k)) for k in keys if k in item}}


def evaluate_one(art: dict, pool: list, pool_def) -> dict:
    cand_id = f"st_{art['id'][:12]}"
    result = insert(
        candidate_id=cand_id,
        image_url=art["image_url"],
        title=art.get("title") or "Untitled",
        pool=pool,
        jury_subject=pool_def.jury_subject,
        framing=pool_def.framing,
        criteria=pool_def.criteria,
        model=MODEL,
        n_groups=INSERTION_GROUPS,
    )
    cand_rationales = [rd["candidate"].get("rationale_this_round")
                       for rd in result["rounds"] if rd.get("candidate")]
    if result.get("not_art"):
        synthesized = ("The jury repeatedly read this as a casual/unfinished "
                       "piece rather than a finished submission, so it was not "
                       "ranked.")
        headline = {"headline": "The jury didn't score this piece.", "subline": ""}
    else:
        synthesized = feedback.synthesize_feedback(cand_rationales, MODEL)
        headline = feedback.verdict_headline(result["percentile"])
    agg = streamlit_aggregate(art)
    return {
        "id": art["id"],
        "title": art.get("title") or "Untitled",
        "artist": art.get("artist_name") or "Unknown",
        "image_url": art["image_url"],
        "sketch_type": agg["sketch_type"],
        "streamlit_avg_score": agg["avg_score"],
        "streamlit_per_criterion": agg["per_criterion"],
        "cloud_rank": result["rank"],
        "cloud_of": result["of"],
        "cloud_percentile": result["percentile"],
        "cloud_elo": round(result["elo"], 1),
        "not_art": bool(result.get("not_art")),
        "n_ranked_rounds": result.get("n_ranked_rounds"),
        "n_evaluated_rounds": result.get("n_evaluated_rounds"),
        "headline": headline,
        "feedback": synthesized,
        "round_rationales": [r for r in cand_rationales if r],
    }


def main():
    print(f"Loading anchor pool '{POOL_ID}' ...")
    pool_def = get_pool(POOL_ID)
    pool = ddb.load_eligible_pool(POOL_ID)
    print(f"  {len(pool)} eligible anchors")

    print(f"Fetching artworks from {STREAMLIT_TABLE} ...")
    arts = fetch_streamlit_artworks()
    arts = [a for a in arts if a.get("image_url")]
    print(f"  {len(arts)} artworks with images")

    random.seed(SEED)
    sample = random.sample(arts, min(N_SAMPLE, len(arts)))
    print(f"Sampled {len(sample)} artworks (seed={SEED}). "
          f"Running insertion via model={MODEL}, groups={INSERTION_GROUPS} ...")

    results, errors = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(evaluate_one, a, pool, pool_def): a for a in sample}
        for i, fut in enumerate(as_completed(futs), 1):
            a = futs[fut]
            try:
                res = fut.result()
                results.append(res)
                print(f"  [{i}/{len(sample)}] OK  rank {res['cloud_rank']}/"
                      f"{res['cloud_of']} (pctl {res['cloud_percentile']}) "
                      f"st_avg {res['streamlit_avg_score']} — {res['title'][:40]}")
            except Exception as e:
                errors.append({"id": a.get("id"), "title": a.get("title"),
                               "error": str(e)})
                print(f"  [{i}/{len(sample)}] ERR {a.get('title')}: {e}")

    results.sort(key=lambda r: r["cloud_rank"])
    payload = {
        "pool_id": POOL_ID,
        "pool_label": pool_def.label,
        "pool_size": len(pool) + 1,
        "model": MODEL,
        "insertion_groups": INSERTION_GROUPS,
        "seed": SEED,
        "n_sample": len(sample),
        "elapsed_sec": round(time.time() - t0, 1),
        "results": results,
        "errors": errors,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nDone in {payload['elapsed_sec']}s. "
          f"{len(results)} ok, {len(errors)} errors. -> {OUT_PATH}")


if __name__ == "__main__":
    main()
