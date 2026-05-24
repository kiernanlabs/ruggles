"""Insert a new candidate piece into an existing ranked pool.

The candidate is compared against decile-spaced anchor pieces in groups of 5,
repeated INSERTION_GROUPS times with fresh anchor draws. Only the candidate's
ELO is updated; anchor ratings stay frozen. Excluded (not-art-flagged) pieces
are not used as anchors.

Usage:
    python -m reddit_art_ranker.insert --image-url https://... --title "My piece"
    python -m reddit_art_ranker.insert --image-path ./mypainting.jpg --title "My piece"
"""

import argparse
import base64
import mimetypes
import random
import sys
import time
import uuid
from pathlib import Path

from . import db
from .config import (
    ANCHOR_DECILES,
    GROUP_SIZE,
    INSERTION_GROUPS,
    LLM_MODEL,
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


def _pick_anchors(pool: list, n_anchors: int) -> list:
    if len(pool) <= n_anchors:
        return list(pool)
    deciles = ANCHOR_DECILES[:n_anchors]
    anchors = []
    used = set()
    for decile in deciles:
        target_idx = int(round((decile / 10.0) * (len(pool) - 1)))
        offset = 0
        while target_idx + offset in used or target_idx - offset in used:
            offset += 1
            if target_idx + offset >= len(pool) and target_idx - offset < 0:
                break
        idx = target_idx + offset if target_idx + offset < len(pool) else target_idx - offset
        used.add(idx)
        anchors.append(pool[idx])
    return anchors


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
) -> dict:
    """Insert a candidate into the ranked pool. `image_url` may be either a
    remote URL or a base64 data URI."""
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

    print(f"Inserting candidate '{title}' (id={candidate_id}) into r/{subreddit} "
          f"({len(pool)} eligible pieces).")
    print(f"Running {n_groups} rounds: candidate + {GROUP_SIZE - 1} anchors each.\n")

    rounds = []  # rich per-round detail for caller / reporting

    for g_idx in range(n_groups):
        with db.connect() as conn:
            pool = _ranked_pool(conn, subreddit)
        anchors = _pick_anchors(pool, GROUP_SIZE - 1)
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
            print(f"  round {g_idx + 1}/{n_groups}: LLM flagged candidate as NOT ART "
                  f"(no ELO update)")
        elif candidate_id in ranked_ids:
            new_ratings, comp_counts = apply_group_ranking(
                local_ratings, ranked_ids, frozen_ids=frozen
            )
            candidate_elo = new_ratings[candidate_id]
            with db.connect() as conn:
                db.update_rating(conn, candidate_id, candidate_elo, comp_counts[candidate_id])
            cand_position = ranked_ids.index(candidate_id) + 1
            print(f"  round {g_idx + 1}/{n_groups}: candidate finished "
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


def print_round_report(result: dict) -> None:
    """Pretty-print all per-round detail for a single inserted candidate."""
    title = result["title"]
    print("\n" + "=" * 90)
    print(f"  {title}  →  final ELO {result['elo']:.1f}  "
          f"(rank {result['rank']}/{result['of']}, ~{result['percentile']}th percentile)")
    print("=" * 90)

    for rd in result["rounds"]:
        print(f"\n  --- Round {rd['round']} ---")
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
    parser.add_argument("--title", default="Candidate submission")
    parser.add_argument("--subreddit", default=SUBREDDIT)
    parser.add_argument("--groups", type=int, default=INSERTION_GROUPS)
    parser.add_argument("--model", default=LLM_MODEL)
    args = parser.parse_args()

    if args.image_path:
        if not args.image_path.exists():
            raise SystemExit(f"File not found: {args.image_path}")
        image_url = _path_to_data_uri(args.image_path)
    else:
        image_url = args.image_url

    result = insert(image_url, args.subreddit, args.title, args.groups, args.model)
    print_round_report(result)


if __name__ == "__main__":
    main()
