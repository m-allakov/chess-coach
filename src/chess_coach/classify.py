"""Move quality from the drop in win probability, not raw centipawns.

Losing 200 cp from an equal position is a disaster; losing 200 cp when already
+900 changes nothing. A logistic curve maps cp to win chance so the same cp
swing counts more where the game is still undecided.
"""

from __future__ import annotations

import math

# Lichess' published cp -> win-chance fit, expressed here in percent.
_K = 0.00368208

# Win-probability points (0-100) a move may cost. Tunable.
INACCURACY = 5.0
MISTAKE = 10.0
BLUNDER = 15.0


def win_pct(cp_white: float) -> float:
    """White's win chance in percent for a white-POV centipawn score."""
    return 50 + 50 * (2 / (1 + math.exp(-_K * cp_white)) - 1)


def mover_win_pct(cp_white: float, color: str) -> float:
    w = win_pct(cp_white)
    return w if color == "white" else 100 - w


def classify(wp_loss: float) -> str:
    if wp_loss >= BLUNDER:
        return "blunder"
    if wp_loss >= MISTAKE:
        return "mistake"
    if wp_loss >= INACCURACY:
        return "inaccuracy"
    return "ok"
