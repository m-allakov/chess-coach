"""Compact, aggregated coaching reports. These are what the LLM reads, so every
function returns small summaries - never raw move lists."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import chess

from .classify import pct
from .moves import my_moves

_KINDS = {"inaccuracy": "inaccuracies", "mistake": "mistakes", "blunder": "blunders"}
WINNING = 75.0   # win chance (%) at which a position counts as "winning"


def _stats(moves: list[dict]) -> dict:
    n = len(moves)
    if not n:
        return {"moves": 0}
    out = {
        "moves": n,
        "avg_wp_loss": pct(sum(m["wp_loss"] for m in moves) / n, 2),
    }
    for kind, label in _KINDS.items():
        out[f"{label}_per_100_moves"] = pct(100 * sum(m["kind"] == kind for m in moves) / n)
    return out


def _group(moves: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for m in moves:
        groups[m[key]].append(m)
    return {k: _stats(v) for k, v in sorted(groups.items())}


def _by_game(moves: list[dict]) -> dict[str, list[dict]]:
    games: dict[str, list[dict]] = defaultdict(list)
    for m in moves:
        games[m["url"]].append(m)
    return games


def weakness_report(conn: sqlite3.Connection, username: str, time_class: str | None = None,
                    last_n: int | None = None) -> dict:
    """Where the player loses the most: by phase, colour and time control, plus
    how often winning positions are thrown away."""
    moves = my_moves(conn, username, time_class, last_n)
    games = _by_game(moves)
    if not moves:
        return {"games": 0, "note": "No analysed games. Run sync_games and analyze_games first."}

    record = {"W": 0, "D": 0, "L": 0}
    winning_games = thrown = 0
    for ms in games.values():
        record[ms[0]["outcome"]] += 1
        peak = max(m["wp_before"] for m in ms)
        if peak >= WINNING:
            winning_games += 1
            thrown += ms[0]["outcome"] != "W"

    blunders = [m for m in moves if m["kind"] == "blunder"]
    blunders_when_winning = sum(m["wp_before"] >= WINNING for m in blunders)
    return {
        "games": len(games),
        "record": record,
        "overall": _stats(moves),
        "by_phase": _group(moves, "phase"),
        "by_color": _group(moves, "color"),
        "by_time_class": _group(moves, "time_class"),
        "winning_positions": {
            "games_reaching_winning_position": winning_games,
            "of_which_not_won": thrown,
            "blunders_made_while_winning": blunders_when_winning,
            "share_of_all_blunders": round(blunders_when_winning / len(blunders), 2) if blunders else 0,
        },
        "definitions": {
            "wp_loss": "win-probability points lost vs the engine's best move (0-100 scale)",
            "winning_position": f"win chance >= {WINNING:.0f}%",
        },
    }


def opening_report(conn: sqlite3.Connection, username: str, min_games: int = 3,
                   time_class: str | None = None, last_n: int | None = None) -> dict:
    """Per opening and colour: results and how good the position was when the
    opening phase ended. Sorted worst first."""
    moves = my_moves(conn, username, time_class, last_n)
    buckets: dict[tuple[str, str], list[list[dict]]] = defaultdict(list)
    for ms in _by_game(moves).values():
        buckets[(ms[0]["opening_family"], ms[0]["color"])].append(ms)

    rows = []
    for (family, color), games in buckets.items():
        if len(games) < min_games:
            continue
        opening_moves = [m for ms in games for m in ms if m["phase"] == "opening"]
        exits = []
        for ms in games:
            opening_only = [m for m in ms if m["phase"] == "opening"]
            if opening_only:
                exits.append(opening_only[-1]["wp_after"])
        wins = sum(ms[0]["outcome"] == "W" for ms in games)
        draws = sum(ms[0]["outcome"] == "D" for ms in games)
        rows.append({
            "opening": family, "color": color, "games": len(games),
            "score_pct": pct(100 * (wins + 0.5 * draws) / len(games), 0),
            "avg_win_chance_leaving_opening": pct(sum(exits) / len(exits)) if exits else None,
            "opening_blunders": sum(m["kind"] == "blunder" for m in opening_moves),
            "opening_mistakes": sum(m["kind"] == "mistake" for m in opening_moves),
            "avg_wp_loss_in_opening": pct(sum(m["wp_loss"] for m in opening_moves) / len(opening_moves), 2)
            if opening_moves else None,
        })
    rows.sort(key=lambda r: (r["score_pct"], r["avg_win_chance_leaving_opening"] or 0))
    return {"min_games": min_games, "openings": rows,
            "note": "avg_win_chance_leaving_opening near 50 means the opening itself is fine; "
                    "well below 50 means the player is already worse after the opening."}


HASTY_SECONDS = 2.0     # a blunder played this fast was barely thought about
THOUGHT_SECONDS = 5.0   # ...and one played after this long was a real calculation error


def time_pressure_report(conn: sqlite3.Connection, username: str, time_class: str | None = None,
                         last_n: int | None = None) -> dict:
    """How the clock affects play: errors by time-pressure zone, how fast time is
    spent, hasty vs. considered blunders, and games lost on time."""
    moves = [m for m in my_moves(conn, username, time_class, last_n) if m["time_zone"]]
    if not moves:
        return {"games": 0, "note": "No analysed games with clock data."}
    games = _by_game(moves)

    zones = {}
    for zone in ("normal", "low", "critical"):
        zm = [m for m in moves if m["time_zone"] == zone]
        zones[zone] = {**_stats(zm), "share_of_moves": round(len(zm) / len(moves), 2)}
    blunders = [m for m in moves if m["kind"] == "blunder"]
    short = [m for m in blunders if m["time_zone"] != "normal"]

    # Blunders made with plenty of time on the clock: did the player rush or misjudge?
    relaxed = [m for m in blunders if m["time_zone"] == "normal"]
    hasty = sum(m["time_spent"] <= HASTY_SECONDS for m in relaxed)
    thought = sum(m["time_spent"] >= THOUGHT_SECONDS for m in relaxed)

    def avg_spent(phase: str) -> float | None:
        pm = [m["time_spent"] for m in moves if m["phase"] == phase]
        return round(sum(pm) / len(pm), 1) if pm else None

    # Share of the starting time still left after the player's 10th move.
    left_after_10 = []
    for ms in games.values():
        tenth = [m for m in ms if m["move_number"] == 10]
        if tenth:
            m = tenth[0]
            left_after_10.append((m["clock_before"] - m["time_spent"]) / m["base_time"])

    timeouts_lost = timeouts_lost_winning = 0
    for ms in games.values():
        if ms[0]["result_code"] == "timeout":
            timeouts_lost += 1
            timeouts_lost_winning += ms[-1]["wp_after"] >= 60
    return {
        "games": len(games),
        "by_time_zone": zones,
        "blunders": {
            "total": len(blunders),
            "made_when_short_of_time": len(short),
            "share_when_short_of_time": round(len(short) / len(blunders), 2) if blunders else 0,
            "with_plenty_of_time": len(relaxed),
            "of_which_hasty": hasty,
            "of_which_considered": thought,
        },
        "avg_seconds_per_move": {p: avg_spent(p) for p in ("opening", "middlegame", "endgame")},
        "avg_share_of_time_left_after_move_10": round(sum(left_after_10) / len(left_after_10), 2)
        if left_after_10 else None,
        "lost_on_time": {
            "games": timeouts_lost,
            "while_position_was_good": timeouts_lost_winning,
        },
        "definitions": {
            "time zones": "share of starting time left before the move: critical < 10%, low < 25%",
            "hasty": f"blunder played in <= {HASTY_SECONDS:.0f}s while time was not short",
            "considered": f"blunder played after >= {THOUGHT_SECONDS:.0f}s while time was not short",
        },
    }


def worst_moments(conn: sqlite3.Connection, username: str, n: int = 5,
                  time_class: str | None = None, last_n: int | None = None,
                  min_depth: int = 16) -> dict:
    """The costliest blunders, each with the position (FEN), the move played and
    the engine's best move, so the player can study them."""
    moves = [m for m in my_moves(conn, username, time_class, last_n)
             if m["kind"] == "blunder" and m["depth"] >= min_depth]
    moves.sort(key=lambda m: m["wp_loss"], reverse=True)
    out = []
    for m in moves[:n]:
        board = chess.Board(m["fen"])
        best = None
        if m["best_uci"]:
            try:
                best = board.san(chess.Move.from_uci(m["best_uci"]))
            except ValueError:
                pass
        out.append({
            "game": m["url"], "move_number": m["move_number"], "color": m["color"], "phase": m["phase"],
            "fen": m["fen"], "played": m["san"], "best": best,
            "played_uci": m["uci"], "best_uci": m["best_uci"],
            "win_chance_before": pct(m["wp_before"]), "win_chance_after": pct(m["wp_after"]),
            "was_winning": m["wp_before"] >= WINNING,
        })
    return {"shown": len(out), "available": len(moves), "blunders": out}
