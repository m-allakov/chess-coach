"""Thin client for the chess.com public API (no authentication needed)."""

from __future__ import annotations

import httpx

BASE_URL = "https://api.chess.com/pub"
# chess.com asks API users to identify themselves with a User-Agent.
USER_AGENT = "chess-coach-mcp/0.1 (personal project)"


class ChessComClient:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )

    def archives(self, username: str) -> list[str]:
        """Monthly archive URLs, oldest first."""
        r = self._client.get(f"/player/{username.lower()}/games/archives")
        r.raise_for_status()
        return r.json()["archives"]

    def month(self, archive_url: str) -> list[dict]:
        r = self._client.get(archive_url)
        r.raise_for_status()
        return r.json().get("games", [])

    def close(self) -> None:
        self._client.close()


def game_row(game: dict, username: str) -> dict | None:
    """Flatten a chess.com game payload into a DB row from `username`'s view.

    Returns None for games without a PGN (e.g. some variants/abandoned games).
    """
    pgn = game.get("pgn")
    if not pgn:
        return None
    me = username.lower()
    white, black = game["white"], game["black"]
    if white["username"].lower() == me:
        color, mine, theirs = "white", white, black
    elif black["username"].lower() == me:
        color, mine, theirs = "black", black, white
    else:
        return None
    return {
        "url": game["url"],
        "end_time": game["end_time"],
        "time_class": game["time_class"],
        "time_control": game["time_control"],
        "rules": game["rules"],
        "rated": int(bool(game.get("rated"))),
        "color": color,
        "my_rating": mine["rating"],
        "opp_rating": theirs["rating"],
        "result": mine["result"],  # chess.com result code, e.g. "win", "resigned"
        "pgn": pgn,
    }
