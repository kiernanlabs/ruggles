# Project instructions

## AWS account — use `kiernanlabs` only

All AWS work for this project lives in **account `561369487018`** (alias **`kiernanlabs`**, IAM user `claude_code`). Credentials are in the project-root `./.env` and in the `kiernanlabs` AWS CLI profile.

- **Always use the `kiernanlabs` profile / account `561369487018`.** Sessions default to it via `AWS_PROFILE=kiernanlabs` in `.claude/settings.local.json`, so a bare `aws …` is already correct.
- **Never use the default profile / account `387663585850`** (IAM user `joe`). That is an unrelated **DigitalFuel production** account (ArtfulHome, taylor_stitch, tie_bar, lime_lush, boston_scally, the webhook/order APIs, ecommiq/dora tables, etc.). Do not create, modify, or delete anything there. Explicit `aws … --profile default` is blocked by a deny rule.
- The deploy/publish scripts (`infra/deploy_app.py`, `infra/provision_data_resources.py`, `local/publish.py`) call `load_dotenv()` and therefore already target `561369487018` — keep it that way.
- The live stack (all in `561369487018`): DynamoDB `ruggles-art-ranker`, S3 `ruggles-art-ranker-assets`, Lambdas `ruggles-art-ranker-api` / `-worker`, HTTP API `gt5jcvia2l` (`ruggles-art-ranker-http`), IAM role `ruggles-art-ranker-lambda-role`.
- Frontend: S3 static-website bucket `ruggles-art-ranker-site` (origin), fronted by CloudFront `E1II6VC92DUK33` (`dlcrktuhrhek5.cloudfront.net`) on **https://miniartsalon.com** (+ www). ACM cert `40fdfa92-e20d-4668-a1c5-66d6caf76395` (us-east-1), Route 53 zone `Z02065051HH21DZ5JCXW6`.
  - Redeploy frontend: `aws s3 sync reddit_art_ranker_cloud/frontend/ s3://ruggles-art-ranker-site/ --exclude "*" --include "*.html" --include "*.css" --include "*.js"` then invalidate CloudFront: `aws cloudfront create-invalidation --distribution-id E1II6VC92DUK33 --paths "/*"`.
  - `www.miniartsalon.com` 301-redirects to the apex via CloudFront Function `redirect-www-to-apex` (viewer-request on the distribution).
- **CORS is open (`*`)** on the API Lambda `ruggles-art-ranker-api` (`CORS_ORIGIN`) and the assets bucket — the API is intentionally public/no-auth for now (CORS isn't an access control; it only governs browser origins). To restrict real usage later, add API Gateway throttling + API key or WAF, not CORS. Set in `infra/deploy_app.py` and `infra/provision_data_resources.py`.

If an AWS command unexpectedly resolves to `387663585850`, stop and switch to the `kiernanlabs` profile before proceeding.
