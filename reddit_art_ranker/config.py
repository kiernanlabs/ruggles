"""Shared config for the reddit_art_ranker module."""

import os
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "reddit_rankings.db"

SUBREDDIT = "Watercolor"
TARGET_POST_COUNT = 100
REDDIT_LISTING = "top"
REDDIT_TIME_FILTER = "month"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# OpenRouter model slugs follow `provider/model` (or `provider/model:variant`).
# Override at the CLI with --model, or set LLM_MODEL env var.
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
GROUP_SIZE = 5

ELO_K = 32
ELO_INITIAL = 1500.0

# Initial rank pass: split between random (wide-range) and rating-bucketed
# (focused on close-rated competitors). Random first to establish a ballpark
# ELO before refining within-cohort.
RANKING_PASSES = 8
RANKING_RANDOM_PASSES = 4

# Candidate insertion: same two-phase shape as the initial rank, but per-piece.
# Random rounds give a wide-range ballpark; focused rounds sample fresh anchors
# from a window around the candidate's current ELO each round, so the
# neighborhood shifts as the candidate's true ELO emerges.
INSERTION_GROUPS = 8
INSERTION_RANDOM_GROUPS = 4
# Size of the rating-similarity window for focused rounds: pick the N pieces
# closest by ELO to the candidate, then sample 4 of them per round.
INSERTION_FOCUSED_WINDOW = 30

# Kept for back-compat / scripts that want the old deterministic anchor design.
ANCHOR_DECILES = (1, 3, 5, 7, 9)

REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT", "ruggles-art-ranker/0.1 by u/anonymous"
)
