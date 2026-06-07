# reddit_art_ranker_cloud — Implementation Plan

Migrate the local `reddit_art_ranker` experiment into a cloud module with two
halves:

1. **Local initial ranking** (unchanged algorithm) → publishes each pool to AWS.
2. **Serverless consumer app** where anyone submits one piece and gets an
   ELO/percentile placement against that pool, costing ~$0 when idle.

---

## 1. What the existing experiment actually is

Read of the current module establishes the facts the design hinges on:

| Aspect | Finding | Consequence for the cloud |
| --- | --- | --- |
| Image storage | Pieces store **URLs** (`i.redd.it`, `preview.redd.it`), not bytes | No image store needed for the dataset; S3 only for **user uploads** + optional durability mirror |
| "Pools" | One per **subreddit** (`Watercolor`, `ColoredPencils`, `learntodraw`); art ranked only within its pool | Pool id becomes the DynamoDB partition key |
| Core algorithm | `elo.py` + `llm.py` are storage- and provider-agnostic already (OpenRouter vision jury) | Lift into a `shared/` package, run **identically** locally and in Lambda |
| Consumer feature | `insert.py` — drop one piece into a pool, 8 sequential LLM rounds (~1–2 min), produces a percentile + HTML report | This is the thing we expose as a web app; the 1–2 min runtime drives the **async** design |
| State | Single 7 MB SQLite `reddit_rankings.db` | SQLite stays the **local staging** format; a new `publish.py` ships it to DynamoDB |
| Pool size | ~100 pieces per pool | Small enough to load a whole pool into Lambda memory in one query — **no GSI, no pagination concerns** |

Reused **verbatim**: `elo.py`, the jury prompt/JSON-schema logic in `llm.py`,
and the two-phase insertion strategy (random ballpark rounds → focused
close-ELO rounds, anchors frozen).

---

## 2. Architecture decision: DynamoDB + Lambda (not Aurora)

The hard requirement is **~$0 when idle, < $5/mo**. That eliminates Aurora as
the default:

| Option | Idle cost | Fit |
| --- | --- | --- |
| **DynamoDB on-demand** ✅ | ~$0 (pay-per-request + pennies of storage) | Access patterns are trivial key lookups; perfect |
| Aurora Serverless v2 | Min ACU billing; auto-pause adds cold-start + ops complexity | Overkill — we have no relational/analytical queries the cloud path needs |
| Aurora Serverless v1 | Deprecated | ✗ |

The only "query" the consumer path runs is *"give me all eligible pieces in
pool X"* — a single DynamoDB `Query` on the partition key returning ~100 small
items. There is no JOIN, no aggregation, no transaction across pools. A
relational engine buys nothing here. **DynamoDB on-demand it is.**

Compute is **AWS Lambda (Python) behind an HTTP API**. Everything scales to
zero between requests, so idle AWS spend is dominated by a few cents of
DynamoDB/S3 storage. (Active cost is dominated by **OpenRouter LLM calls**,
which are not AWS and are pay-per-use regardless.)

### Why the work is split into two Lambdas

A submission runs ~8 sequential vision calls + a feedback call ≈ 1–2 minutes.
API Gateway HTTP APIs cap integration time at ~30 s, so we **cannot** answer a
submission synchronously. The standard, simple fix:

```
Browser ──PUT image──▶ S3 (presigned)
   │
   ├─POST /submissions─▶ ApiFunction ──async invoke (Event)──▶ WorkerFunction
   │                         │ creates JOB record (status=queued)      │ runs 8 rounds
   │◀──── { job_id } ────────┘                                         │ writes progress
   │                                                                   │ writes result
   └─GET /submissions/{id} (poll every 1.5s) ◀────────────────────────┘ status=done
```

`ApiFunction` only ever does fast things (create a job, presign an upload, read
a job), so it stays well under the gateway timeout. `WorkerFunction` does the
slow LLM work off to the side and streams progress into the job record. This is
why a minute-scale task lives happily on serverless with no SQS/Step Functions
machinery — Lambda's own async (`InvocationType=Event`) is enough for a PoC.
(Production hardening would add an SQS queue + DLQ in front of the worker for
ret/ visibility; called out in §8.)

