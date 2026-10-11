"""Measure how much Stockfish depth matters for finding the user's mistakes.

Each position is searched once to MAX_DEPTH; the score reported at every
intermediate depth is recorded (iterative deepening gives this for free).
Each shallower depth is then compared with the deepest result.

Usage: python scripts/depth_benchmark.py <db> <stockfish.exe> [n_games]
"""

from __future__ import annotations

import io
import random
import sqlite3
import statistics
import sys
import time

import chess
import chess.engine
import chess.pgn

MAX_DEPTH = 20
DEPTHS = [6, 8, 10, 12, 14, 16, 18, 20]
MATE_CP = 1000  # clip evals to +-1000 cp so mates don't dominate averages
BLUNDER_CP = 200
MISTAKE_CP = 100


def clip(score: chess.engine.PovScore) -> int:
    cp = score.white().score(mate_score=MATE_CP)
    return max(-MATE_CP, min(MATE_CP, cp))


def terminal_score(board: chess.Board) -> int:
    if board.is_checkmate():
        return -MATE_CP if board.turn == chess.WHITE else MATE_CP
    return 0


def search(engine, board):
    """Return ({depth: white_cp}, {depth: seconds}) for one position."""
    scores: dict[int, int] = {}
    times: dict[int, float] = {}
    t0 = time.perf_counter()
    with engine.analysis(board, chess.engine.Limit(depth=MAX_DEPTH)) as a:
        for info in a:
            if "depth" in info and "score" in info and "pv" in info:
                d = info["depth"]
                scores[d] = clip(info["score"])
                times[d] = time.perf_counter() - t0
    return scores, times


def at_depth(scores: dict[int, int], d: int) -> int:
    ds = [x for x in scores if x <= d]
    return scores[max(ds)] if ds else scores[min(scores)]


def analyse_game(engine, pgn: str):
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    positions = []
    for move in game.mainline_moves():
        positions.append((board.copy(), board.turn))
        board.push(move)
    final = terminal_score(board) if board.is_game_over() else None
    per_pos = []
    for b, _ in positions:
        per_pos.append(search(engine, b))
    if final is not None:
        per_pos.append(({MAX_DEPTH: final}, {MAX_DEPTH: 0.0}))
    else:
        per_pos.append(search(engine, board))
    return [t for _, t in positions], per_pos


def losses(turns, per_pos, d):
    """Centipawn loss for every move at search depth d."""
    out = []
    for i, turn in enumerate(turns):
        before = at_depth(per_pos[i][0], d)
        after = at_depth(per_pos[i + 1][0], d)
        loss = before - after if turn == chess.WHITE else after - before
        out.append(max(0, loss))
    return out


def main() -> None:
    db, sf = sys.argv[1], sys.argv[2]
    n_games = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT url, time_class, pgn FROM games WHERE rated=1 AND time_class IN ('blitz','rapid') ORDER BY end_time DESC LIMIT 400"
    ).fetchall()
    random.Random(7).shuffle(rows)
    rows = rows[:n_games]

    engine = chess.engine.SimpleEngine.popen_uci(sf)
    engine.configure({"Threads": 4, "Hash": 256})

    all_losses = {d: [] for d in DEPTHS}
    game_acpl = {d: [] for d in DEPTHS}
    cum_time = {d: 0.0 for d in DEPTHS}
    n_pos = 0
    t_start = time.perf_counter()
    for k, (url, tc, pgn) in enumerate(rows, 1):
        turns, per_pos = analyse_game(engine, pgn)
        n_pos += len(per_pos)
        for d in DEPTHS:
            l = losses(turns, per_pos, d)
            all_losses[d].append(l)
            game_acpl[d].append(statistics.mean(l) if l else 0)
            cum_time[d] += sum(at_dep for _, t in per_pos for at_dep in [t[max((x for x in t if x <= d), default=min(t))]])
        print(f"[{k}/{len(rows)}] {tc} {len(turns)} plies, elapsed {time.perf_counter()-t_start:.0f}s", flush=True)
    engine.quit()

    ref = all_losses[MAX_DEPTH]
    flat_ref = [x for g in ref for x in g]
    ref_blunders = {i for i, x in enumerate(flat_ref) if x >= BLUNDER_CP}
    ref_big = {i for i, x in enumerate(flat_ref) if x >= MISTAKE_CP}

    print(f"\n{len(rows)} games, {n_pos} positions. Reference = depth {MAX_DEPTH}")
    print(f"Reference blunders (>= {BLUNDER_CP}cp): {len(ref_blunders)}; mistakes+ (>= {MISTAKE_CP}cp): {len(ref_big)}\n")
    print("depth | s/game | blunder recall | blunder precision | mistake+ recall | game ACPL MAE | ACPL (all)")
    for d in DEPTHS:
        flat = [x for g in all_losses[d] for x in g]
        b = {i for i, x in enumerate(flat) if x >= BLUNDER_CP}
        m = {i for i, x in enumerate(flat) if x >= MISTAKE_CP}
        rec = len(b & ref_blunders) / len(ref_blunders) if ref_blunders else 1
        prec = len(b & ref_blunders) / len(b) if b else 1
        mrec = len(m & ref_big) / len(ref_big) if ref_big else 1
        mae = statistics.mean(abs(a - r) for a, r in zip(game_acpl[d], game_acpl[MAX_DEPTH]))
        print(f"{d:>5} | {cum_time[d]/len(rows):>6.1f} | {rec:>14.1%} | {prec:>17.1%} | {mrec:>15.1%} | {mae:>13.1f} | {statistics.mean(flat):.1f}")


if __name__ == "__main__":
    main()
