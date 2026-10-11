"""Check a book's claim against the player's own games.

A principle in the knowledge base carries a `testable_prediction`. For the predictions that the stored
data can answer, this module turns the sentence into a measurement and reports whether the games
agree. A book is an authority on chess in general; these tests ask whether the claim holds for *this*
player, at *this* level, in *this* time control. Each result says plainly when the data cannot tell.

Unit of observation: the game. Moves inside one game are not independent (one bad position produces
several bad moves), so every comparison is made on per-game rates, never on pooled moves.
"""

from __future__ import annotations

import sqlite3

from . import principles
from .moves import my_moves
from .stats import MIN_SAMPLE, compare_means

BLUNDER_LOSS = 15.0          # win-chance points; the same threshold the reports use
WINNING = 80.0               # win chance at which a position counts as clearly winning
EQUALISH = (40.0, 60.0)
MIN_MOVES_PER_ZONE = 5       # a game needs this many moves in a zone to contribute a rate there


def _verdict(effect: dict, predicted_sign: int) -> str:
    """supported / contradicted when the difference is clear, otherwise inconclusive."""
    if not effect["clear"]:
        return "inconclusive"
    return "supported" if (effect["change"] > 0) == (predicted_sign > 0) else "contradicted"


def _by_game(moves: list[dict]) -> dict[str, list[dict]]:
    games: dict[str, list[dict]] = {}
    for m in moves:
        games.setdefault(m["url"], []).append(m)
    return games


def _rate(moves: list[dict]) -> float:
    return 100.0 * sum(m["wp_loss"] >= BLUNDER_LOSS for m in moves) / len(moves)


def castle_early(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> dict:
    """am-024: games where the player had not castled by move 12 show more large losses in moves 8-20
    than games where they castled by move 10."""
    early, late = [], []
    for moves in _by_game(my_moves(conn, username, time_class)).values():
        if max(m["move_number"] for m in moves) < 20:
            continue                                   # too short to judge the middlegame
        castled = [m["move_number"] for m in moves if (m["san"] or "").startswith("O-O")]
        window = [m for m in moves if 8 <= m["move_number"] <= 20]
        if len(window) < MIN_MOVES_PER_ZONE * 2:
            continue
        if castled and min(castled) <= 10:
            early.append(_rate(window))
        elif not castled or min(castled) > 12:
            late.append(_rate(window))
    effect = compare_means(late, early)
    return {"principle_id": "am-024", "measure": "large losses per 100 moves, moves 8-20",
            "groups": {"castled_by_move_10": _summary(early), "not_castled_by_move_12": _summary(late)},
            "difference": effect, "predicted": "higher when not castled", "verdict": _verdict(effect, +1),
            "caveat": "Only games that lasted past move 20. Players who castle late may also differ in other ways."}


def winning_vigilance(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> dict:
    """am-010: a clearly winning position makes the player careless, so large losses per move are more
    frequent there than in equal positions. Compared within the same games, so the player's overall
    skill and the opponent's strength cancel out."""
    gaps = []
    for moves in _by_game(my_moves(conn, username, time_class)).values():
        winning = [m for m in moves if m["wp_before"] >= WINNING]
        equal = [m for m in moves if EQUALISH[0] <= m["wp_before"] <= EQUALISH[1]]
        if len(winning) >= MIN_MOVES_PER_ZONE and len(equal) >= MIN_MOVES_PER_ZONE:
            gaps.append(_rate(winning) - _rate(equal))
    effect = compare_means(gaps, [0.0] * len(gaps))
    return {"principle_id": "am-010", "measure": "large losses per 100 moves, winning minus equal positions (same game)",
            "groups": {"games_with_both": _summary(gaps)}, "difference": effect,
            "predicted": "higher when winning", "verdict": _verdict(effect, +1),
            "caveat": "Evaluation noise can exaggerate drops from very high positions, so read the size as a strong hint, not an exact figure."}


def _summary(values: list[float]) -> dict:
    return {"games": len(values), "mean": round(sum(values) / len(values), 1) if values else None}


TESTS = {"am-024": castle_early, "am-010": winning_vigilance}


def check(conn: sqlite3.Connection, username: str, principle_id: str, time_class: str | None = None) -> dict | None:
    """The data check for one principle, or None when there is no test for it. Adds a plain-language line."""
    test = TESTS.get(principle_id)
    if test is None:
        return None
    out = test(conn, username, time_class)
    out["summary"] = _sentence(out)
    return out


def _sentence(out: dict) -> str:
    d = out["difference"]
    enough = all(g["games"] >= MIN_SAMPLE for g in out["groups"].values())
    if not enough:
        return "Too few of your games to say whether this holds for you."
    if out["verdict"] == "inconclusive":
        return f"Your games neither confirm nor contradict this (difference {d['change']:+g}, noise ±{d['noise_margin']:g})."
    word = "bear this out" if out["verdict"] == "supported" else "point the other way"
    return f"Your own games {word} (difference {d['change']:+g}, beyond the noise of ±{d['noise_margin']:g})."


def report(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> list[dict]:
    """Every principle that can be checked, with its advice and source, for display next to the coaching."""
    books = principles.sources()
    by_id = {r["id"]: r for r in principles.load_all()}
    out = []
    for pid in TESTS:
        rec = by_id.get(pid)
        if rec is None:
            continue
        result = check(conn, username, pid, time_class)
        book = books[rec["source"]["book"]]
        out.append({**result, "advice": rec.get("advice"), "claim": rec["claim"],
                    "source": {"author": book["author"], "title": book["title"], "location": rec["source"]["location"]}})
    return out
