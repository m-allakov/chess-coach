"""Measured evidence about the player's own games, used to test coaching principles.

Each function answers one question with counts and shares; none of them judges. The commentary
layer cites these numbers and the principle tests compare them with what a principle predicts."""

from __future__ import annotations

import sqlite3
import statistics
from collections import Counter, defaultdict

import chess

from .moves import my_moves

WINNING = 75.0          # win chance (%) that counts as a winning position
FROM_GOOD = 60.0        # the decisive blunder is made from a position still at least this good
DECISIVE = 15.0         # win-chance points a move must cost to be called a blunder here
_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _reply_kind(fen: str, uci: str | None) -> str:
    """What the opponent's very next move did: mated, won a piece, won a pawn, gave check, or was quiet."""
    if not uci:
        return "quiet"
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    captured = board.piece_at(move.to_square)
    board.push(move)
    if board.is_checkmate():
        return "mated"
    if captured and _VALUE.get(captured.piece_type, 0) >= 3:
        return "won_piece"
    if captured:
        return "won_pawn"
    return "gave_check" if board.is_check() else "quiet"


def decisive_blunders(conn: sqlite3.Connection, username: str, time_class: str | None = None) -> dict:
    """For games that were not won but had a clearly winning position: the first costly move made
    from a still-good position, and what the opponent did next."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for m in my_moves(conn, username, time_class, who="both"):
        by_game[m["url"]].append(m)

    replies, phases, secs, wp_before, held = Counter(), Counter(), [], [], []
    found_best, relaxed, with_clock, n = 0, 0, 0, 0
    for ms in by_game.values():
        ms.sort(key=lambda m: m["ply"])
        mine = [m for m in ms if m["mine"]]
        if not mine or mine[0]["outcome"] == "W":
            continue
        start = next((i for i, m in enumerate(mine) if m["wp_before"] >= WINNING), None)
        if start is None:
            continue
        blunder = next((m for m in mine[start:] if m["wp_loss"] >= DECISIVE and m["wp_before"] >= FROM_GOOD), None)
        if blunder is None:
            continue
        n += 1
        nxt = next((m for m in ms if m["ply"] == blunder["ply"] + 1), None)
        replies[_reply_kind(nxt["fen"], nxt["uci"]) if nxt else "quiet"] += 1
        if nxt and nxt["uci"]:
            found_best += nxt["uci"] == nxt["best_uci"]
        phases[blunder["phase"]] += 1
        wp_before.append(blunder["wp_before"])
        held.append(sum(1 for m in mine[start:] if m["ply"] < blunder["ply"]))
        if blunder["time_zone"]:
            with_clock += 1
            relaxed += blunder["time_zone"] == "normal"
            secs.append(blunder["time_spent"])

    if not n:
        return {"games": 0}
    material_or_mate = replies["won_piece"] + replies["won_pawn"] + replies["mated"]
    return {
        "games": n,
        "opponent_next_move": dict(replies),
        "material_or_mate_share": round(material_or_mate / n, 3),
        "opponent_found_best_reply_share": round(found_best / n, 3),
        "phase": dict(phases),
        "relaxed_clock_share": round(relaxed / with_clock, 3) if with_clock else None,
        "median_seconds_on_blunder": round(statistics.median(secs), 1) if secs else None,
        "median_win_chance_before": round(statistics.median(wp_before)),
        "median_moves_held_before": statistics.median(held),
    }
