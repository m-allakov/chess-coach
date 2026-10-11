"""Behavioural patterns across games: playing on after losses (tilt), tiredness over a
session, time of day, and how often a clearly worse position is saved.

All comparisons use the noise-aware test in stats.compare_rates, so a pattern is only
called clear when it is bigger than sampling noise would explain."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .coach import FINAL_STRETCH, game_summaries
from .moves import my_moves
from .stats import compare_means, compare_rates

SESSION_GAP_MINUTES = 30   # a longer pause between two games starts a new session
CLEARLY_WORSE = 20.0       # win chance (%) at or below which the position was clearly worse


def _block(games: list[dict]) -> dict:
    """Score and blunder rate of a group of games, with the sample sizes they rest on."""
    n = len(games)
    if not n:
        return {"games": 0}
    moves = sum(g["moves"] for g in games)
    wins = sum(g["outcome"] == "W" for g in games)
    draws = sum(g["outcome"] == "D" for g in games)
    return {
        "games": n,
        "score_pct": round(100 * (wins + 0.5 * draws) / n, 1),
        "blunders_per_100_moves": round(100 * sum(g["blunders"] for g in games) / moves, 1),
        "lost_on_time_pct": round(100 * sum(g["result_code"] == "timeout" for g in games) / n, 1),
        "_game_blunder_rates": [100 * g["blunders"] / g["moves"] for g in games],
    }


def _verdicts(group: dict, base: dict) -> dict:
    """How `group` differs from `base`. Score is compared as a share of games; blunder
    rate as the average of per-game rates (see stats.compare_means)."""
    if group.get("games", 0) == 0 or base.get("games", 0) == 0:
        return {}
    comparisons = {
        "score_pct": (compare_rates(group["score_pct"], group["games"], base["score_pct"], base["games"]), True),
        "blunders_per_100_moves": (
            compare_means(group["_game_blunder_rates"], base["_game_blunder_rates"]), False),
    }
    out = {}
    for key, (cmp, higher_better) in comparisons.items():
        verdict = "no clear difference" if not cmp["clear"] else (
            "better" if (cmp["change"] > 0) == higher_better else "worse")
        out[key] = {"difference": cmp["change"], "noise_margin": cmp["noise_margin"], "verdict": verdict}
    return out


def _public(block: dict) -> dict:
    return {k: v for k, v in block.items() if not k.startswith("_")}


def split_sessions(games_oldest_first: list[dict], gap_minutes: float) -> list[list[dict]]:
    """Group games into sessions: a pause longer than `gap_minutes` between the end of
    two games starts a new one."""
    sessions: list[list[dict]] = []
    for g in games_oldest_first:
        if sessions and g["end_time"] - sessions[-1][-1]["end_time"] <= gap_minutes * 60:
            sessions[-1].append(g)
        else:
            sessions.append([g])
    return sessions


def tilt_from_games(games_newest_first: list[dict], gap_minutes: float = SESSION_GAP_MINUTES) -> dict:
    games = sorted(games_newest_first, key=lambda g: g["end_time"])
    sessions = split_sessions(games, gap_minutes)

    by_losses: dict[str, list[dict]] = {"0": [], "1": [], "2": [], "3+": []}
    by_position: dict[str, list[dict]] = {"games 1-3": [], "games 4-8": [], "game 9+": []}
    for session in sessions:
        streak = 0
        for i, g in enumerate(session):
            by_losses["3+" if streak >= 3 else str(streak)].append(g)
            by_position["games 1-3" if i < 3 else "games 4-8" if i < 8 else "game 9+"].append(g)
            streak = streak + 1 if g["outcome"] == "L" else 0

    blocks = {k: _block(v) for k, v in by_losses.items()}
    base = blocks["0"]
    after_losses = {
        k: {**_public(b), **({"vs_games_not_after_a_loss": _verdicts(b, base)} if k != "0" else {})}
        for k, b in blocks.items()
    }
    pos = {k: _block(v) for k, v in by_position.items()}
    fatigue = {
        k: {**_public(b), **({"vs_games_1_to_3": _verdicts(b, pos["games 1-3"])} if k != "games 1-3" else {})}
        for k, b in pos.items()
    }
    clear = [f"{k} losses in a row: {m}" for k, b in blocks.items() if k != "0"
             for m, v in _verdicts(b, base).items() if v["verdict"] == "worse"]
    return {
        "sessions": len(sessions),
        "session_gap_minutes": gap_minutes,
        "games": len(games),
        "after_consecutive_losses": after_losses,
        "position_in_session": fatigue,
        "clear_signs_of_tilt": clear or "none that rise above sampling noise",
        "note": "A session ends after a pause longer than the gap between the end of two games. "
                "'Worse' is reported only when the difference is larger than sampling noise.",
    }


def tilt_report(conn: sqlite3.Connection, username: str, time_class: str | None = None,
                session_gap_minutes: float = SESSION_GAP_MINUTES) -> dict:
    games = game_summaries(my_moves(conn, username, time_class))
    if not games:
        return {"games": 0, "note": "No analysed games."}
    return tilt_from_games(games, session_gap_minutes)


_PARTS = (("night 00-06", range(0, 6)), ("morning 06-12", range(6, 12)),
          ("afternoon 12-18", range(12, 18)), ("evening 18-24", range(18, 24)))


def time_of_day_from_games(games: list[dict]) -> dict:
    """Group by the local hour a game ended and by weekday/weekend."""
    parts: dict[str, list[dict]] = {name: [] for name, _ in _PARTS}
    days = {"weekday": [], "weekend": []}
    for g in games:
        when = datetime.fromtimestamp(g["end_time"])
        for name, hours in _PARTS:
            if when.hour in hours:
                parts[name].append(g)
        days["weekend" if when.weekday() >= 5 else "weekday"].append(g)

    out = {}
    for name, gs in parts.items():
        rest = [g for other, og in parts.items() if other != name for g in og]
        out[name] = {**_public(_block(gs)), "vs_other_times": _verdicts(_block(gs), _block(rest))}
    best = [n for n, v in out.items() if v["vs_other_times"].get("score_pct", {}).get("verdict") == "better"]
    worst = [n for n, v in out.items() if v["vs_other_times"].get("score_pct", {}).get("verdict") == "worse"]
    return {
        "games": len(games),
        "by_time_of_day": out,
        "weekday_vs_weekend": {k: _public(_block(v)) for k, v in days.items()},
        "clearly_better_times": best or "none that rise above sampling noise",
        "clearly_worse_times": worst or "none that rise above sampling noise",
        "note": "Times are the local clock of the machine running this server, taken from "
                "when each game ended.",
    }


def time_of_day_report(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> dict:
    games = game_summaries(my_moves(conn, username, time_class))
    return time_of_day_from_games(games) if games else {"games": 0, "note": "No analysed games."}


def resilience_from_games(games: list[dict]) -> dict:
    """How often a clearly worse position (win chance <= 20%) with the game still going
    was saved. The last few moves of each game are ignored (see coach.FINAL_STRETCH):
    otherwise every lost game counts as 'clearly worse' simply because it ended lost."""
    worse = [g for g in games if g["min_wp_early"] <= CLEARLY_WORSE]
    wins = sum(g["outcome"] == "W" for g in worse)
    draws = sum(g["outcome"] == "D" for g in worse)
    return {
        "games": len(games),
        "games_clearly_worse_with_play_left": len(worse),
        "share_of_all_games_pct": round(100 * len(worse) / len(games), 1) if games else None,
        "won": wins, "drawn": draws, "lost": len(worse) - wins - draws,
        "saved_pct": round(100 * (wins + draws) / len(worse), 1) if worse else None,
        "definition": f"clearly worse = win chance reached {CLEARLY_WORSE:.0f}% or lower before the "
                      f"last {FINAL_STRETCH} moves of the game",
    }


def resilience_report(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> dict:
    games = game_summaries(my_moves(conn, username, time_class))
    return resilience_from_games(games) if games else {"games": 0, "note": "No analysed games."}
