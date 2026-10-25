"""Which cheap features separate chess.com's brilliants from look-alike moves?

Looks only at moves that already pass the loose rule (offers a piece, within 5 win-%
of best, not worse than 50% after) and compares the 39 chess.com brilliants among them
with the rest.

Usage: PYTHONPATH=scripts python scripts/crosstab_brilliant.py <db> <username>
"""

from __future__ import annotations

import sys
from collections import Counter

import chess

from calibrate_brilliant import load
from chess_coach.db import connect
from chess_coach.moves import my_moves
from tune_brilliant import offered


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def main() -> None:
    conn = connect(sys.argv[1])
    allm = my_moves(conn, sys.argv[2])
    window = {m["url"].rsplit("/", 1)[-1] for m in allm}
    truth = {t for t in load() if t[0] in window}

    pos, neg = [], []
    for m in allm:
        if not m["uci"] or m["wp_loss"] > 5 or m["wp_after"] < 50:
            continue
        off = offered(m["fen"], m["uci"])
        if off < 1:
            continue
        board = chess.Board(m["fen"])
        mv = chess.Move.from_uci(m["uci"])
        row = {
            "off": off, "check": "+" in m["san"], "capture": board.is_capture(mv),
            "piece": board.piece_at(mv.from_square).symbol().upper(),
            "before": m["wp_before"], "after": m["wp_after"], "gain": m["wp_after"] - m["wp_before"],
            "move": m["move_number"], "spent": m["time_spent"], "phase": m["phase"],
            "best": m["uci"] == m["best_uci"],
            "pieces": len(board.piece_map()),
        }
        (pos if (m["url"].rsplit("/", 1)[-1], m["ply"]) in truth else neg).append(row)

    print(f"chess.com brilliants: {len(pos)}   look-alikes: {len(neg)}\n")
    print(f"{'feature':<22}{'brilliant':>12}{'look-alike':>12}")
    for key in ("off", "before", "after", "gain", "move", "spent", "pieces"):
        print(f"{key:<22}{mean(r[key] for r in pos):>12}{mean(r[key] for r in neg):>12}")
    for key in ("check", "capture", "best"):
        print(f"{key + ' (share)':<22}{mean(100 * r[key] for r in pos):>11}%{mean(100 * r[key] for r in neg):>11}%")
    print("piece  brilliant:", dict(Counter(r["piece"] for r in pos)))
    print("piece  look-alike:", dict(Counter(r["piece"] for r in neg)))
    print("off    brilliant:", dict(sorted(Counter(r["off"] for r in pos).items())))
    print("off    look-alike:", dict(sorted(Counter(r["off"] for r in neg).items())))


if __name__ == "__main__":
    main()
