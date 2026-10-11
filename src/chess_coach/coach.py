"""Coach summary: why games were not won, what to work on this week, and whether the
numbers are moving. Everything is measured against the player's own history, so it
does not assume any rating level."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import commentary, evidence, principle_tests, reports
from .moves import my_moves
from .stats import compare_means, compare_rates

WINNING = 75.0           # win chance (%) that counts as "had a winning position"
BAD_OPENING = 35.0       # win chance (%) after the opening below which the opening is the trouble
DECISIVE_BLUNDER = 20.0  # a single move losing this many points is the story of the game
TIME_COSTLY = 35.0       # a timeout only counts as "time" if the position was not already lost

# Evaluated in this order: the first that applies is the game's primary cause.
FINAL_STRETCH = 5        # the last moves of a game, ignored when asking "was it recoverable?"

CAUSES = ("time", "thrown_win", "opening", "blunder", "gradual")

CAUSE_TEXT = {
    "time": "lost on the clock while the position was not clearly lost",
    "thrown_win": "had a winning position and did not win",
    "opening": "was already clearly worse after the opening",
    "blunder": "one costly mistake decided it",
    "gradual": "slow decline with no single decisive mistake",
}

# General training guidance per cause. This is not derived from the player's data.
ADVICE = {
    "time": ["Decide a clock plan: where you may spend time and where you must move fast.",
             "When short of time, prefer simple safe moves over complicated ones.",
             "Look at the time pressure report to see how time is spent in each phase."],
    "thrown_win": ["When clearly ahead, trade pieces and remove counterplay instead of attacking.",
                   "Before every move ask what the opponent threatens; winning games are lost to one oversight.",
                   "Go through the worst moments of these games to see exactly where the win slipped."],
    "opening": ["Review the openings where you leave theory worse (the opening report lists them).",
                "Prefer lines you understand over lines you only memorised."],
    "blunder": ["Run a blunder check before each move: what does the move leave undefended, "
                "and what are the opponent's checks and captures?",
                "Tactics puzzles in short daily sessions build pattern recognition."],
    "gradual": ["Look at the middlegame plans and endgames in these games rather than single moves.",
                "Study typical plans for your openings."],
}


def classify_non_win(g: dict) -> str:
    """Primary cause of a game that was not a win. `g` is a game summary."""
    if g["result_code"] == "timeout" and g["final_wp"] >= TIME_COSTLY:
        return "time"
    if g["peak_wp"] >= WINNING:
        return "thrown_win"
    if g["opening_exit_wp"] is not None and g["opening_exit_wp"] < BAD_OPENING:
        return "opening"
    if g["biggest_loss"] >= DECISIVE_BLUNDER:
        return "blunder"
    return "gradual"


def game_summaries(moves: list[dict]) -> list[dict]:
    """One summary per game, newest first (my_moves already orders games that way)."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for m in moves:
        by_game[m["url"]].append(m)
    out = []
    for url, ms in by_game.items():
        opening = [m for m in ms if m["phase"] == "opening"]
        worst = max(ms, key=lambda m: m["wp_loss"])
        out.append({
            "url": url, "end_time": ms[0]["end_time"], "outcome": ms[0]["outcome"],
            "result_code": ms[0]["result_code"],
            "opening_family": ms[0]["opening_family"], "time_class": ms[0]["time_class"],
            "moves": len(ms),
            "peak_wp": max(max(m["wp_before"] for m in ms), ms[-1]["wp_after"]),
            "opening_exit_wp": opening[-1]["wp_after"] if opening else None,
            "biggest_loss": worst["wp_loss"],
            "final_wp": ms[-1]["wp_after"],
            "min_wp": min(min(m["wp_before"], m["wp_after"]) for m in ms),
            # Worst position before the final stretch of the game. A lost game almost always
            # ends in a lost position, which says nothing about whether it could be saved.
            "min_wp_early": min((min(m["wp_before"], m["wp_after"]) for m in ms[:-FINAL_STRETCH]),
                                default=100.0),
            "blunders": sum(m["kind"] == "blunder" for m in ms),
        })
    return out


def _window_metrics(games: list[dict]) -> dict:
    n = len(games)
    if not n:
        return {"games": 0}
    moves = sum(g["moves"] for g in games)
    reached = [g for g in games if g["peak_wp"] >= WINNING]
    wins = sum(g["outcome"] == "W" for g in games)
    draws = sum(g["outcome"] == "D" for g in games)
    return {
        "games": n,
        "score_pct": round(100 * (wins + 0.5 * draws) / n, 1),
        "blunders_per_100_moves": round(100 * sum(g["blunders"] for g in games) / moves, 1),
        "lost_on_time_pct_of_games": round(100 * sum(g["result_code"] == "timeout" for g in games) / n, 1),
        "winning_positions_not_won_pct": round(
            100 * sum(g["outcome"] != "W" for g in reached) / len(reached), 1) if reached else None,
        # How many observations each percentage rests on; used to judge whether a change is real.
        "sample_sizes": {"score_pct": n, "blunders_per_100_moves": moves,
                         "lost_on_time_pct_of_games": n, "winning_positions_not_won_pct": len(reached)},
    }


# True when a higher value is the better direction.
_HIGHER_IS_BETTER = {"score_pct": True, "blunders_per_100_moves": False,
                     "lost_on_time_pct_of_games": False, "winning_positions_not_won_pct": False}


