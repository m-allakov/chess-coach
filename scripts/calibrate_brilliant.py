"""Compare our brilliant-move detector with chess.com's own list.

Reads chess.com's brilliant moves (gameid:move pairs) and looks each one up among
the analysed games, printing the features our detector uses so the definition can be
tuned against ground truth.

Usage: python scripts/calibrate_brilliant.py <db> <stockfish.exe>
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import chess

from chess_coach import opportunities as o
from chess_coach.db import connect
from chess_coach.moves import my_moves

LIST = Path(__file__).with_name("chesscom_brilliants.txt")


def load() -> list[tuple[str, int]]:
    line = [l for l in LIST.read_text().splitlines() if l and not l.startswith("#")][0]
    return [(g, int(m)) for g, m in (p.split(":") for p in line.split(","))]


def main() -> None:
    db, sf = sys.argv[1], sys.argv[2]
    conn = connect(db)
    moves = my_moves(conn, "sample_player")
    by_key = {(m["url"].rsplit("/", 1)[-1], m["ply"]): m for m in moves}
    games = {k[0] for k in by_key}
    truth = load()

    in_db = [(g, n) for g, n in truth if g in games]
    print(f"chess.com lists {len(truth)} brilliants; {len(in_db)} are in analysed games")

    # The `move` parameter is the 0-based ply of the move (verified: 53/53 are the player's own).
    rows = []
    for g, n in in_db:
        m = by_key[(g, n)]
        board = chess.Board(m["fen"])
        move = chess.Move.from_uci(m["uci"])
        piece = board.piece_at(move.from_square).symbol().upper()
        rows.append({
            "game": g, "n": m["move_number"], "san": m["san"], "piece": piece,
            "capture": board.is_capture(move),
            "best": m["uci"] == m["best_uci"],
            "loss": m["wp_loss"], "before": m["wp_before"], "after": m["wp_after"],
            "offered": o.sacrificed_material(m["fen"], m["uci"]),
            "kind": m["kind"], "phase": m["phase"],
        })

    print(f"\n{'game':>13} mv {'san':<8} pc cap best  loss before after offered kind")
    for r in sorted(rows, key=lambda r: r["offered"]):
        print(f"{r['game']:>13} {r['n']:>2} {r['san']:<8} {r['piece']}  {'x' if r['capture'] else '.'}   "
              f"{'Y' if r['best'] else '.'}  {r['loss']:5.1f} {r['before']:6.0f} {r['after']:5.0f} "
              f"{r['offered']:>7} {r['kind']}")

    def share(pred, label):
        print(f"  {label:<46} {sum(1 for r in rows if pred(r))}/{len(rows)}")

    print("\nHow many of chess.com's brilliants satisfy each condition:")
    share(lambda r: r["best"], "engine's best move")
    share(lambda r: r["loss"] <= 2, "within 2 win-% of best")
    share(lambda r: r["loss"] <= 5, "within 5 win-% of best")
    share(lambda r: r["kind"] in ("mistake", "blunder"), "we call it a mistake/blunder")
    share(lambda r: r["before"] < 80, "win chance before < 80%")
    share(lambda r: r["after"] >= 50, "win chance after >= 50%")
    share(lambda r: r["offered"] >= 1, "static exchange says piece is offered (>=1)")
    share(lambda r: r["capture"], "move is a capture")
    print("  moved piece:", dict(Counter(r["piece"] for r in rows)))
    print("  phase:", dict(Counter(r["phase"] for r in rows)))


if __name__ == "__main__":
    main()
