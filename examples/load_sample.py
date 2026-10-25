"""Load the anonymized sample games into the local cache, so the tools can be tried without
a chess.com account or network access.

The 30 games are 3+0 blitz games of one real player. Usernames are replaced
(sample_player, opponent_01...) and game links point to example.invalid.

Usage: python examples/load_sample.py   (writes to CHESS_COACH_DB, or ~/.chess_coach/games.db)
"""

from __future__ import annotations

import json
from pathlib import Path

from chess_coach.db import connect, insert_games

SAMPLE = Path(__file__).with_name("sample_games.json")
USERNAME = "sample_player"


def main() -> None:
    rows = json.loads(SAMPLE.read_text(encoding="utf-8"))
    new = insert_games(connect(), USERNAME, rows)
    print(f"{new} new games for {USERNAME} ({len(rows)} in the sample)")


if __name__ == "__main__":
    main()
