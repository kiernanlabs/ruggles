"""Pool registry — a "pool" is an isolated competition (originally one per
subreddit). Art is only ever ranked against other art in the same pool.

Adding a pool is data, not code: append an entry here, then run the local
fetch + rank + publish pipeline with --pool <id>. The consumer frontend reads
this list (served by the `GET /pools` endpoint) to populate its picker.

`jury_subject` is interpolated into the LLM system prompt so the jury framing
matches the medium ("watercolor pieces", "colored-pencil drawings", ...).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Pool:
    id: str            # stable key used in DynamoDB partition keys + URLs
    label: str         # human-facing name shown in the UI
    subreddit: str     # source subreddit for the local fetch step
    jury_subject: str  # noun phrase injected into the jury prompt


POOLS: dict[str, Pool] = {
    "watercolor": Pool(
        id="watercolor",
        label="Watercolor",
        subreddit="Watercolor",
        jury_subject="watercolor paintings",
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
