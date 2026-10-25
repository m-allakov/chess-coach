"""Tag the costly moves of analysed games with motifs and keep the result.

Reads the stored analysis. The engine is started for a ply only when the analysis never stored
what a rule needs: the best line at a position (older backfilled games hold it only around
costly moves) or the opponent's threat before a move (never stored). Each such search runs once, at
the fixed depth in config.py, and its result is kept."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter

import chess

from . import config
from .analysis import run_pool, search_detail
from .classify import classify, mover_win_pct
from .mates import is_known, mate_fields
from .motifs import MOTIF_VERSION, UNTAGGED, in_scope, tag_move


def _load(conn: sqlite3.Connection, url: str) -> dict:
    return {
        "url": url,
        "positions": conn.execute(
            "SELECT ply, fen, turn, cp, best_uci, move_uci FROM positions WHERE url=? ORDER BY ply",
            (url,)).fetchall(),
        "detail": {r["ply"]: {"mate": r["mate"], "pv": r["pv"]} for r in conn.execute(
            "SELECT ply, mate, pv FROM position_detail WHERE url=?", (url,))},
        "probes": {r["ply"]: r["pv"].split() for r in conn.execute(
            "SELECT ply, pv FROM threat_probe WHERE url=?", (url,))},
        "has_detail": conn.execute("SELECT 1 FROM game_detail WHERE url=?", (url,)).fetchone() is not None,
    }


def tag_game(engine, game: dict) -> dict:
    """Tag one loaded game. Returns the rows to store and what the engine had to be asked."""
    positions, detail, probes = game["positions"], game["detail"], game["probes"]
    stored = set(detail)
    fills, new_probes, rows, searches = [], [], [], Counter()

    def best_line(ply: int) -> list[str]:
        entry = detail.get(ply)
        if entry and entry["pv"]:
            return entry["pv"].split()
        p = positions[ply]
        if not p["best_uci"]:                      # the game's last position: no move to look at
            return []
        d = search_detail(engine, chess.Board(p["fen"]), config.MOTIF_FILL_DEPTH,
                          [chess.Move.from_uci(p["best_uci"])], config.MOTIF_SEARCH_SECONDS)
        detail[ply] = {"mate": d["mate"], "pv": " ".join(d["pv"])}
        fills.append((ply, d["mate"], " ".join(d["pv"])))
        searches["best_line_fills"] += 1
        return d["pv"]

    def threat_line(ply: int, board: chess.Board) -> list[str] | None:
        if board.is_check():                       # cannot pass the move
            return None
        if ply not in probes:
            passed = board.copy(stack=False)
            passed.push(chess.Move.null())
            # rebuilt from the FEN: the engine library warns about a move stack holding a null move
            d = search_detail(engine, chess.Board(passed.fen()), config.MOTIF_THREAT_DEPTH, None, config.MOTIF_SEARCH_SECONDS)
            probes[ply] = d["pv"]
            new_probes.append((ply, config.MOTIF_THREAT_DEPTH, d["cp"], d["mate"], " ".join(d["pv"])))
            searches["threat_probes"] += 1
        return probes[ply]

    for i in range(len(positions) - 1):
        p, q = positions[i], positions[i + 1]
        if not p["move_uci"]:
            continue
        color = p["turn"]
        loss = max(0.0, mover_win_pct(p["cp"], color) - mover_win_pct(q["cp"], color))
        mates = mate_fields(
            color, detail[i]["mate"] if i in detail else None, detail[i + 1]["mate"] if i + 1 in detail else None,
            is_known(game["has_detail"], i in stored, p["cp"]), is_known(game["has_detail"], i + 1 in stored, q["cp"]))
        if not in_scope(classify(loss), mates["missed_mate"]):
            continue
        board = chess.Board(p["fen"])
        tags = tag_move(board, chess.Move.from_uci(p["move_uci"]), p["best_uci"], best_line(i),
                        best_line(i + 1), threat_line(i, board), mates["missed_mate"])
        rows.append((i, list(tags) or [UNTAGGED], {t: d for t, d in tags.items() if d}))
    return {"url": game["url"], "rows": rows, "fills": fills, "probes": new_probes, "searches": searches}


def _save(conn: sqlite3.Connection, result: dict) -> None:
    url = result["url"]
    conn.execute("DELETE FROM move_motif WHERE url=?", (url,))
    conn.executemany(
        "INSERT INTO move_motif (url, ply, version, tags, details) VALUES (?,?,?,?,?)",
        [(url, ply, MOTIF_VERSION, json.dumps(tags), json.dumps(details)) for ply, tags, details in result["rows"]])
    # Keep a stored mate score; only the line was missing.
    conn.executemany(
        "INSERT INTO position_detail (url, ply, mate, pv) VALUES (?,?,?,?) "
        "ON CONFLICT(url, ply) DO UPDATE SET pv = excluded.pv",
        [(url, ply, mate, pv) for ply, mate, pv in result["fills"]])
    conn.executemany(
        "INSERT OR REPLACE INTO threat_probe (url, ply, depth, cp, mate, pv) VALUES (?,?,?,?,?,?)",
        [(url, *probe) for probe in result["probes"]])
    conn.execute("INSERT OR REPLACE INTO motif_game (url, version, tagged_at) VALUES (?,?,?)",
                 (url, MOTIF_VERSION, int(time.time())))
    conn.commit()


_PENDING = """FROM games g JOIN game_analysis a ON a.url = g.url
              WHERE g.username = ? AND g.rules = 'chess'
                AND g.url NOT IN (SELECT url FROM motif_game WHERE version = ?)"""


def pending_count(conn: sqlite3.Connection, username: str) -> int:
    return conn.execute(f"SELECT COUNT(*) {_PENDING}", (username.lower(), MOTIF_VERSION)).fetchone()[0]


def tag_pending(conn: sqlite3.Connection, engine_factory, username: str, limit: int = 25,
                workers: int | None = None, max_seconds: float | None = None,
                urls: list[str] | None = None) -> dict:
    """Tag up to `limit` analysed games that are not tagged under the current rules, newest first.
    Stops starting new games after `max_seconds`; call again to continue. `urls` restricts the
    batch to those games (used right after an analysis run)."""
    t0 = time.monotonic()
    only = f" AND g.url IN ({','.join('?' * len(urls))})" if urls else ""
    rows = conn.execute(f"SELECT g.url {_PENDING}{only} ORDER BY g.end_time DESC LIMIT ?",
                        (username.lower(), MOTIF_VERSION, *(urls or []), limit)).fetchall()
    games = [_load(conn, r["url"]) for r in rows]
    searches: Counter = Counter()

    def save(result):
        _save(conn, result)
        searches.update(result["searches"])

    done = run_pool(games, engine_factory, workers, max_seconds, tag_game, save) if games else 0
    return {"tagged_now": done, "motif_pending": pending_count(conn, username),
            "engine_searches": {"best_line_fills": searches["best_line_fills"],
                                "threat_probes": searches["threat_probes"]},
            "seconds": round(time.monotonic() - t0, 1)}
