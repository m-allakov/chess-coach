"""Two-pass Stockfish analysis, newest games first.

Pass 1 searches every position at a shallow depth. Pass 2 re-searches only the
moves that look costly, at a deeper depth, so confirmed mistakes are reliable
without paying deep-search prices for the whole game.
Measured on 12 blitz games: depth 12 ~2 s/game, depth 14 ~10 s, depth 18 ~48 s.
"""

from __future__ import annotations

import io
import os
import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import chess
import chess.engine
import chess.pgn

MATE_CP = 1000  # evals are clipped to +-1000 cp so forced mates don't dominate averages
FAST_DEPTH = 12
DEEP_DEPTH = 16
REFINE_LOSS = 80  # pass-1 loss (cp) above which a move is re-searched deeper

_PIECE_VALUES = ((chess.QUEEN, 9), (chess.ROOK, 5), (chess.BISHOP, 3), (chess.KNIGHT, 3))


def phase_of(board: chess.Board) -> str:
    if board.fullmove_number <= 10:
        return "opening"
    material = sum(
        len(board.pieces(pt, color)) * value
        for pt, value in _PIECE_VALUES
        for color in (chess.WHITE, chess.BLACK)
    )
    return "endgame" if material <= 14 else "middlegame"


def _clip(cp: int) -> int:
    return max(-MATE_CP, min(MATE_CP, cp))


def search(engine, board: chess.Board, depth: int) -> tuple[int, str | None]:
    """Return (white-POV cp, best move uci) for a position."""
    if board.is_game_over():
        if board.is_checkmate():
            return (-MATE_CP if board.turn == chess.WHITE else MATE_CP), None
        return 0, None
    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    cp = info["score"].white().score(mate_score=MATE_CP)
    pv = info.get("pv")
    return _clip(cp), (pv[0].uci() if pv else None)


def move_losses(positions: list[dict]) -> list[int]:
    """Centipawn each side gave up with the move played from positions[i]."""
    out = []
    for i in range(len(positions) - 1):
        before, after = positions[i]["cp"], positions[i + 1]["cp"]
        loss = before - after if positions[i]["turn"] == "white" else after - before
        out.append(max(0, loss))
    return out


def analyse_game(engine, pgn: str, fast_depth=FAST_DEPTH, deep_depth=DEEP_DEPTH,
                 refine_loss=REFINE_LOSS) -> tuple[list[dict], int]:
    """Analyse one game. Returns (position rows, number of re-searched plies)."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    positions: list[dict] = []
    for move in game.mainline_moves():
        positions.append(_row(board, move))
        board.push(move)
    positions.append(_row(board, None))

    for p in positions:
        p["cp"], p["best_uci"] = search(engine, chess.Board(p["fen"]), fast_depth)
        p["depth"] = fast_depth

    refined = 0
    todo = set()
    for i, loss in enumerate(move_losses(positions)):
        if loss >= refine_loss:
            todo.update((i, i + 1))
    for i in sorted(todo):
        p = positions[i]
        p["cp"], p["best_uci"] = search(engine, chess.Board(p["fen"]), deep_depth)
        p["depth"] = deep_depth
        refined += 1
    return positions, refined


def _row(board: chess.Board, move: chess.Move | None) -> dict:
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "phase": phase_of(board),
        "move_san": board.san(move) if move else None,
        "move_uci": move.uci() if move else None,
    }


def _save(conn: sqlite3.Connection, url: str, positions, refined, seconds, fast, deep) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO positions
           (url, ply, fen, turn, phase, move_san, move_uci, cp, best_uci, depth)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [
            (url, i, p["fen"], p["turn"], p["phase"], p["move_san"], p["move_uci"],
             p["cp"], p["best_uci"], p["depth"])
            for i, p in enumerate(positions)
        ],
    )
    conn.execute(
        "INSERT OR REPLACE INTO game_analysis VALUES (?,?,?,?,?,?)",
        (url, fast, deep, refined, round(seconds, 2), int(time.time())),
    )
    conn.commit()


def default_engine_factory(path: str, threads: int = 2, hash_mb: int = 128):
    def make():
        engine = chess.engine.SimpleEngine.popen_uci(path)
        engine.configure({"Threads": threads, "Hash": hash_mb})
        return engine
    return make


def analyze_pending(
    conn: sqlite3.Connection,
    engine_factory,
    username: str,
    limit: int = 25,
    workers: int | None = None,
    max_seconds: float | None = None,
    fast_depth: int = FAST_DEPTH,
    deep_depth: int = DEEP_DEPTH,
) -> dict:
    """Analyse up to `limit` not-yet-analysed games, newest first.

    Stops starting new games after `max_seconds` so callers (MCP tool calls)
    can return promptly; call again to continue where it left off.
    """
    rows = conn.execute(
        """SELECT url, pgn FROM games
           WHERE username=? AND rules='chess'
             AND url NOT IN (SELECT url FROM game_analysis)
           ORDER BY end_time DESC LIMIT ?""",
        (username.lower(), limit),
    ).fetchall()
    workers = workers or max(1, min(4, (os.cpu_count() or 4) // 4))
    deadline = time.monotonic() + max_seconds if max_seconds else None

    engines: queue.Queue = queue.Queue()
    for _ in range(min(workers, max(1, len(rows)))):
        engines.put(engine_factory())

    def work(row):
        if deadline and time.monotonic() > deadline:
            return None
        engine = engines.get()
        try:
            t0 = time.monotonic()
            positions, refined = analyse_game(engine, row["pgn"], fast_depth, deep_depth)
            return row["url"], positions, refined, time.monotonic() - t0
        finally:
            engines.put(engine)

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for result in ex.map(work, rows):
                if result is None:
                    continue
                url, positions, refined, secs = result
                _save(conn, url, positions, refined, secs, fast_depth, deep_depth)
                done += 1
    finally:
        while not engines.empty():
            engines.get().quit()
    return {"analyzed_now": done, **status(conn, username)}


def status(conn: sqlite3.Connection, username: str) -> dict:
    u = username.lower()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE username=? AND rules='chess'", (u,)
    ).fetchone()[0]
    analyzed = conn.execute(
        "SELECT COUNT(*) FROM game_analysis WHERE url IN (SELECT url FROM games WHERE username=?)", (u,)
    ).fetchone()[0]
    return {"games_total": total, "games_analyzed": analyzed, "games_pending": total - analyzed}
