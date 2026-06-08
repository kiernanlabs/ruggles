"""Pool registry — a "pool" is an isolated competition (originally one per
subreddit). Art is only ever ranked against other art in the same pool.

Adding a pool is data, not code: append an entry here, then run the local
fetch + rank + publish pipeline with --pool <id>. The consumer frontend reads
this list (served by the `GET /pools` endpoint) to populate its picker.

`jury_subject` is interpolated into the LLM system prompt so the jury framing
matches the medium ("watercolor pieces", "colored-pencil drawings", ...).

`framing` and `criteria` further customize the jury prompt per pool:
  - `framing`  — the opening role/context sentence(s) that set how the jury
                 should think about this pool (e.g. an "art show juror" for
                 watercolor vs. a fundamentals coach for a learning community).
  - `criteria` — the judging-basis sentence describing what to weigh.
Both are optional; when left as None the prompt falls back to a generic
art-show framing built from `jury_subject` (see shared/llm.py). Only the FIRST
image of each post is sent to the jury, so framing should not assume that
reference photos or before/after shots posted as separate images are visible.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Pool:
    id: str                      # stable key used in DynamoDB partition keys + URLs
    label: str                   # human-facing name shown in the UI
    subreddit: str               # source subreddit for the local fetch step
    jury_subject: str            # noun phrase injected into the jury prompt
    framing: str | None = None   # opening role/context for the jury prompt
    criteria: str | None = None  # judging-basis sentence for the jury prompt


POOLS: dict[str, Pool] = {
    "watercolor": Pool(
        id="watercolor",
        label="Watercolor",
        subreddit="Watercolor",
        jury_subject="watercolor paintings",
        framing="You are a juror in an art show, evaluating watercolor paintings.",
        criteria=(
            "Use your own judgment, informed broadly by typical jury criteria "
            "— technique, composition, use of light and color, mood, "
            "originality, and overall execution — but do not score each "
            "criterion separately."
        ),
    ),
    "coloredpencils": Pool(
        id="coloredpencils",
        label="Colored Pencils",
        subreddit="ColoredPencils",
        jury_subject="colored-pencil drawings",
    ),
    "learntodraw": Pool(
        id="learntodraw",
        label="Learn To Draw",
        subreddit="learntodraw",
        jury_subject="drawings",
        framing=(
            "You are one of a jury of ordinary people — not art teachers or "
            "critics — looking at drawings posted by people learning to draw. "
            "Rank them the way a general audience would: by which pieces are "
            "the most skillful and the most pleasing to look at. Trust your "
            "overall impression and gut reaction, not a technical checklist — "
            "an impressive, appealing drawing should rank above one that is "
            "technically tidy but plain or unremarkable. You are shown only "
            "one image per post; if that image places a reference photo and "
            "the artist's drawing side by side, judge only the hand-drawn "
            "work, not the reference."
        ),
        criteria=(
            "Judge each piece on overall skill and on how impressive and "
            "pleasing it is to an ordinary viewer — the gut sense of \"wow, "
            "that's good\" that would earn it an upvote — rather than scoring "
            "individual fundamentals separately. Reward pieces that look "
            "accomplished, appealing, and finished as a whole."
        ),
    ),
}


def get_pool(pool_id: str) -> Pool:
    try:
        return POOLS[pool_id]
    except KeyError:
        raise KeyError(
            f"Unknown pool '{pool_id}'. Known pools: {sorted(POOLS)}"
        )


def pool_ids() -> list[str]:
    return list(POOLS)
