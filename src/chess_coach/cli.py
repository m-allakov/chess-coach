"""Command line runner for long jobs (a full backlog is too long for one MCP call)."""

from __future__ import annotations

import argparse
import time

from .analysis import analyze_pending, backfill_details, default_engine_factory, status
from .chesscom import ChessComClient
from .config import stockfish_path
from .db import connect
from .sync import sync_games
from .tagging import tag_pending


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

    b = sub.add_parser("backfill", help="add mate and best-line data to games analysed earlier")
    b.add_argument("username")
    b.add_argument("--batch", type=int, default=25, help="games per round")

    t = sub.add_parser("tag", help="tag costly moves with motifs (hanging pieces, forks, missed captures...)")
    t.add_argument("username")
    t.add_argument("--batch", type=int, default=25, help="games per round")

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
    if args.cmd == "backfill":
        t0 = time.monotonic()
        while True:
            res = backfill_details(conn, factory, args.username, args.batch)
            print(f"{res['backfill_pending']} games left ({time.monotonic() - t0:.0f}s)", flush=True)
            if res["backfilled_now"] == 0 or res["backfill_pending"] == 0:
                break
        return

    if args.cmd == "tag":
        t0 = time.monotonic()
        while True:
            res = tag_pending(conn, factory, args.username, args.batch)
            print(f"{res['motif_pending']} games left ({time.monotonic() - t0:.0f}s, "
                  f"{res['engine_searches']['best_line_fills']} line fills, "
                  f"{res['engine_searches']['threat_probes']} threat probes)", flush=True)
            if res["tagged_now"] == 0 or res["motif_pending"] == 0:
                break
        return

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
