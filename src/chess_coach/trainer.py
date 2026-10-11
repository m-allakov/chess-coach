"""Training positions from the player's own blunders.

A puzzle is a position where the player went wrong; the answer is judged by the engine,
so any move that holds the position counts, not just the engine's first choice."""

from __future__ import annotations

import random
import sqlite3

import chess

from .analysis import search
from .classify import mover_win_pct
from .moves import my_moves

MIN_LOSS = 20.0        # the original blunder cost at least this many win-chance points
MIN_BEFORE = 30.0      # skip positions that were already lost: there was nothing to find
MIN_DEPTH = 16         # only blunders confirmed by the deeper pass
CHECK_DEPTH = 12       # depth used to judge the answer given in the app

# How much win chance an answer may cost compared with the engine's best, by verdict.
_VERDICTS = ((5.0, "good"), (12.0, "inaccurate"), (25.0, "mistake"))   # "best" is only the engine's own move


def puzzle_id(url: str, ply: int) -> str:
    return f"{url.rsplit('/', 1)[-1]}:{ply}"


def puzzles(conn: sqlite3.Connection, username: str, n: int = 12, time_class: str | None = None,
            seed: int | None = None, exclude: set[str] | None = None) -> dict:
    """A random selection of positions in which the player's move lost a lot.

    The engine's answer is deliberately not included; it is revealed after a try."""
    skip = exclude or set()
    pool = [m for m in my_moves(conn, username, time_class)
            if m["kind"] == "blunder" and m["wp_loss"] >= MIN_LOSS and m["wp_before"] >= MIN_BEFORE
            and m["depth"] >= MIN_DEPTH and m["best_uci"] and m["uci"] != m["best_uci"]
            and puzzle_id(m["url"], m["ply"]) not in skip]
    random.Random(seed).shuffle(pool)
    return {
        "available": len(pool),
        "puzzles": [{
            "id": puzzle_id(m["url"], m["ply"]), "fen": m["fen"], "color": m["color"],
            "phase": m["phase"], "move_number": m["move_number"],
            "win_chance": round(m["wp_before"]), "game": m["url"],
        } for m in pool[:n]],
    }


def _load(conn: sqlite3.Connection, pid: str, username: str) -> sqlite3.Row | None:
    game_id, _, ply = pid.partition(":")
    if not game_id.isdigit() or not ply.isdigit():
        return None
    return conn.execute(
        """SELECT p.fen, p.best_uci, p.move_uci, p.move_san, p.cp, g.color, g.url
           FROM positions p JOIN games g ON g.url = p.url
           WHERE g.url LIKE ? AND p.ply = ? AND g.username = ?""",
        (f"%/{game_id}", int(ply), username.lower()),
    ).fetchone()


def _verdict(loss: float) -> str:
    for limit, name in _VERDICTS:
        if loss <= limit:
            return name
    return "blunder"


def check(conn: sqlite3.Connection, engine, username: str, pid: str, uci: str) -> dict:
    """Judge an answer. Returns an `error` for unknown puzzles or illegal moves."""
    row = _load(conn, pid, username)
    if row is None:
        return {"error": "Unknown puzzle"}
    board = chess.Board(row["fen"])
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return {"error": "Not a move"}
    if move not in board.legal_moves:
        return {"error": "Illegal move"}

    color = row["color"]
    before = mover_win_pct(row["cp"], color)
    answer_san = board.san(move)
    best = chess.Move.from_uci(row["best_uci"])
    best_san = board.san(best)
    if move == best:
        loss, verdict = 0.0, "best"
    else:
        board.push(move)
        cp, _ = search(engine, board, CHECK_DEPTH)
        loss = max(0.0, before - mover_win_pct(cp, color))
        verdict = _verdict(loss)
    return {
        "verdict": verdict, "solved": verdict in ("best", "good"),
        "answer": answer_san, "best": best_san, "best_uci": row["best_uci"],
        "played_in_game": row["move_san"], "played_uci": row["move_uci"],
        "win_chance_lost": round(loss, 1), "game": row["url"],
    }