---

## 3. Module layout

```
reddit_art_ranker_cloud/
├── PLAN.md                  ← this document
├── README.md                ← quickstart
├── shared/                  ← storage/runtime-agnostic core (laptop == Lambda)
│   ├── config.py            ← algorithm constants + env-injected AWS names
│   ├── pools.py             ← pool registry (id, label, subreddit, jury subject)
│   ├── elo.py               ← verbatim from original
│   └── llm.py               ← original jury, jury-subject parameterized per pool
├── local/                   ← initial ranking → cloud publish
│   ├── __init__.py          ← reuse original fetch+rank; only publish is new
│   └── publish.py           ← SQLite ▶ DynamoDB (+ optional S3 image mirror)
├── backend/                 ← Lambda consumer app
│   ├── ddb.py               ← single-table data layer
│   ├── insert.py            ← cloud insertion (in-memory pool, progress callback)
│   ├── feedback.py          ← synthesized feedback + better/similar/worse tiers
│   ├── handlers.py          ← api_handler (routes) + worker_handler (async)
│   └── requirements.txt
├── infra/
│   └── template.yaml        ← AWS SAM: HTTP API, 2 Lambdas, DynamoDB, S3
└── frontend/
    └── index.html           ← vanilla SPA (upload → poll → render)
```

`shared`, `backend`, `local` are **top-level packages** so the identical layout
works for local `python -m local.publish` (run from inside the module dir) and
for the Lambda zip (`CodeUri: ../`, handler `backend.handlers.api_handler`).

---

## 4. Data model (single-table DynamoDB)

Table `art-ranker`, keys `pk` / `sk`. Floats stored as `Decimal`, restored to
float on read (`ddb._floats`).

| Entity | pk | sk | Key attributes |
| --- | --- | --- | --- |
| **Piece** | `POOL#<pool>` | `PIECE#<piece_id>` | title, image_url, source_image_url, upvotes…, **elo**, n_comparisons, n_not_art_flags |
| **Pool meta** | `POOL#<pool>` | `META` | label, jury_subject, n_pieces, elo_min/max |
| **Job** (submission) | `JOB#<job_id>` | `META` | pool_id, status, progress{round,total,elo,percentile}, image_key, **result**, ttl |

Access patterns, all O(1) key ops:

- *Load anchors for pool* → `Query pk=POOL#<pool> AND begins_with(sk,"PIECE#")`.
- *List pools* → small `Scan` filtered to `sk=META` (≤ a handful of pools).
- *Create/poll job* → `Put`/`Get`/`Update` on `JOB#<id>`.

Jobs carry a `ttl` so finished submissions self-expire after 30 days
(DynamoDB TTL, free). No GSI is required at PoC scale; if a public leaderboard
is added later, a `GSI1(pk=POOL#<pool>, sk=elo)` gives sorted-by-rating reads.

---

## 5. The two halves, end to end

### 5a. Local initial ranking → publish

The ranking algorithm is **not** reimplemented — that keeps cloud results
identical to the experiment and avoids drift:

```bash
# 1. Build the leaderboard with the ORIGINAL module (unchanged)
python -m reddit_art_ranker.fetch_pushshift subreddits25/Watercolor_submissions.zst \
    --subreddit Watercolor --limit 100
python -m reddit_art_ranker.rank --subreddit Watercolor

# 2. Publish that pool to AWS (new)
cd reddit_art_ranker_cloud
python -m local.publish --sqlite ../reddit_art_ranker/reddit_rankings.db \
    --subreddit Watercolor --pool watercolor --mirror-images
```

`publish.py` reads the finished SQLite leaderboard and, per piece, optionally
**mirrors the image into S3** (`pool/<pool>/<id>.jpg`) so the cloud app never
depends on reddit hot-links — some `preview.redd.it` URLs are signed and expire.
It then batch-writes one DynamoDB item per piece plus a pool-meta item. It's
**idempotent**: re-run after a re-rank to refresh ELOs. Repeat per pool.

