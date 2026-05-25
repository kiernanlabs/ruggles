"""Snapshot, reset, list, and compare model-rating runs.

The `ratings` table only holds one set of ELOs at a time. To compare two
models against the same 500-piece pool, snapshot the current ratings after
each rank pass, then reset and re-run with a different model.

Usage:
    # Snapshot current ratings under a model label
    python -m reddit_art_ranker.snapshot save \\
        --model gpt-5.4-mini --label "first-500-run"

    # Reset live ratings (keeps comparisons history)
    python -m reddit_art_ranker.snapshot reset

    # List snapshots
    python -m reddit_art_ranker.snapshot list

    # Compare two snapshots (rank correlation, top movers, vs upvotes)
    python -m reddit_art_ranker.snapshot compare \\
        --run-a 1 --run-b 2
"""

import argparse
import sys

from . import db
from .config import SUBREDDIT

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _spearman(xs: list, ys: list) -> float | None:
    """Spearman rho, stdlib only. Ignores any None pair."""
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


def cmd_save(args):
    with db.connect() as conn:
        run_id = db.snapshot_ratings(
            conn, model=args.model, subreddit=args.subreddit,
            label=args.label, note=args.note,
        )
    print(f"Saved snapshot run_id={run_id}  model={args.model}  "
          f"label={args.label or '(none)'}")


def cmd_reset(args):
    with db.connect() as conn:
        n = db.reset_ratings(conn, subreddit=args.subreddit)
    print(f"Reset ratings for {n} pieces in r/{args.subreddit}. "
          "Pieces + comparisons history kept intact.")


def cmd_list(args):
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, model, label, subreddit, snapshot_at, n_pieces, n_excluded, note
            FROM model_runs ORDER BY id
            """
        ).fetchall()
    if not rows:
        print("No snapshots yet.")
        return
    print(f"{'id':>3}  {'model':30s}  {'label':25s}  {'pieces':>6}  {'excl':>4}  snapshot_at")
    for r in rows:
        print(f"{r['id']:>3}  {r['model']:30s}  {(r['label'] or '-'):25s}  "
              f"{r['n_pieces']:>6}  {r['n_excluded']:>4}  {r['snapshot_at']}")


def _load_run(conn, run_id: int) -> tuple[dict, dict]:
    """Return (meta_dict, {reddit_id: {elo, n_comparisons, n_not_art_flags}})."""
    meta = conn.execute(
        "SELECT * FROM model_runs WHERE id = ?", (run_id,)
    ).fetchone()
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


def cmd_compare(args):
    with db.connect() as conn:
        meta_a, rat_a = _load_run(conn, args.run_a)
        meta_b, rat_b = _load_run(conn, args.run_b)
        pieces = {r["reddit_id"]: dict(r) for r in conn.execute(
            """SELECT reddit_id, title, upvotes, num_comments, upvote_ratio,
                      permalink, image_url FROM pieces WHERE subreddit = ?""",
            (args.subreddit,),
        ).fetchall()}

    # Eligible in BOTH runs (not excluded in either)
    eligible = [
        pid for pid in pieces
        if pid in rat_a and pid in rat_b
        and rat_a[pid]["n_not_art_flags"] < 2 and rat_b[pid]["n_not_art_flags"] < 2
    ]
    excl_a = sum(1 for pid in pieces if pid in rat_a and rat_a[pid]["n_not_art_flags"] >= 2)
    excl_b = sum(1 for pid in pieces if pid in rat_b and rat_b[pid]["n_not_art_flags"] >= 2)
    excl_either = sum(
        1 for pid in pieces if pid in rat_a and pid in rat_b
        and (rat_a[pid]["n_not_art_flags"] >= 2 or rat_b[pid]["n_not_art_flags"] >= 2)
    )

    print(f"\n=== Comparing run_id {args.run_a} vs run_id {args.run_b} ===")
    print(f"  A: {meta_a['model']:30s}  ({meta_a['label']})  excl={excl_a}")
    print(f"  B: {meta_b['model']:30s}  ({meta_b['label']})  excl={excl_b}")
    print(f"  Eligible in BOTH (used for correlations): {len(eligible)}  "
          f"(excluded by either: {excl_either})\n")

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

    print("=== Spearman rank correlations ===")
    print(f"  A vs B (model agreement)     rho = {rho_models:+.3f}")
    print()
    print(f"  A vs upvotes                 rho = {rho_a_up:+.3f}")
    print(f"  B vs upvotes                 rho = {rho_b_up:+.3f}")
    print(f"  A vs upvote_ratio            rho = {rho_a_ratio:+.3f}")
    print(f"  B vs upvote_ratio            rho = {rho_b_ratio:+.3f}")
    print(f"  A vs num_comments            rho = {rho_a_cmt:+.3f}")
    print(f"  B vs num_comments            rho = {rho_b_cmt:+.3f}")

    # Top movers: pieces where rank changed most between the two runs
    rank_a = {pid: i + 1 for i, pid in enumerate(
        sorted(eligible, key=lambda p: -rat_a[p]["elo"]))}
    rank_b = {pid: i + 1 for i, pid in enumerate(
        sorted(eligible, key=lambda p: -rat_b[p]["elo"]))}
    moves = [(pid, rank_a[pid], rank_b[pid], rank_b[pid] - rank_a[pid])
             for pid in eligible]
    moves.sort(key=lambda x: -abs(x[3]))

    print(f"\n=== Biggest rank movers (B - A; positive = fell in B) ===")
    print(f"{'piece':>10}  {'rank A':>6}  {'rank B':>6}  {'Δ':>5}  {'upvotes':>7}  title")
    for pid, ra, rb, delta in moves[:15]:
        p = pieces[pid]
        print(f"{pid:>10}  {ra:>6}  {rb:>6}  {delta:+5d}  "
              f"{(p['upvotes'] or 0):>7}  {(p['title'] or '')[:50]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="Snapshot current ratings into model_runs")
    s.add_argument("--model", required=True)
    s.add_argument("--label", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--subreddit", default=SUBREDDIT)

    r = sub.add_parser("reset", help="Reset ELOs to baseline (keep comparisons)")
    r.add_argument("--subreddit", default=SUBREDDIT)

    sub.add_parser("list", help="List all snapshots")

    c = sub.add_parser("compare", help="Compare two snapshots head-to-head")
    c.add_argument("--run-a", type=int, required=True)
    c.add_argument("--run-b", type=int, required=True)
    c.add_argument("--subreddit", default=SUBREDDIT)

    args = parser.parse_args()
    {"save": cmd_save, "reset": cmd_reset,
     "list": cmd_list, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
