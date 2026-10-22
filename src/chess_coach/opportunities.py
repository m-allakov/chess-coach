"""Missed opportunities (opponent blundered, did the player punish it?) and
sacrifice candidates (sound moves that put a piece on offer)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import chess

from .classify import pct
from .mates import MISSED_MATE_MAX
from .moves import my_moves

_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}

# Reply after an opponent blunder: lost fewer points than this = punished it,
# at least MISSED_LOSS = threw the chance away, in between = partly used.
PUNISHED_LOSS = 5.0
MISSED_LOSS = 10.0

# Sacrifice-candidate rule (documented in sacrifice_candidates).
SACRIFICE_MAX_LOSS = 5.0      # win-% points the move may cost against the engine's best
SACRIFICE_MIN_AFTER = 50.0    # mover is not worse afterwards
SACRIFICE_MIN_OFFERED = 2     # pawns of material put on offer (2 = a piece for a pawn)


def missed_opportunities(conn: sqlite3.Connection, username: str, n: int = 5,
                         time_class: str | None = None, last_n: int | None = None) -> dict:
    """After each opponent blunder, how often did the player's very next move use it?"""
    everything = my_moves(conn, username, time_class, last_n, who="both")
    by_game: dict[str, dict[int, dict]] = defaultdict(dict)
    for m in everything:
        by_game[m["url"]][m["ply"]] = m

    chances = []
    for plies in by_game.values():
        for ply, opp in plies.items():
            reply = plies.get(ply + 1)
            if opp["mine"] or opp["kind"] != "blunder" or not reply or not reply["mine"]:
                continue
            chances.append((opp, reply))

    def verdict(reply: dict) -> str:
        if reply["wp_loss"] < PUNISHED_LOSS:
            return "punished"
        return "missed" if reply["wp_loss"] >= MISSED_LOSS else "partly"

    tally = {"punished": 0, "partly": 0, "missed": 0}
    by_phase: dict[str, dict[str, int]] = defaultdict(lambda: {"punished": 0, "partly": 0, "missed": 0})
    spent: dict[str, list[float]] = defaultdict(list)
    missed = []
    for opp, reply in chances:
        v = verdict(reply)
        tally[v] += 1
        by_phase[reply["phase"]][v] += 1
        if reply["time_spent"] is not None:
            spent[v].append(reply["time_spent"])
        if v == "missed":
            missed.append(reply)

    total = len(chances)
    missed.sort(key=lambda m: m["wp_loss"], reverse=True)
    examples = []
    for m in missed[:n]:
        board = chess.Board(m["fen"])
        best = None
        if m["best_uci"]:
            try:
                best = board.san(chess.Move.from_uci(m["best_uci"]))
            except ValueError:
                pass
        examples.append({
            "game": m["url"], "move_number": m["move_number"], "phase": m["phase"], "fen": m["fen"],
            "played": m["san"], "best": best,
            "played_uci": m["uci"], "best_uci": m["best_uci"],
            "win_chance_before_your_move": pct(m["wp_before"]),
            "win_chance_after_your_move": pct(m["wp_after"]),
            "seconds_spent": round(m["time_spent"], 1) if m["time_spent"] is not None else None,
        })
    return {
        "opponent_blunders_you_could_use": total,
        **tally,
        "punish_rate": round(tally["punished"] / total, 2) if total else None,
        "by_phase": {p: dict(v) for p, v in sorted(by_phase.items())},
        "avg_seconds_on_reply": {k: round(sum(v) / len(v), 1) for k, v in spent.items() if v},
        "worst_missed": examples,
        "missed_mates": missed_mates(everything, n),
        "definitions": {
            "opponent blunder": "a move costing the opponent 15+ win-chance points",
            "punished": f"your next move lost under {PUNISHED_LOSS:.0f} points",
            "missed": f"your next move gave back {MISSED_LOSS:.0f}+ points",
        },
    }


