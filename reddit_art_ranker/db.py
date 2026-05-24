"""SQLite schema + helpers for the reddit art ranker."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH, ELO_INITIAL

SCHEMA = """
CREATE TABLE IF NOT EXISTS pieces (
    reddit_id     TEXT PRIMARY KEY,
    subreddit     TEXT NOT NULL,
    title         TEXT,
    author        TEXT,
    permalink     TEXT,
    image_url     TEXT NOT NULL,
    upvotes       INTEGER,
    num_comments  INTEGER,
    upvote_ratio  REAL,
    awards        INTEGER,
    created_utc   REAL,
    fetched_at    TEXT NOT NULL,
    is_candidate  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ratings (
    reddit_id        TEXT PRIMARY KEY,
    elo              REAL NOT NULL,
    n_comparisons    INTEGER NOT NULL DEFAULT 0,
    n_not_art_flags  INTEGER NOT NULL DEFAULT 0,
    last_updated     TEXT NOT NULL,
    FOREIGN KEY (reddit_id) REFERENCES pieces(reddit_id)
);

CREATE TABLE IF NOT EXISTS comparisons (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at                  TEXT NOT NULL,
    model                       TEXT NOT NULL,
    subreddit                   TEXT NOT NULL,
    piece_ids_json              TEXT NOT NULL,
    ranking_json                TEXT NOT NULL,
    rationale                   TEXT,
    per_piece_rationales_json   TEXT,
    candidate_id                TEXT
);
"""

# Idempotent migrations for DBs created before newer columns existed.
_ALTERS = [
    "ALTER TABLE comparisons ADD COLUMN per_piece_rationales_json TEXT",
    "ALTER TABLE ratings ADD COLUMN n_not_art_flags INTEGER NOT NULL DEFAULT 0",
]


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        for sql in _ALTERS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_piece(conn, piece: dict, is_candidate: bool = False) -> None:
    conn.execute(
        """
        INSERT INTO pieces (
            reddit_id, subreddit, title, author, permalink, image_url,
            upvotes, num_comments, upvote_ratio, awards, created_utc,
            fetched_at, is_candidate
        ) VALUES (
            :reddit_id, :subreddit, :title, :author, :permalink, :image_url,
            :upvotes, :num_comments, :upvote_ratio, :awards, :created_utc,
            :fetched_at, :is_candidate
        )
        ON CONFLICT(reddit_id) DO UPDATE SET
            upvotes      = excluded.upvotes,
            num_comments = excluded.num_comments,
            upvote_ratio = excluded.upvote_ratio,
            awards       = excluded.awards,
            fetched_at   = excluded.fetched_at
        """,
        {
            **piece,
            "fetched_at": _now(),
            "is_candidate": 1 if is_candidate else 0,
        },
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO ratings (reddit_id, elo, n_comparisons, last_updated)
        VALUES (?, ?, 0, ?)
        """,
        (piece["reddit_id"], ELO_INITIAL, _now()),
    )


def get_pieces(conn, subreddit: str, include_candidates: bool = True):
    sql = "SELECT * FROM pieces WHERE subreddit = ?"
    if not include_candidates:
        sql += " AND is_candidate = 0"
    return conn.execute(sql, (subreddit,)).fetchall()


def get_ratings(conn, subreddit: str):
    return conn.execute(
        """
        SELECT p.reddit_id, p.title, p.upvotes, p.num_comments, p.upvote_ratio,
               p.permalink, p.image_url, p.is_candidate,
               r.elo, r.n_comparisons, r.n_not_art_flags
        FROM pieces p
        JOIN ratings r ON r.reddit_id = p.reddit_id
        WHERE p.subreddit = ?
        ORDER BY r.elo DESC
        """,
        (subreddit,),
    ).fetchall()


def increment_not_art_flag(conn, reddit_id: str) -> int:
    """Bump the not-art flag counter for a piece. Returns the new value."""
    conn.execute(
        """
        UPDATE ratings
        SET n_not_art_flags = n_not_art_flags + 1, last_updated = ?
        WHERE reddit_id = ?
        """,
        (_now(), reddit_id),
    )
    row = conn.execute(
        "SELECT n_not_art_flags FROM ratings WHERE reddit_id = ?", (reddit_id,)
    ).fetchone()
    return int(row["n_not_art_flags"]) if row else 0


def update_rating(conn, reddit_id: str, new_elo: float, comparisons_delta: int) -> None:
    conn.execute(
        """
        UPDATE ratings
        SET elo = ?, n_comparisons = n_comparisons + ?, last_updated = ?
        WHERE reddit_id = ?
        """,
        (new_elo, comparisons_delta, _now(), reddit_id),
    )


def record_comparison(
    conn,
    model: str,
    subreddit: str,
    piece_ids: list,
    ranking: list,
    rationale: str = "",
    candidate_id: str | None = None,
    per_piece_rationales: list | None = None,
) -> int:
    """per_piece_rationales is a list of {'piece_id': ..., 'rationale': ...} dicts,
    aligned with the `ranking` order (best -> worst)."""
    cur = conn.execute(
        """
        INSERT INTO comparisons (
            created_at, model, subreddit, piece_ids_json, ranking_json,
            rationale, per_piece_rationales_json, candidate_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now(),
            model,
            subreddit,
            json.dumps(piece_ids),
            json.dumps(ranking),
            rationale,
            json.dumps(per_piece_rationales) if per_piece_rationales is not None else None,
            candidate_id,
        ),
    )
    return cur.lastrowid
