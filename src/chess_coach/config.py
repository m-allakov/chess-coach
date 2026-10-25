"""Locating the Stockfish binary."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_BUNDLED = (
    Path(__file__).resolve().parents[2]
    / "engines" / "stockfish" / "stockfish" / "stockfish-windows-x86-64-universal.exe"
)


def stockfish_path() -> str:
    """CHESS_COACH_STOCKFISH, then the bundled copy, then `stockfish` on PATH."""
    env = os.environ.get("CHESS_COACH_STOCKFISH")
    if env:
        return env
    if _BUNDLED.exists():
        return str(_BUNDLED)
    found = shutil.which("stockfish")
    if found:
        return found
    raise FileNotFoundError(
        "Stockfish not found. Set CHESS_COACH_STOCKFISH or put stockfish on PATH."
    )


# Motif tagging (see motifs.py). The engine is only started for a ply whose stored data
# lacks what a rule needs: the best line after the move, or the opponent's threat before it.
MOTIF_FILL_DEPTH = 14        # fixed search depth for a missing best line
MOTIF_THREAT_DEPTH = 12      # fixed search depth for the null-move threat probe
MOTIF_SEARCH_SECONDS = 3.0   # hard cap per search
