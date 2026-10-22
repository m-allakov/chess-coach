"""Season-level views: how each recent game unfolded, and how results fall over days."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

import chess

from .classify import BLUNDER, classify, mover_win_pct, pct
from .moves import opening_names, outcome


def game_curves(conn: sqlite3.Connection, username: str, n: int = 60,
                time_class: str | None = None) -> list[dict]:
    """The last `n` analysed games, newest first, each with the player's win chance after
    every ply (0-100) and the plies where the player's own move was a blunder."""
    where = "g.username = ? AND g.url IN (SELECT url FROM game_analysis)"
    args: list = [username.lower()]
    if time_class:
        where += " AND g.time_class = ?"
        args.append(time_class)
    games = conn.execute(
        f"""SELECT g.url, g.end_time, g.time_class, g.color, g.result, g.my_rating, g.opp_rating, g.pgn
            FROM games g WHERE {where} ORDER BY g.end_time DESC LIMIT ?""",
        (*args, n),
    ).fetchall()
    out = []
    for g in games:
        rows = conn.execute("SELECT turn, cp FROM positions WHERE url = ? ORDER BY ply", (g["url"],)).fetchall()
        if len(rows) < 2:
            continue
        curve = [pct(mover_win_pct(r["cp"], g["color"])) for r in rows]
        blunders = [i + 1 for i, r in enumerate(rows[:-1])
                    if r["turn"] == g["color"] and curve[i] - curve[i + 1] >= BLUNDER]
        out.append({
            "id": g["url"].rsplit("/", 1)[-1], "url": g["url"], "end_time": g["end_time"],
            "time_class": g["time_class"], "color": g["color"], "outcome": outcome(g["result"]),
            "result_code": g["result"], "my_rating": g["my_rating"], "opp_rating": g["opp_rating"],
            "opening": opening_names(g["pgn"])[0], "curve": curve, "blunders": blunders,
        })
    return out


def daily_results(conn: sqlite3.Connection, username: str, days: int = 140,
                  time_class: str | None = None) -> list[dict]:
    """Wins, draws and losses per local calendar day, oldest first, for the last `days`
    days that contain games."""
    where = "username = ?"
    args: list = [username.lower()]
    if time_class:
        where += " AND time_class = ?"
        args.append(time_class)
    per_day: dict[str, dict] = defaultdict(lambda: {"W": 0, "D": 0, "L": 0})
    for r in conn.execute(f"SELECT end_time, result FROM games WHERE {where}", args):
        day = datetime.fromtimestamp(r["end_time"]).date().isoformat()
        per_day[day][outcome(r["result"])] += 1
    rows = [{"date": d, "games": sum(v.values()), **v} for d, v in sorted(per_day.items())]
    return rows[-days:]


def game_detail(conn: sqlite3.Connection, username: str, game_id: str) -> dict | None:
    """One analysed game, position by position, for the game viewer.

    `plies[i]` is the position before move i (the last entry has no move). Win chances
    are from the player's point of view; `kind` grades the move played from that
    position by the side that played it, so the opponent's mistakes are marked too."""
    g = conn.execute(
        """SELECT g.url, g.end_time, g.time_class, g.color, g.result, g.my_rating, g.opp_rating, g.pgn
           FROM games g WHERE g.username = ? AND g.url LIKE ?
             AND g.url IN (SELECT url FROM game_analysis)""",
        (username.lower(), f"%/{game_id}"),
    ).fetchone()
    if g is None:
        return None
    rows = conn.execute(
        "SELECT ply, fen, turn, move_san, move_uci, best_uci, cp FROM positions WHERE url = ? ORDER BY ply",
        (g["url"],),
    ).fetchall()
    plies = []
    for i, r in enumerate(rows):
        entry = {
            "ply": r["ply"], "fen": r["fen"], "mover": r["turn"], "mine": r["turn"] == g["color"],
            "san": r["move_san"], "uci": r["move_uci"], "best_uci": r["best_uci"], "best_san": None,
            "wp": pct(mover_win_pct(r["cp"], g["color"])), "kind": None,
        }
        if r["best_uci"]:
            try:
                entry["best_san"] = chess.Board(r["fen"]).san(chess.Move.from_uci(r["best_uci"]))
            except ValueError:
                pass
        if i + 1 < len(rows) and r["move_uci"]:
            before = mover_win_pct(r["cp"], r["turn"])
            after = mover_win_pct(rows[i + 1]["cp"], r["turn"])
            entry["kind"] = classify(max(0.0, before - after))
            entry["loss"] = pct(max(0.0, before - after))
        plies.append(entry)
    return {
        "id": game_id, "url": g["url"], "end_time": g["end_time"], "time_class": g["time_class"],
        "color": g["color"], "outcome": outcome(g["result"]), "result_code": g["result"],
        "my_rating": g["my_rating"], "opp_rating": g["opp_rating"],
        "opening": opening_names(g["pgn"])[0], "plies": plies,
    }
