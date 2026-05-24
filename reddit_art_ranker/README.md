# reddit_art_ranker

One-off tool that pulls recent art from a subreddit, ranks it head-to-head with
an LLM jury, and lets you drop a new piece into the established ranking.

## How it works

- **fetch_reddit.py** — pulls N image posts from a subreddit into SQLite (live Reddit API via PRAW)
- **fetch_pushshift.py** — same output schema, but reads from a pushshift archive dump (NDJSON or `.zst`). Use this if you don't have Reddit API credentials
- **rank.py** — runs the LLM on groups of 5 pieces; each 5-way ranking is
  decomposed into 10 pairwise outcomes that feed an ELO update. Early passes
  use random grouping for coverage; later passes bucket by current rating so
  close-rated pieces face each other (that's where ELO learns most)
- **insert.py** — drops a candidate into 4 groups against decile-spaced anchor
  pieces; only the candidate's rating updates
- **analyze.py** — prints the leaderboard and Spearman rho between ELO and
  Reddit engagement (upvotes, comments, upvote-ratio)

All state lives in `reddit_rankings.db` (SQLite, sibling of this file).

## Setup

1. `pip install -r ../requirements.txt`
2. Create a Reddit script app at <https://www.reddit.com/prefs/apps>
3. Add to your `.env`:

   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USER_AGENT=ruggles-art-ranker/0.1 by u/your_username
   OPENAI_API_KEY=...   # already set if you've used evaluate_images.py
   ```

## Usage

Run from the project root (so `python -m reddit_art_ranker.*` resolves).

**Option A — pushshift archive (no Reddit API needed)**

Drop a subreddit dump from <https://academictorrents.com/details/3e3f64dee22dc304cdd2546254ca1f8e8ae542b4>
into the repo, then point the loader at the NDJSON or `.zst`:

```bash
python -m reddit_art_ranker.fetch_pushshift \
    reddit_art_ranker/reddit/subreddits25/Watercolor_submissions/Watercolor_submissions \
    --since 2024-01-01 --limit 100 --sort score
```

Defaults: keep posts with score >= 20, since 2023-01-01, take top 100 by score.
Reading the `.zst` directly requires `pip install zstandard`; the decompressed
NDJSON works with stdlib only.

**Option B — live Reddit API (requires a script app)**

```bash
python -m reddit_art_ranker.fetch_reddit   # uses REDDIT_CLIENT_ID/SECRET
```

**Then for either path:**

```bash
# Rank them (~160 LLM calls at defaults: 8 passes * 20 groups)
python -m reddit_art_ranker.rank

# Leaderboard + ELO-vs-engagement correlation
python -m reddit_art_ranker.analyze --csv

# Drop a new piece in
python -m reddit_art_ranker.insert \
    --image-url https://i.imgur.com/your-piece.jpg \
    --title "My watercolor"
```

## Knobs

All defaults live in `config.py`:

| Constant | Default | Meaning |
| --- | --- | --- |
| `SUBREDDIT` | `Watercolor` | Source subreddit |
| `TARGET_POST_COUNT` | `100` | Pieces fetched per run |
| `GROUP_SIZE` | `5` | Pieces per LLM call |
| `RANKING_PASSES` | `8` | Full passes over the pool |
| `INSERTION_GROUPS` | `4` | Groups per candidate insertion |
| `ANCHOR_DECILES` | `(1, 3, 5, 7, 9)` | Where in the rank to draw anchors |
| `LLM_MODEL` | `gpt-5.4-mini` | Vision-capable OpenAI model |
| `ELO_K` | `32` | ELO update strength |

## Notes

- Position bias is mitigated by shuffling image order within each prompt
  (`llm.py:rank_group(..., shuffle=True)`).
- `pieces.is_candidate = 1` flags user-submitted pieces. They're excluded from
  the established pool (no self-referencing during insertion).
- `comparisons` table logs every LLM judgment with the model name, the input
  piece IDs, and the resulting ranking — handy for re-running ELO with
  different K values or auditing weird outcomes.
