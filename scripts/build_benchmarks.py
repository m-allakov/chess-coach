"""Build rating-band benchmarks from public chess.com games.

For every player found by find_peers.py: download recent blitz games into a separate
database, analyse them with the same pipeline used for the user, and compute the same
per-player metrics the coach reports. Only band-level aggregates are written out
(medians and quartiles); no usernames or games leave the local peers database.

Usage: python scripts/build_benchmarks.py <candidates.json> <peers.db> <out.json> [games_per_player] [time_control]
"""

from __future__ import annotations

import json
import statistics
import sys
import time

from chess_coach import coach
from chess_coach.analysis import analyze_pending, default_engine_factory
from chess_coach.chesscom import ChessComClient, game_row
from chess_coach.config import stockfish_path
from chess_coach.db import connect, insert_games
from chess_coach.moves import my_moves


def download_blitz(conn, client: ChessComClient, username: str, want: int,
                   time_control: str | None = None) -> int:
    """Newest rated standard-chess blitz games (optionally one exact time control, so
    players are compared on the same clock), from the last few monthly archives."""
    rows = []
    for url in reversed(client.archives(username)[-(6 if time_control else 2):]):
        time.sleep(0.2)
        for g in reversed(client.month(url)):
            if g.get("rules") != "chess" or g.get("time_class") != "blitz" or not g.get("rated"):
                continue
            if time_control and g.get("time_control") != time_control:
                continue
            row = game_row(g, username)
            if row:
                rows.append(row)
        if len(rows) >= want:
            break
    rows = rows[:want]
    insert_games(conn, username, rows)
    return len(rows)


def player_metrics(conn, username: str) -> dict | None:
    return coach.peer_metrics(my_moves(conn, username))


def summarise(values: list[float]) -> dict:
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    q = statistics.quantiles(values, n=4) if len(values) >= 4 else [values[0], statistics.median(values), values[-1]]
    return {"n": len(values), "p25": round(q[0], 1), "median": round(statistics.median(values), 1), "p75": round(q[2], 1)}


def main() -> None:
    cand_path, db_path, out_path = sys.argv[1:4]
    per_player = int(sys.argv[4]) if len(sys.argv) > 4 else 40
    time_control = sys.argv[5] if len(sys.argv) > 5 else None
    candidates = json.load(open(cand_path))
    conn = connect(db_path)
    client = ChessComClient()
    factory = default_engine_factory(stockfish_path())

    by_band: dict[str, list[dict]] = {}
    t0 = time.time()
    for band, players in candidates.items():
        by_band[band] = []
        for p in players:
            name = p["username"]
            try:
                n = download_blitz(conn, client, name, per_player, time_control)
                analyze_pending(conn, factory, name, limit=per_player)
            except Exception as exc:   # one bad player must not stop the run
                print(f"  skip {band} player: {type(exc).__name__}", flush=True)
                continue
            metrics = player_metrics(conn, name)
            if metrics:
                by_band[band].append(metrics)
            print(f"[{band}] {len(by_band[band])}/{len(players)} players, {n} games ({time.time() - t0:.0f}s)", flush=True)
    client.close()

    keys = ["score_pct", "blunders_per_100_moves", "avg_wp_loss", "lost_on_time_pct_of_games",
            "winning_positions_not_won_pct", "opening_blunders_per_100",
            "middlegame_blunders_per_100", "endgame_blunders_per_100"]
    out = {
        band: {"players": len(rows), "games_per_player": per_player, "time_control": time_control or "any blitz",
               **{k: summarise([r[k] for r in rows]) for k in keys}}
        for band, rows in by_band.items()
    }
    json.dump(out, open(out_path, "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
