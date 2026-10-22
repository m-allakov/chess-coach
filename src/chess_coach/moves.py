"""Turn stored positions into the player's own classified moves."""

from __future__ import annotations

import re
import sqlite3

from .classify import classify, mover_win_pct
from .mates import is_known, mate_fields

_DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
_ECO_URL = re.compile(r'\[ECOUrl "[^"]*/openings/([^"]+)"\]')
_FAMILY_END = {"Defense", "Opening", "Gambit", "Game", "Attack", "System", "Defence"}


def outcome(result: str) -> str:
    if result == "win":
        return "W"
    return "D" if result in _DRAWS else "L"


_CLK = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")

# Share of the starting time still on the clock below which a player is "short of time".
CRITICAL_FRACTION = 0.10
LOW_FRACTION = 0.25


def parse_clocks(pgn: str) -> list[float]:
    """Seconds left after each ply (the mover's own clock, increment included)."""
    return [int(h) * 3600 + int(m) * 60 + float(s) for h, m, s in _CLK.findall(pgn)]


def parse_time_control(tc: str) -> tuple[float, float] | None:
    """'180' -> (180, 0); '60+1' -> (60, 1); daily formats ('1/86400') -> None."""
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", tc)
    return (float(m.group(1)), float(m.group(2) or 0)) if m else None


def time_zone(clock_before: float, base: float) -> str:
    share = clock_before / base
    if share < CRITICAL_FRACTION:
        return "critical"
    return "low" if share < LOW_FRACTION else "normal"


def opening_names(pgn: str) -> tuple[str, str]:
    """(family, full name) from the chess.com ECOUrl header, e.g. Sicilian Defense."""
    m = _ECO_URL.search(pgn)
    if not m:
        return "Unknown", "Unknown"
    words = []
    for w in m.group(1).split("-"):
        if re.match(r"^\d", w):   # trailing move list such as "3.Bc4"
            break
        words.append(w)
    full = " ".join(words) or "Unknown"
    family = []
    for w in words:
        family.append(w)
        if w in _FAMILY_END:
            break
    else:
        family = words[:3]
    return " ".join(family) or "Unknown", full


def my_moves(conn: sqlite3.Connection, username: str, time_class: str | None = None,
             last_n: int | None = None, who: str = "mine") -> list[dict]:
    """Every analysed move of the player ('mine'), the opponents' ('opponent') or
    both, newest game first. Win chances are from the mover's point of view."""
    where = ["g.username = ?", "g.url IN (SELECT url FROM game_analysis)"]
    if who == "mine":
        where.append("p.turn = g.color")
    elif who == "opponent":
        where.append("p.turn != g.color")
    args: list = [username.lower()]
    if time_class:
        where.append("g.time_class = ?")
        args.append(time_class)
    if last_n:
        where.append("""g.url IN (SELECT g2.url FROM games g2
                         JOIN game_analysis a ON a.url = g2.url
                         WHERE g2.username = ? {tc} ORDER BY g2.end_time DESC LIMIT ?)""".format(
            tc="AND g2.time_class = ?" if time_class else ""))
        args.append(username.lower())
        if time_class:
            args.append(time_class)
        args.append(last_n)
    rows = conn.execute(
        f"""SELECT g.url, g.color, g.result, g.time_class, g.time_control, g.my_rating, g.end_time, g.pgn,
                   p.ply, p.fen, p.turn, p.phase, p.move_san, p.move_uci, p.best_uci,
                   p.cp AS cp0, q.cp AS cp1, p.depth AS d0, q.depth AS d1,
                   dp.ply IS NOT NULL AS has0, dp.mate AS mate0,
                   dq.ply IS NOT NULL AS has1, dq.mate AS mate1, gd.url IS NOT NULL AS has_detail
            FROM positions p
            JOIN positions q ON q.url = p.url AND q.ply = p.ply + 1
            JOIN games g ON g.url = p.url
            LEFT JOIN position_detail dp ON dp.url = p.url AND dp.ply = p.ply
            LEFT JOIN position_detail dq ON dq.url = p.url AND dq.ply = p.ply + 1
            LEFT JOIN game_detail gd ON gd.url = p.url
            WHERE {' AND '.join(where)}
            ORDER BY g.end_time DESC, p.ply""",
        args,
    ).fetchall()

    openings: dict[str, tuple[str, str]] = {}
    plies: dict[str, int] = {}
    for r in rows:
        plies[r["url"]] = max(plies.get(r["url"], 0), r["ply"] + 1)
    clocks: dict[str, list[float] | None] = {}
    moves = []
    for r in rows:
        if r["url"] not in openings:
            openings[r["url"]] = opening_names(r["pgn"])
            c = parse_clocks(r["pgn"])
            # Only trust clocks that line up one-to-one with the analysed plies.
            clocks[r["url"]] = c if len(c) >= plies[r["url"]] else None
        clock = clock_fields(r, clocks[r["url"]])
        wp0 = mover_win_pct(r["cp0"], r["turn"])   # from the side that made the move
        wp1 = mover_win_pct(r["cp1"], r["turn"])
        loss = max(0.0, wp0 - wp1)
        family, full = openings[r["url"]]
        moves.append({
            "url": r["url"], "color": r["color"], "mine": r["turn"] == r["color"],
            "time_class": r["time_class"], "time_control": r["time_control"], "my_rating": r["my_rating"],
            "end_time": r["end_time"],
            "outcome": outcome(r["result"]), "ply": r["ply"], "move_number": r["ply"] // 2 + 1,
            "phase": r["phase"], "fen": r["fen"], "san": r["move_san"], "uci": r["move_uci"],
            "best_uci": r["best_uci"],
            "cp_before": r["cp0"], "cp_after": r["cp1"],
            "wp_before": wp0, "wp_after": wp1, "wp_loss": loss, "kind": classify(loss),
            "depth": min(r["d0"], r["d1"]),
            "opening_family": family, "opening": full,
            "result_code": r["result"], **clock,
            **mate_fields(r["turn"], r["mate0"], r["mate1"],
                          is_known(r["has_detail"], r["has0"], r["cp0"]),
                          is_known(r["has_detail"], r["has1"], r["cp1"])),
        })
    return moves


def clock_fields(row, clocks: list[float] | None) -> dict:
    """Time left before the move, time spent on it, and the resulting pressure zone."""
    none = {"clock_before": None, "clock_after": None, "time_spent": None, "base_time": None,
            "time_zone": None}
    tc = parse_time_control(row["time_control"])
    if not clocks or not tc:
        return none
    base, inc = tc
    ply = row["ply"]
    before = clocks[ply - 2] if ply >= 2 else base
    spent = max(0.0, before + inc - clocks[ply])
    return {"clock_before": before, "clock_after": clocks[ply], "time_spent": spent, "base_time": base,
            "time_zone": time_zone(before, base)}
