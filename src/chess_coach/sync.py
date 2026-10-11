"""Download a player's recent games into the local cache."""

from __future__ import annotations

import sqlite3

from .chesscom import ChessComClient, game_row
from .db import insert_games


def sync_games(
    conn: sqlite3.Connection,
    client: ChessComClient,
    username: str,
    months: int = 3,
) -> dict:
    """Fetch the last `months` monthly archives and store standard-chess games."""
    archive_urls = client.archives(username)[-months:]
    fetched = new = 0
    for url in archive_urls:
        rows = []
        for game in client.month(url):
            if game.get("rules") != "chess":
                continue
            row = game_row(game, username)
            if row:
                rows.append(row)
        fetched += len(rows)
        new += insert_games(conn, username, rows)
    return {"username": username, "months": len(archive_urls), "games_seen": fetched, "games_new": new}
