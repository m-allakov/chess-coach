"""Is a chess.com brilliant 'hard to find'? Measure how much worse the 2nd and 3rd
best moves are in the candidate positions (MultiPV).

Usage: PYTHONPATH=scripts python scripts/onlymove_brilliant.py <db> <stockfish.exe>
"""

from __future__ import annotations

import sys
import time

import chess
import chess.engine

from calibrate_brilliant import load
from chess_coach.classify import mover_win_pct
from chess_coach.db import connect
from chess_coach.moves import my_moves
from tune_brilliant import offered

DEPTH, LINES = 14, 3


def main() -> None:
    conn = connect(sys.argv[1])
    allm = my_moves(conn, "sample_player")
    window = {m["url"].rsplit("/", 1)[-1] for m in allm}
    truth = {t for t in load() if t[0] in window}

    cands = []
    for m in allm:
        if not m["uci"] or m["wp_loss"] > 5 or m["wp_after"] < 50 or m["san"].startswith("O-O"):
            continue
        board = chess.Board(m["fen"])
        if board.piece_type_at(chess.Move.from_uci(m["uci"]).from_square) == chess.KING:
            continue
        if offered(m["fen"], m["uci"]) >= 1:
            cands.append(m)
    print(f"{len(cands)} candidates", flush=True)

    engine = chess.engine.SimpleEngine.popen_uci(sys.argv[2])
    engine.configure({"Threads": 4, "Hash": 256})
    rows = []
    t0 = time.perf_counter()
    for i, m in enumerate(cands, 1):
        board = chess.Board(m["fen"])
        infos = engine.analyse(board, chess.engine.Limit(depth=DEPTH), multipv=LINES)
        wps = [mover_win_pct(info["score"].white().score(mate_score=1000), m["color"]) for info in infos]
        wps += [wps[-1]] * (LINES - len(wps))
        rows.append({
            "pos": (m["url"].rsplit("/", 1)[-1], m["ply"]) in truth,
            "gap2": wps[0] - wps[1], "gap3": wps[0] - wps[2], "legal": board.legal_moves.count(),
        })
        if i % 50 == 0:
            print(f"  {i}/{len(cands)} {time.perf_counter() - t0:.0f}s", flush=True)
    engine.quit()

    pos = [r for r in rows if r["pos"]]
    neg = [r for r in rows if not r["pos"]]
    print(f"\nbrilliants {len(pos)}  look-alikes {len(neg)}")
    for key in ("gap2", "gap3", "legal"):
        print(f"{key:<6} brilliant {sum(r[key] for r in pos)/len(pos):6.1f}   look-alike {sum(r[key] for r in neg)/len(neg):6.1f}")
    print("\nrule: gap to 2nd-best move >= X win-%")
    for x in (0, 3, 5, 10, 15, 20):
        f = [r for r in rows if r["gap2"] >= x]
        tp = sum(r["pos"] for r in f)
        print(f"  gap2>={x:<3} flagged {len(f):>4}  found {tp:>2}/{len(pos)}  precision {tp / max(1, len(f)):5.1%}")


if __name__ == "__main__":
    main()
