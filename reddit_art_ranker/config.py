"""Shared config for the reddit_art_ranker module."""

import os
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "reddit_rankings.db"

SUBREDDIT = "Watercolor"
TARGET_POST_COUNT = 100
REDDIT_LISTING = "top"
REDDIT_TIME_FILTER = "month"

LLM_MODEL = "gpt-5.4-mini"
GROUP_SIZE = 5

ELO_K = 32
ELO_INITIAL = 1500.0

RANKING_PASSES = 8
INSERTION_GROUPS = 4
ANCHOR_DECILES = (1, 3, 5, 7, 9)

REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT", "ruggles-art-ranker/0.1 by u/anonymous"
)
