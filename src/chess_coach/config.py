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