def missed_mates(moves: list[dict], n: int) -> dict:
    """Moves where the player had a forced mate and played something that let it go.
    `moves` may hold both sides' moves; only the player's are looked at."""
    found = [m for m in moves if m["mine"] and m["missed_mate"]]
    found.sort(key=lambda m: (m["missed_mate"], -m["end_time"], -m["ply"]))   # shortest first, then newest
    by_phase = {p: {"total": 0, "short": 0} for p in ("opening", "middlegame", "endgame")}
    for m in found:
        by_phase[m["phase"]]["total"] += 1
        by_phase[m["phase"]]["short"] += m["missed_mate"] <= MISSED_MATE_MAX
    listed = []
    for m in found[:n]:
        board = chess.Board(m["fen"])
        best = None
        if m["best_uci"]:
            try:
                best = board.san(chess.Move.from_uci(m["best_uci"]))
            except ValueError:
                pass
        listed.append({
            "game": m["url"], "move_no": m["move_number"], "fen": m["fen"],
            "played_san": m["san"], "best_san": best, "mate_n": m["missed_mate"],
            "time_spent": round(m["time_spent"], 1) if m["time_spent"] is not None else None,
            "clock_after": round(m["clock_after"], 1) if m["clock_after"] is not None else None,
        })
    return {
        "total": len(found),
        "short": sum(m["missed_mate"] <= MISSED_MATE_MAX for m in found),
        "by_phase": by_phase,
        "missed": listed,
        "definitions": {
            "missed mate": "you had a forced mate before your move and none after it",
            "short": f"mate in {MISSED_MATE_MAX} or fewer",
            "order": "shortest mate first, then newest game",
            "coverage": "games analysed before mate data was stored count only where it was backfilled",
        },
    }


def _exchange_loss(board: chess.Board, square: chess.Square) -> int:
    """Material (in pawns) the side to move nets by capturing on `square`, assuming
    both sides keep recapturing with their cheapest legal piece."""
    victim = board.piece_at(square)
    if victim is None:
        return 0
    captures = [m for m in board.legal_moves if m.to_square == square]
    if not captures:
        return 0
    cheapest = min(captures, key=lambda m: _VALUE[board.piece_type_at(m.from_square)])
    board.push(cheapest)
    try:
        return max(0, _VALUE[victim.piece_type] - _exchange_loss(board, square))
    finally:
        board.pop()


def sacrificed_material(fen: str, uci: str) -> int:
    """Most material (pawns) the opponent can win by capturing one of the mover's
    pieces after the move, net of anything the move itself captured. Pawns and kings
    are not counted as offered pieces. A static exchange count, not an engine search."""
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    mover = board.turn
    captured = board.piece_at(move.to_square)
    captured_value = _VALUE[captured.piece_type] if captured else 0
    board.push(move)
    best = 0
    for square, piece in board.piece_map().items():
        if piece.color != mover or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        loss = _exchange_loss(board, square)
        if square == move.to_square:
            loss -= captured_value
        best = max(best, loss)
    return best


def sacrifice_candidates(conn: sqlite3.Connection, username: str, n: int = 10,
                         time_class: str | None = None, last_n: int | None = None) -> dict:
    """Sound moves that put a piece on offer: the tactical shots among a player's moves.

    This approximates chess.com's "brilliant" label but does not reproduce it, because
    chess.com's rule is not public. Measured against the 53 brilliants chess.com lists
    for the author (see scripts/calibrate_brilliant.py and tune_brilliant.py), this rule
    finds about two thirds of them but also flags roughly ten times as many other moves.
    Treat the list as "moves worth a second look", never as a count of brilliants.

    A move qualifies when it costs at most 5 win-chance points against the engine's
    best, leaves the mover at 50%+ afterwards, and offers at least 2 pawns of material
    on a piece other than a pawn or king (static exchange).
    """
    found = []
    for m in my_moves(conn, username, time_class, last_n):
        if (not m["uci"] or m["wp_loss"] > SACRIFICE_MAX_LOSS or m["wp_after"] < SACRIFICE_MIN_AFTER
                or m["depth"] < 12):
            continue
        offered = sacrificed_material(m["fen"], m["uci"])
        if offered >= SACRIFICE_MIN_OFFERED:
            found.append((m, offered))
    found.sort(key=lambda t: (t[1], t[0]["wp_after"] - t[0]["wp_before"]), reverse=True)
    return {
        "candidates": len(found),
        "shown": [
            {"game": m["url"], "move_number": m["move_number"], "phase": m["phase"], "fen": m["fen"],
             "played": m["san"], "material_offered_pawns": offered,
             "win_chance_before": pct(m["wp_before"]), "win_chance_after": pct(m["wp_after"])}
            for m, offered in found[:n]
        ],
        "note": "Approximates chess.com's brilliant label; expect many ordinary tactical moves.",
    }
