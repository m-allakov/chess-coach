"""Forced mates around a move: the mate the mover had, and whether the move kept it.

Mate scores are stored from White's side as moves-to-mate counted in the mating side's own
moves: +3 = White mates in 3, -3 = Black mates in 3, 0 = the position is checkmate. After a
move played in the best line, the mate is one move shorter (N becomes N-1)."""

from __future__ import annotations

from .analysis import DECIDED_CP

MISSED_MATE_MAX = 5   # a mate this short or shorter counts as a clear miss (flagged, reported first)


def is_known(game_has_detail: bool, has_row: bool, cp: int) -> bool:
    """Whether a position's mate status was recorded.

    A game with no detail at all knows nothing. A backfilled game holds rows only for costly
    moves and for every position at 900 cp or more, so a missing row below that really means
    "no mate"; a fully analysed game has a row for every position."""
    return game_has_detail and (has_row or abs(cp) < DECIDED_CP)


def for_mover(mate: int | None, color: str) -> int | None:
    """Moves `color` needs to mate, or None when `color` is not the side mating."""
    if not mate:
        return None
    n = mate if color == "white" else -mate
    return n if n > 0 else None


def mate_fields(color: str, mate_before: int | None, mate_after: int | None,
                known_before: bool, known_after: bool) -> dict:
    """What the move did to the mover's own mate, if it had one.

    mate_before_mover: N when the mover had a forced mate in N before moving.
    missed_mate: N when that mate no longer exists after the move.
    mate_lengthened: {"from": N, "to": M} when the mate survives but takes more than N-1
    moves. All three are None when the mover had no mate or the data cannot say."""
    out = {"mate_before_mover": None, "missed_mate": None, "mate_lengthened": None}
    n = for_mover(mate_before, color) if known_before else None
    if n is None:
        return out
    out["mate_before_mover"] = n
    if not known_after or mate_after == 0:   # unknown, or the move delivered checkmate
        return out
    kept = for_mover(mate_after, color)
    if kept is None:
        out["missed_mate"] = n
    elif kept > n - 1:
        out["mate_lengthened"] = {"from": n, "to": kept}
    return out
