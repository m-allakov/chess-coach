"""Score candidate brilliant-move rules against chess.com's own list.

For every analysed move of the player, compute cheap features, then report how many
of chess.com's brilliants each rule finds (recall) and how many other moves it also
flags (precision). Ground truth only covers the analysed 3-month window.

Usage: python scripts/tune_brilliant.py <db> <username>
"""

from __future__ import annotations

import sys

from calibrate_brilliant import load
from chess_coach.db import connect
from chess_coach.moves import my_moves
from chess_coach.opportunities import sacrificed_material as offered


def main() -> None:
    conn, username = connect(sys.argv[1]), sys.argv[2]
    truth = {(g, n) for g, n in load()}
    moves = [m for m in my_moves(conn, username) if m["uci"] and m["wp_loss"] <= 10]
    window = {m["url"].rsplit("/", 1)[-1] for m in my_moves(conn, username)}
    truth = {t for t in truth if t[0] in window}
    print(f"{len(moves)} candidate moves (loss <= 10), {len(truth)} chess.com brilliants in window")

    feats = []
    for m in moves:
        feats.append((m, offered(m["fen"], m["uci"]), (m["url"].rsplit("/", 1)[-1], m["ply"]) in truth))

    def evaluate(name, rule):
        flagged = [(m, t) for m, off, t in feats if rule(m, off)]
        tp = sum(t for _, t in flagged)
        print(f"{name:<58} flagged {len(flagged):>5}  found {tp:>2}/{len(truth)}  precision {tp / max(1, len(flagged)):5.1%}")

    for off_min in (1, 2, 3):
        for loss_max in (2, 5):
            for after_min in (50, 0):
                evaluate(f"offered>={off_min} loss<={loss_max} after>={after_min}",
                         lambda m, off, o=off_min, l=loss_max, a=after_min:
                         off >= o and m["wp_loss"] <= l and m["wp_after"] >= a)
    print()
    for off_min in (2, 3):
        evaluate(f"offered>={off_min} loss<=5 after>=50 before<80",
                 lambda m, off, o=off_min: off >= o and m["wp_loss"] <= 5 and m["wp_after"] >= 50 and m["wp_before"] < 80)
        evaluate(f"offered>={off_min} loss<=5 after>=50 not-capture",
                 lambda m, off, o=off_min: off >= o and m["wp_loss"] <= 5 and m["wp_after"] >= 50 and "x" not in m["san"])


if __name__ == "__main__":
    main()
