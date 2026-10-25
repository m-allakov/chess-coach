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

from .classify import classify, mover_win_pct

MATE_CP = 1000  # evals are clipped to +-1000 cp so forced mates don't dominate averages
FAST_DEPTH = 12
DEEP_DEPTH = 16
REFINE_LOSS = 80  # pass-1 loss (cp) above which a move is re-searched deeper
PV_PLIES = 5      # length of the stored principal variation
DECIDED_CP = 900  # backfill also re-searches positions this lopsided: they may hide a mate

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


def search_detail(engine, board: chess.Board, depth: int,
                  root_moves: list[chess.Move] | None = None, seconds: float | None = None) -> dict:
    """Search one position. Returns white-POV `cp` (clipped), `best_uci`, `mate` (moves to
    mate, + = White mates, None = no forced mate, 0 = already checkmate) and `pv` (UCI).

    `root_moves` restricts the search to those moves, so a backfill can re-derive the
    mate/PV of a move that is already stored without second-guessing it."""
    if board.is_game_over():
        if board.is_checkmate():
            return {"cp": -MATE_CP if board.turn == chess.WHITE else MATE_CP,
                    "best_uci": None, "mate": 0, "pv": []}
        return {"cp": 0, "best_uci": None, "mate": None, "pv": []}
    extra = {"root_moves": root_moves} if root_moves else {}
    info = engine.analyse(board, chess.engine.Limit(depth=depth, time=seconds), **extra)
    score = info["score"].white()
    pv = [m.uci() for m in info.get("pv", [])[:PV_PLIES]]
    return {"cp": _clip(score.score(mate_score=MATE_CP)), "best_uci": pv[0] if pv else None,
            "mate": score.mate(), "pv": pv}


def search(engine, board: chess.Board, depth: int) -> tuple[int, str | None]:
    """Return (white-POV cp, best move uci) for a position."""
    found = search_detail(engine, board, depth)
    return found["cp"], found["best_uci"]


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
        p.update(search_detail(engine, chess.Board(p["fen"]), fast_depth))
        p["depth"] = fast_depth

    refined = 0
    todo = set()
    for i, loss in enumerate(move_losses(positions)):
        if loss >= refine_loss:
            todo.update((i, i + 1))
    for i in sorted(todo):
        p = positions[i]
        p.update(search_detail(engine, chess.Board(p["fen"]), deep_depth))
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


def _save(conn: sqlite3.Connection, url: str, positions, refined, seconds, fast, deep,
          engine_name: str | None = None) -> None:
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
    conn.executemany(
        "INSERT OR REPLACE INTO position_detail (url, ply, mate, pv) VALUES (?,?,?,?)",
        [(url, i, p["mate"], " ".join(p["pv"])) for i, p in enumerate(positions)],
    )
    conn.execute("INSERT OR REPLACE INTO game_detail (url, engine, complete) VALUES (?,?,1)",
                 (url, engine_name))
    conn.execute(
        "INSERT OR REPLACE INTO game_analysis VALUES (?,?,?,?,?,?)",
        (url, fast, deep, refined, round(seconds, 2), int(time.time())),
    )
    conn.commit()


def _engine_name(engine) -> str | None:
    return getattr(engine, "id", {}).get("name")


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
    saved: list[str] | None = None,
) -> dict:
    """Analyse up to `limit` not-yet-analysed games, newest first.

    Stops starting new games after `max_seconds` so callers (MCP tool calls)
    can return promptly; call again to continue where it left off. If `saved` is given, the
    url of every game analysed now is appended to it.
    """
    rows = conn.execute(
        """SELECT url, pgn FROM games
           WHERE username=? AND rules='chess'
             AND url NOT IN (SELECT url FROM game_analysis)
           ORDER BY end_time DESC LIMIT ?""",
        (username.lower(), limit),
    ).fetchall()

    def job(engine, row):
        t0 = time.monotonic()
        positions, refined = analyse_game(engine, row["pgn"], fast_depth, deep_depth)
        return row["url"], positions, refined, time.monotonic() - t0, _engine_name(engine)

    def save(result):
        url, positions, refined, secs, engine_name = result
        _save(conn, url, positions, refined, secs, fast_depth, deep_depth, engine_name)
        if saved is not None:
            saved.append(url)

    done = run_pool(rows, engine_factory, workers, max_seconds, job, save)
    return {"analyzed_now": done, **status(conn, username)}