def _trend(recent_games: list[dict], before_games: list[dict]) -> dict:
    """Compare two windows of games. A change only counts as better/worse when it exceeds
    what sampling noise alone would produce (about 95% confidence), so a 100-game window
    does not trigger verdicts from a lucky or unlucky streak."""
    recent, before = _window_metrics(recent_games), _window_metrics(before_games)
    out = {}
    for key, higher_better in _HIGHER_IS_BETTER.items():
        a, b = recent.get(key), before.get(key)
        if a is None or b is None:
            continue
        if key == "blunders_per_100_moves":      # built from moves: compare per-game rates
            cmp = compare_means([100 * g["blunders"] / g["moves"] for g in recent_games],
                                [100 * g["blunders"] / g["moves"] for g in before_games])
        else:
            cmp = compare_rates(a, recent["sample_sizes"][key], b, before["sample_sizes"][key])
        change = round(a - b, 1)
        verdict = "no clear change" if not cmp["clear"] else (
            "better" if (change > 0) == higher_better else "worse")
        out[key] = {"recent": a, "before": b, "change": change,
                    "noise_margin": cmp["noise_margin"], "verdict": verdict}
    return out


def peer_metrics(moves: list[dict]) -> dict | None:
    """The numbers compared with other players of a similar rating (see peers.py).

    None when there are too few games for the figures to mean anything."""
    games = game_summaries(moves)
    if len(games) < 10:
        return None
    window = _window_metrics(games)
    overall = reports._stats(moves)
    phases = reports._group(moves, "phase")
    return {
        "games": window["games"],
        "score_pct": window["score_pct"],
        "blunders_per_100_moves": window["blunders_per_100_moves"],
        "avg_wp_loss": overall["avg_wp_loss"],
        "lost_on_time_pct_of_games": window["lost_on_time_pct_of_games"],
        "winning_positions_not_won_pct": window["winning_positions_not_won_pct"],
        "opening_blunders_per_100": phases.get("opening", {}).get("blunders_per_100_moves"),
        "middlegame_blunders_per_100": phases.get("middlegame", {}).get("blunders_per_100_moves"),
        "endgame_blunders_per_100": phases.get("endgame", {}).get("blunders_per_100_moves"),
    }


def coach_plan(conn: sqlite3.Connection, username: str, time_class: str | None = None,
               recent_games: int = 100) -> dict:
    """Why games were not won, one focus for the coming week, and the trend."""
    games = game_summaries(my_moves(conn, username, time_class))
    if not games:
        return {"games": 0, "note": "No analysed games. Run sync_games and analyze_games first."}

    # The focus comes from recent games; fall back to everything when there are few.
    recent = games[:recent_games] if len(games) >= 2 * 30 else games
    earlier = games[len(recent):]

    non_wins = [g for g in recent if g["outcome"] != "W"]
    by_cause: dict[str, list[dict]] = {c: [] for c in CAUSES}
    for g in non_wins:
        by_cause[classify_non_win(g)].append(g)

    causes = {
        c: {"games": len(gs),
            "share_of_non_wins": round(len(gs) / len(non_wins), 2) if non_wins else 0,
            "meaning": CAUSE_TEXT[c],
            "examples": [g["url"] for g in gs[:3]]}
        for c, gs in by_cause.items()
    }
    timeouts = [g for g in non_wins if g["result_code"] == "timeout"]
    causes["time"]["all_timeouts_in_period"] = len(timeouts)
    top = max(CAUSES, key=lambda c: len(by_cause[c]))
    metrics_recent = _window_metrics(recent)

    result = {
        "games_analysed": len(games),
        "recent_window": {"games": len(recent), **metrics_recent},
        "games_not_won": len(non_wins),
        "why_games_were_not_won": causes,
        "weekly_focus": {
            "area": top,
            "because": f"{len(by_cause[top])} of {len(non_wins)} recent games that were not won "
                       f"({CAUSE_TEXT[top]}).",
            "general_advice": ADVICE[top],
        },
        "trend_vs_earlier_games": _trend(recent, earlier) if earlier else {},
        "method": {
            "attribution_order": list(CAUSES),
            "rules": {
                "time": f"lost on the clock with win chance still {TIME_COSTLY:.0f}%+ "
                        "(a timeout in an already lost position is attributed to the other rules)",
                "thrown_win": f"win chance reached {WINNING:.0f}%+ at some point but the game was not won",
                "opening": f"win chance after the opening was under {BAD_OPENING:.0f}%",
                "blunder": f"a single move cost {DECISIVE_BLUNDER:.0f}+ win-chance points",
                "gradual": "none of the above",
            },
            "note": "Each non-win gets one cause, the first rule that applies. General advice is "
                    "standard training guidance, not derived from the player's data.",
        },
    }
    # Habits from the coaching literature for this cause, a commentary that cites measured evidence,
    # and the evidence itself. Causes the literature does not cover yet keep the generic advice.
    profile = evidence.decisive_blunders(conn, username, time_class)
    result["weekly_focus"]["principle_advice"] = commentary.advice_for(top)
    result["commentary"] = commentary.generate(result, profile)
    result["principle_checks"] = principle_tests.report(conn, username, time_class)
    result["evidence"] = {"decisive_blunders": profile}
    return result
