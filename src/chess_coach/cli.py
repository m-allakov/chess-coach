"""Command line runner for long jobs (a full backlog is too long for one MCP call)."""

from __future__ import annotations

import argparse
import time

from .analysis import analyze_pending, default_engine_factory, status
from .chesscom import ChessComClient
from .config import stockfish_path
from .db import connect
from .sync import sync_games


def main() -> None:
    parser = argparse.ArgumentParser(prog="chess-coach")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="download recent games")
    s.add_argument("username")
    s.add_argument("--months", type=int, default=3)

    a = sub.add_parser("analyze", help="analyse pending games, newest first")
    a.add_argument("username")
    a.add_argument("--batch", type=int, default=25, help="games per round")
    a.add_argument("--max-games", type=int, default=None, help="stop after this many")

    args = parser.parse_args()
    conn = connect()

    if args.cmd == "sync":
        client = ChessComClient()
        try:
            print(sync_games(conn, client, args.username, args.months))
        finally:
            client.close()
        return

    factory = default_engine_factory(stockfish_path())
    total = 0
    t0 = time.monotonic()
    while True:
        remaining = None if args.max_games is None else args.max_games - total
        if remaining is not None and remaining <= 0:
            break
        res = analyze_pending(conn, factory, args.username, min(args.batch, remaining or args.batch))
        total += res["analyzed_now"]
        print(f"{res['games_analyzed']}/{res['games_total']} analysed "
              f"({time.monotonic() - t0:.0f}s)", flush=True)
        if res["analyzed_now"] == 0 or res["games_pending"] == 0:
            break
    print(status(conn, args.username))


if __name__ == "__main__":
    main()
