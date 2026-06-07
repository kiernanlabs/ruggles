# reddit_art_ranker_cloud

Cloud migration of the local `reddit_art_ranker` experiment. Same ELO + LLM-jury
ranking algorithm, now with:

- a **local pipeline** that ranks a pool and publishes it to AWS, and
- a **serverless consumer app** where anyone submits one piece of art and gets
  its ELO/percentile placement within a pool.

Designed to cost **~$0 when idle** (DynamoDB on-demand + Lambda + HTTP API all
scale to zero). See **[PLAN.md](PLAN.md)** for the full design, rationale, data
model, and cost breakdown.

## Layout

| Path | What |
| --- | --- |
| `shared/` | Storage-agnostic core: ELO math, the OpenRouter jury, pool registry. Runs identically on a laptop and in Lambda. |
| `local/publish.py` | Push a finished SQLite leaderboard → DynamoDB (+ optional S3 image mirror). |
| `backend/` | Lambda handlers (`api_handler`, `worker_handler`), the DynamoDB data layer, and the cloud insertion algorithm. |
| `infra/template.yaml` | AWS SAM stack: HTTP API, two Lambdas, DynamoDB table, S3 bucket. |
| `frontend/index.html` | Single-file SPA: upload → poll → render the verdict. |

## Quickstart

```bash
# 1. Rank a pool locally with the ORIGINAL module (unchanged)
python -m reddit_art_ranker.fetch_pushshift <dump.zst> --subreddit Watercolor
python -m reddit_art_ranker.rank --subreddit Watercolor

# 2. Deploy the cloud stack
cd reddit_art_ranker_cloud
sam build -t infra/template.yaml
sam deploy --guided --parameter-overrides OpenRouterApiKey=sk-or-...

# 3. Publish the pool (run from inside this module dir)
python -m local.publish --sqlite ../reddit_art_ranker/reddit_rankings.db \
    --subreddit Watercolor --pool watercolor --mirror-images

# 4. Point the frontend at the deployed ApiUrl and open it
#    (edit API_URL in frontend/index.html, then host it on S3 or open locally)
```

## Requirements

- Local pipeline / publish: `pip install boto3 openai python-dotenv` + AWS creds.
- `OPENROUTER_API_KEY` in the environment (local) or as a SAM parameter (cloud).
- `sam` CLI + an AWS account for deploy.