def run_pool(rows, engine_factory, workers, max_seconds, job, save) -> int:
    """Run `job(engine, row)` over `rows` on a pool of engines and `save` each result
    on the calling thread. Stops starting new rows after `max_seconds`."""
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
            return job(engine, row)
        finally:
            engines.put(engine)

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for result in ex.map(work, rows):
                if result is None:
                    continue
                save(result)
                done += 1
    finally:
        while not engines.empty():
            engines.get().quit()
    return done


_BACKFILL_SCOPE = """FROM games WHERE username=? AND rules='chess'
             AND url IN (SELECT url FROM game_analysis)
             AND url NOT IN (SELECT url FROM game_detail)"""


def backfill_details(conn: sqlite3.Connection, engine_factory, username: str, limit: int = 25,
                     workers: int | None = None, max_seconds: float | None = None) -> dict:
    """Add mate and principal-variation data to games analysed before it was stored.

    Only the plies a reader needs are searched again: both ends of every move that cost an
    inaccuracy or worse, and any position already decided by 900 cp (it may be a mate). Each
    is searched at the depth it was stored at, restricted to the stored best move, so the
    stored evaluations and best moves never change - only mate and pv are added.
    Newest games first, in batches like `analyze_pending`; call again to continue."""
    rows = conn.execute(
        f"SELECT url {_BACKFILL_SCOPE} ORDER BY end_time DESC LIMIT ?", (username.lower(), limit)
    ).fetchall()
    jobs = [{"url": r["url"], "positions": conn.execute(
        "SELECT ply, fen, turn, cp, best_uci, depth FROM positions WHERE url=? ORDER BY ply",
        (r["url"],)).fetchall()} for r in rows]

    def job(engine, row):
        positions = row["positions"]
        wanted = set()
        for i in range(len(positions) - 1):
            a, b = positions[i], positions[i + 1]
            loss = mover_win_pct(a["cp"], a["turn"]) - mover_win_pct(b["cp"], a["turn"])
            if classify(max(0.0, loss)) != "ok":
                wanted.update((i, i + 1))
        wanted.update(i for i, p in enumerate(positions) if abs(p["cp"]) >= DECIDED_CP)
        found = []
        for i in sorted(wanted):
            p = positions[i]
            root = [chess.Move.from_uci(p["best_uci"])] if p["best_uci"] else None
            d = search_detail(engine, chess.Board(p["fen"]), p["depth"], root)
            found.append((row["url"], p["ply"], d["mate"], " ".join(d["pv"])))
        return row["url"], found, _engine_name(engine)

    def save(result):
        url, found, engine_name = result
        conn.executemany("INSERT OR REPLACE INTO position_detail (url, ply, mate, pv) VALUES (?,?,?,?)", found)
        conn.execute("INSERT OR REPLACE INTO game_detail (url, engine, complete) VALUES (?,?,0)",
                     (url, engine_name))
        conn.commit()

    done = run_pool(jobs, engine_factory, workers, max_seconds, job, save)
    pending = conn.execute(f"SELECT COUNT(*) {_BACKFILL_SCOPE}", (username.lower(),)).fetchone()[0]
    return {"backfilled_now": done, "backfill_pending": pending}


def status(conn: sqlite3.Connection, username: str) -> dict:
    u = username.lower()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE username=? AND rules='chess'", (u,)
    ).fetchone()[0]
    analyzed = conn.execute(
        "SELECT COUNT(*) FROM game_analysis WHERE url IN (SELECT url FROM games WHERE username=?)", (u,)
    ).fetchone()[0]
    return {"games_total": total, "games_analyzed": analyzed, "games_pending": total - analyzed}
