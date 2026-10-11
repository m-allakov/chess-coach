"""SQLite cache so each game is downloaded and analysed only once."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("CHESS_COACH_DB", Path.home() / ".chess_coach" / "games.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    url          TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    end_time     INTEGER NOT NULL,
    time_class   TEXT NOT NULL,
    time_control TEXT NOT NULL,
    rules        TEXT NOT NULL,
    rated        INTEGER NOT NULL,
    color        TEXT NOT NULL,
    my_rating    INTEGER,
    opp_rating   INTEGER,
    result       TEXT NOT NULL,
    pgn          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_user_time ON games (username, end_time);

-- One row per position; ply i is the position before the i-th move (0-based).
-- The last ply has no move. cp is from White's point of view, clipped to +-1000.
CREATE TABLE IF NOT EXISTS positions (
    url       TEXT NOT NULL,
    ply       INTEGER NOT NULL,
    fen       TEXT NOT NULL,
    turn      TEXT NOT NULL,
    phase     TEXT NOT NULL,
    move_san  TEXT,
    move_uci  TEXT,
    cp        INTEGER NOT NULL,
    best_uci  TEXT,
    depth     INTEGER NOT NULL,
    PRIMARY KEY (url, ply)
);

CREATE TABLE IF NOT EXISTS game_analysis (
    url           TEXT PRIMARY KEY,
    fast_depth    INTEGER NOT NULL,
    deep_depth    INTEGER NOT NULL,
    refined_plies INTEGER NOT NULL,
    seconds       REAL NOT NULL,
    analyzed_at   INTEGER NOT NULL
);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)  # wait out writes from a background analysis run
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_games(conn: sqlite3.Connection, username: str, rows: list[dict]) -> int:
    """Insert rows, skipping games already stored. Returns number of new games."""
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO games
            (url, username, end_time, time_class, time_control, rules, rated,
             color, my_rating, opp_rating, result, pgn)
        VALUES
            (:url, :username, :end_time, :time_class, :time_control, :rules, :rated,
             :color, :my_rating, :opp_rating, :result, :pgn)
        """,
        [{**r, "username": username.lower()} for r in rows],
    )
    conn.commit()
    return conn.total_changes - before