### 5b. Consumer submission (the live app)

1. Browser loads `frontend/index.html`, calls `GET /pools`, shows ready pools.
2. User picks a pool, optionally titles the piece, selects an image.
3. `POST /uploads` → presigned S3 `PUT` URL; browser uploads the file directly
   to S3 (bytes never pass through Lambda).
4. `POST /submissions {pool,image_key,title}` → `ApiFunction` writes a `queued`
   job and async-invokes `WorkerFunction`; returns `job_id` immediately.
5. `WorkerFunction` loads the pool anchors, presigns a `GET` for the candidate
   image, and runs the **identical** two-phase insertion (`backend/insert.py`),
   writing `progress` after each round. Then it synthesizes artist feedback and
   buckets the faced anchors into better/similar/worse tiers, and stores the
   structured `result`.
6. Browser polls `GET /submissions/{id}` every 1.5 s, showing live round
   progress, then renders the percentile verdict + feedback + comparison tiers.

The candidate's ELO is the only rating that moves; published anchors are frozen,
so consumer traffic never perturbs a pool's leaderboard.

---

## 6. Cost model

| Resource | Idle | Active (per submission) |
| --- | --- | --- |
| DynamoDB on-demand | ~$0 (≈ pennies storage for ~300 items) | ~10 reads + ~10 writes ≈ negligible |
| Lambda (api + worker, arm64) | $0 | ~2 min worker @ 512 MB ≈ <$0.002 |
| HTTP API | $0 | $1 per **million** requests |
| S3 | pennies (mirrored pool images) | 1 PUT + a few GETs |
| **OpenRouter (not AWS)** | $0 | ~9 vision calls — the real variable cost |

**Idle AWS spend ≈ $0–1/mo**, comfortably under the $5 target. The only
meaningful per-use cost is the LLM, which is inherent to the product and
pay-as-you-go.

---

## 7. Deploy

```bash
cd reddit_art_ranker_cloud
sam build -t infra/template.yaml
sam deploy --guided \
  --parameter-overrides OpenRouterApiKey=sk-or-... LlmModel=openai/gpt-5.4-mini
# → copy the ApiUrl output into frontend/index.html (API_URL)
aws s3 cp frontend/index.html s3://<a-public-website-bucket>/index.html
```

Then run the §5a publish step for each pool. The frontend is a single static
file — host it on an S3 website bucket (optionally CloudFront for TLS/custom
domain), or open it locally for testing.

---

## 8. Build order & deferred hardening

**Phase 1 — MVP (this scaffold):** `shared/` core, `publish.py`, the two
handlers, SAM stack, static frontend. Deploy, publish Watercolor, submit a
piece end to end.

**Phase 2 — robustness:** put **SQS + DLQ** in front of `WorkerFunction` (retry
+ poison-message isolation) instead of bare async invoke; move
`OPENROUTER_API_KEY` to **Secrets Manager**; lock `CORS_ORIGIN` to the real
frontend origin; add basic abuse controls (per-IP rate limit via API Gateway
throttling, image size/type validation already partly in `_create_upload`).

**Phase 3 — polish:** public per-pool leaderboard (add the `GSI1` sorted by
ELO); CloudFront + custom domain; capture submissions as new candidate pieces
for analytics; optional re-host of the polished HTML report to S3 for shareable
links (port the original `insert.py` renderer if a static artifact is wanted
over the JS-rendered result).

---

## 9. Open questions for you

1. **Pools to launch with** — all three subreddits, or start with Watercolor?
2. **Image durability** — mirror pool images into S3 (recommended, avoids
   reddit link-rot) or keep hot-linking to save the storage/transfer?
3. **Frontend hosting** — S3 website bucket is simplest; want CloudFront + a
   custom domain, or is a plain bucket URL fine for the PoC?
4. **Model** — keep `openai/gpt-5.4-mini`, or pick a cheaper vision model
   (e.g. `google/gemini-2.5-flash`) to cut per-submission cost?
