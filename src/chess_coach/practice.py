"""Spaced repetition for the exercises: which positions are resting, and how often they come back.

Progress lives in the database, next to the games, so it survives a changed browser and the coach
can later see what has actually been practised. Boxes follow the Leitner idea: every correct answer
moves a position one box up and pushes its next appearance further away; a miss sends it back to
box 0 and brings it up again within minutes.
"""

from __future__ import annotations

import sqlite3
import time

INTERVALS_DAYS = (0, 1, 3, 7, 21)       # wait after being in box 0..4
RETRY_MINUTES = 10                      # a missed position returns this soon
MS_DAY = 86_400_000
MS_MINUTE = 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def record(conn: sqlite3.Connection, username: str, pid: str, solved: bool, now_ms: int | None = None) -> dict:
    """Store one answer and schedule the position's next appearance."""
    now = _now_ms() if now_ms is None else now_ms
    user = username.lower()
    row = conn.execute("SELECT box, tried, solved FROM practice WHERE username = ? AND puzzle_id = ?",
                       (user, pid)).fetchone()
    box, tried, won = (row["box"], row["tried"], row["solved"]) if row else (0, 0, 0)
    box = min(len(INTERVALS_DAYS) - 1, box + 1) if solved else 0
    due = now + (INTERVALS_DAYS[box] * MS_DAY if solved else RETRY_MINUTES * MS_MINUTE)
    conn.execute(
        """INSERT INTO practice (username, puzzle_id, box, due_ms, tried, solved, last_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (username, puzzle_id) DO UPDATE SET
             box = excluded.box, due_ms = excluded.due_ms, tried = excluded.tried,
             solved = excluded.solved, last_ms = excluded.last_ms""",
        (user, pid, box, due, tried + 1, won + int(solved), now))
    conn.commit()
    return {"box": box, "due_ms": due}


def resting(conn: sqlite3.Connection, username: str, now_ms: int | None = None) -> set[str]:
    """Positions that are not due yet and should not be offered again."""
    now = _now_ms() if now_ms is None else now_ms
    rows = conn.execute("SELECT puzzle_id FROM practice WHERE username = ? AND due_ms > ?",
                        (username.lower(), now))
    return {r["puzzle_id"] for r in rows}


def summary(conn: sqlite3.Connection, username: str) -> dict:
    """What has been practised so far, for the coach to build on."""
    row = conn.execute(
        "SELECT COUNT(*) AS positions, COALESCE(SUM(tried), 0) AS answers, COALESCE(SUM(solved), 0) AS solved, "
        "COALESCE(SUM(box >= 3), 0) AS mastered FROM practice WHERE username = ?", (username.lower(),)).fetchone()
    return {"positions": row["positions"], "answers": row["answers"], "solved": row["solved"],
            "mastered": row["mastered"]}


def import_progress(conn: sqlite3.Connection, username: str, items: dict) -> int:
    """Take over progress an older version kept in the browser: {puzzle_id: {"box", "due"(ms)}}.
    Existing rows win, so importing twice (or after new answers) never overwrites anything."""
    user = username.lower()
    added = 0
    for pid, p in (items or {}).items():
        try:
            box = max(0, min(len(INTERVALS_DAYS) - 1, int(p.get("box", 0))))
            due = int(p.get("due", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if not isinstance(pid, str) or not 3 <= len(pid) <= 40 or ":" not in pid:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO practice (username, puzzle_id, box, due_ms, tried, solved, last_ms) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)", (user, pid, box, due, int(box > 0), _now_ms()))
        added += cur.rowcount
    conn.commit()
    return added
