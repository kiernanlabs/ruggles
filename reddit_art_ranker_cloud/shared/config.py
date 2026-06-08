"""Shared, storage-agnostic config for the cloud art ranker.

Algorithm constants are identical to the original module so cloud results are
comparable to the local experiment. AWS resource names are read from the
environment (injected by the SAM template at deploy time) with local-dev
fallbacks, so importing this file never requires AWS to be configured.
"""

import os

# ── LLM jury ────────────────────────────────────────────────────────────────
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# OpenRouter model slugs follow `provider/model`. Override with LLM_MODEL.
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-3.1-flash-lite")
GROUP_SIZE = 5

# ── ELO ─────────────────────────────────────────────────────────────────────
ELO_K = 32
ELO_INITIAL = 1500.0

# ── Initial (local) ranking pass ────────────────────────────────────────────
RANKING_PASSES = 8
RANKING_RANDOM_PASSES = 4

# ── Candidate insertion (cloud consumer path) ───────────────────────────────
INSERTION_GROUPS = 8
INSERTION_RANDOM_GROUPS = 4
# Focused rounds sample anchors from the closest-ELO pieces spanning ~this many
# PERCENTILE POINTS of the pool (centered on the candidate). Expressed as a
# fraction of the pool so the span stays ~constant as the pool grows, rather
# than a fixed count that narrows as more pieces are added. A floor count keeps
# small pools workable.
INSERTION_FOCUSED_PCT = 20
INSERTION_FOCUSED_MIN_WINDOW = 20

# Pieces flagged "not art" this many times are excluded from anchor pools.
NOT_ART_EXCLUDE_AT = 2

# ── AWS resource names (env-injected by SAM; safe local defaults) ────────────
# All AWS resources are prefixed "ruggles-" so they're easy to spot/clean up.
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DDB_TABLE = os.getenv("DDB_TABLE", "ruggles-art-ranker")
S3_BUCKET = os.getenv("S3_BUCKET", "ruggles-art-ranker-assets")
WORKER_FUNCTION = os.getenv("WORKER_FUNCTION", "ruggles-art-ranker-worker")

# How long a finished submission/report record lives before DynamoDB TTL
# reaps it (seconds). 30 days — enough for a user to revisit their result link.
SUBMISSION_TTL_SECONDS = 30 * 24 * 3600
