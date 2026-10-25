"""Reports built on the motif tags: what kinds of mistakes the player makes, and how won games
get lost."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from statistics import mean

from .classify import pct
from .motifs import MOTIF_VERSION, TAGS, UNTAGGED, in_scope, san_of
from .moves import my_moves
from .reports import HASTY_SECONDS, THOUGHT_SECONDS, WINNING

_RANK = {"inaccuracy": 1, "mistake": 2, "blunder": 3}
SINGLE_COLLAPSE = 30.0    # one move costing this much is a "single" collapse
_CHUNK = 500


def _motif_rows(conn: sqlite3.Connection, urls: set[str]) -> dict[tuple[str, int], tuple[list, dict]]:
    rows, listed = {}, sorted(urls)
    for i in range(0, len(listed), _CHUNK):
        part = listed[i:i + _CHUNK]
        for r in conn.execute(
                f"SELECT url, ply, tags, details FROM move_motif WHERE version = ? AND url IN ({','.join('?' * len(part))})",
                (MOTIF_VERSION, *part)):
            rows[(r["url"], r["ply"])] = (json.loads(r["tags"]), json.loads(r["details"]))
    return rows


def _tagged_games(conn: sqlite3.Connection, urls: set[str]) -> set[str]:
    done, listed = set(), sorted(urls)
    for i in range(0, len(listed), _CHUNK):
        part = listed[i:i + _CHUNK]
        done.update(r[0] for r in conn.execute(
            f"SELECT url FROM motif_game WHERE version = ? AND url IN ({','.join('?' * len(part))})",
            (MOTIF_VERSION, *part)))
    return done


def _pace(m: dict) -> str:
    """How the move was played against the clock, with the same lines time_pressure_report draws."""
    if m["time_zone"] is None:
        return "no_clock"
    if m["time_zone"] != "normal":
        return "short_of_time"
    if m["time_spent"] <= HASTY_SECONDS:
        return "hasty"
    return "considered" if m["time_spent"] >= THOUGHT_SECONDS else "normal_pace"


def _example(m: dict) -> dict:
    return {
        "url": m["url"], "move_no": m["move_number"], "color": m["color"], "fen": m["fen"],
        "played_san": m["san"], "best_san": san_of(m["fen"], m["best_uci"]), "wp_loss": pct(m["wp_loss"]),
        "time_spent": round(m["time_spent"], 1) if m["time_spent"] is not None else None,
        "clock_before": round(m["clock_before"], 1) if m["clock_before"] is not None else None,
    }


def motif_report(conn: sqlite3.Connection, username: str, last_n: int | None = None,
                 time_class: str | None = None, color: str | None = None, phase: str | None = None,
                 min_class: str = "mistake", n_examples: int = 3) -> dict:
    """What kinds of costly moves the player makes, from the motif tags."""
    if min_class not in ("mistake", "blunder"):
        raise ValueError("min_class must be 'mistake' or 'blunder'")
    moves = [m for m in my_moves(conn, username, time_class, last_n)
             if (not color or m["color"] == color) and (not phase or m["phase"] == phase)]
    urls = {m["url"] for m in moves}
    rows, done = _motif_rows(conn, urls), _tagged_games(conn, urls)

    pending = [m for m in moves if m["url"] not in done and in_scope(m["kind"], m["missed_mate"])]
    tagged = []
    for m in moves:
        found = rows.get((m["url"], m["ply"]))
        # Missed mates are tagged whatever their class, so they stay in at either setting.
        if found and (_RANK.get(m["kind"], 0) >= _RANK[min_class] or "missed_mate" in found[0]):
            tagged.append((m, found[0]))
    if not tagged:
        return {"games": len(urls), "tagged_moves": 0, "pending_moves": len(pending),
                "pending_games": len(urls - done),
                "note": "No tagged moves. Run tag_motifs (analyze_games tags new games by itself)."}

    total = len(tagged)
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for m, tags in tagged:
        for tag in tags:
            by_tag[tag].append(m)

    def grid(key) -> dict:
        cells: dict[str, list[tuple[dict, list]]] = defaultdict(list)
        for pair in tagged:
            cells[key(pair[0])].append(pair)
        return {name: {"moves": len(items), "tags": dict(Counter(t for _, tags in items for t in tags))}
                for name, items in sorted(cells.items())}

    examples = {}
    for tag, ms in by_tag.items():
        newest = sorted(ms, key=lambda m: (m["end_time"], m["ply"]), reverse=True)[:n_examples]
        costliest = sorted(ms, key=lambda m: m["wp_loss"], reverse=True)[:n_examples]
        examples[tag] = {"newest": [_example(m) for m in newest], "costliest": [_example(m) for m in costliest]}

    return {
        "games": len(urls),
        "tagged_moves": total,
        "pending_moves": len(pending),
        "pending_games": len(urls - done),
        "min_class": min_class,
        "tags": {tag: {
            "count": len(by_tag.get(tag, [])),
            "share_pct": pct(100 * len(by_tag.get(tag, [])) / total),
            "avg_wp_loss": pct(mean(m["wp_loss"] for m in by_tag[tag])) if by_tag.get(tag) else None,
        } for tag in (*TAGS, UNTAGGED)},
        "untagged_share_pct": pct(100 * len(by_tag.get(UNTAGGED, [])) / total),
        "by_phase": grid(lambda m: m["phase"]),
        "by_pace": grid(_pace),
        "by_color": grid(lambda m: m["color"]),
        "examples": examples,
        "definitions": {
            "share_pct": "share of tagged moves carrying the tag; a move can carry several tags, so the "
                         "shares can add up to more than 100",
            "untagged": "no rule matched: positional or strategic mistakes, or tactics too deep for the "
                        "stored best line",
            "pace": f"hasty = played in <= {HASTY_SECONDS:.0f}s with time to spare, considered = >= "
                    f"{THOUGHT_SECONDS:.0f}s with time to spare, short_of_time = under 25% of the clock "
                    "(time_pressure_report's lines)",
            "scope": "mistakes, blunders and mates of 5 moves or fewer that were let go; inaccuracies are not tagged",
        },
    }


def conversion_report(conn: sqlite3.Connection, username: str, last_n: int | None = None,
                      time_class: str | None = None, color: str | None = None,
                      threshold: float = WINNING, n: int = 10) -> dict:
    """Games where the player reached a winning position (win chance >= threshold, counted exactly
    as weakness_report counts it) and did not win, and how each one slipped away."""
    games: dict[str, list[dict]] = defaultdict(list)
    for m in my_moves(conn, username, time_class, last_n, who="both"):
        games[m["url"]].append(m)
    games = {url: sorted(ms, key=lambda m: m["ply"]) for url, ms in games.items()
             if not color or ms[0]["color"] == color}
    rows = _motif_rows(conn, set(games))
    done = _tagged_games(conn, set(games))

    reached = lost = 0
    found = []
    for url, ms in games.items():
        mine = [m for m in ms if m["mine"]]
        if not mine or max(m["wp_before"] for m in mine) < threshold:
            continue
        reached += 1
        if mine[0]["outcome"] == "W":
            continue
        lost += 1
        found.append(_lost_game(url, ms, mine, threshold, rows, done))

    found.sort(key=lambda g: g["_end"], reverse=True)

    def tally(key) -> dict:
        return dict(Counter(key(g) for g in found).most_common())

    motifs_seen = Counter(t for g in found for t in (g["collapse_motifs"] or []))
    return {
        "summary": {
            "threshold": threshold,
            "games_reaching_winning_position": reached,
            "of_which_not_won": lost,
            "not_won_pct": pct(100 * lost / reached) if reached else None,
        },
        "by_result": tally(lambda g: g["result"]),
        "by_termination": tally(lambda g: g["termination"]),
        "by_collapse_type": tally(lambda g: g["collapse_type"]),
        "by_collapse_phase": tally(lambda g: g["_phase"]),
        "by_collapse_time_zone": tally(lambda g: g["_zone"] or "no_clock"),
        "collapse_motifs": {
            "tags": dict(motifs_seen.most_common()),
            "tagged_collapses": sum(g["collapse_motifs"] is not None for g in found),
            "pending_collapses": sum(g["collapse_motifs"] is None for g in found),
        },
        "avg_moves_from_threshold_to_collapse": round(mean(g["_held"] for g in found), 1) if found else None,
        "games": [{k: v for k, v in g.items() if not k.startswith("_")} for g in found[:n]],
        "definitions": {
            "winning position": f"win chance >= {threshold:.0f}% before one of the player's moves (the same test as weakness_report)",
            "collapse_move": "the player's costliest move from the first time the threshold was reached",
            "ply": "numbered from 1 (White's first move); a move's ply is the move itself",
            "fell_below_50_ply": "the ply after which the win chance never again reached 50%",
            "collapse_type": f"single = the collapse move alone cost >= {SINGLE_COLLAPSE:.0f} points; time = lost or "
                             "drawn on the clock; gradual = the collapse move is under half of the fall from the peak "
                             "to the end; other. Checked in that order.",
            "collapse_motifs": "null when the collapse move is a mistake not tagged yet; [] when it was not costly "
                               "enough to tag",
        },
    }


def _lost_game(url: str, ms: list[dict], mine: list[dict], threshold: float, rows: dict, done: set) -> dict:
    ours = lambda m: m["wp_before"] if m["mine"] else 100 - m["wp_before"]   # the player's win chance
    series = [ours(m) for m in ms] + [ms[-1]["wp_after"] if ms[-1]["mine"] else 100 - ms[-1]["wp_after"]]
    first = next(m for m in mine if m["wp_before"] >= threshold)
    peak = max(mine, key=lambda m: m["wp_before"])
    collapse = max((m for m in mine if m["ply"] >= first["ply"]), key=lambda m: m["wp_loss"])

    tail = len(series)
    while tail > 0 and series[tail - 1] < 50:
        tail -= 1
    fell = tail if tail < len(series) else None

    code = mine[0]["result_code"]
    drop = peak["wp_before"] - series[-1]
    if collapse["wp_loss"] >= SINGLE_COLLAPSE:
        kind = "single"
    elif code in ("timeout", "timevsinsufficient"):
        kind = "time"
    elif drop > 0 and collapse["wp_loss"] < 0.5 * drop:
        kind = "gradual"
    else:
        kind = "other"

    found = rows.get((url, collapse["ply"]))
    if found:
        motifs = found[0]
    else:
        motifs = None if url not in done and in_scope(collapse["kind"], collapse["missed_mate"]) else []
    return {
        "url": url, "result": "loss" if mine[0]["outcome"] == "L" else "draw", "termination": code,
        "peak_ply": peak["ply"] + 1, "peak_win_chance": pct(peak["wp_before"]),
        "collapse_ply": collapse["ply"] + 1, "collapse_san": collapse["san"],
        "collapse_best_san": san_of(collapse["fen"], collapse["best_uci"]),
        "collapse_wp_loss": pct(collapse["wp_loss"]), "collapse_motifs": motifs,
        "collapse_time_spent": round(collapse["time_spent"], 1) if collapse["time_spent"] is not None else None,
        "clock_at_peak": round(peak["clock_before"], 1) if peak["clock_before"] is not None else None,
        "clock_at_collapse": round(collapse["clock_before"], 1) if collapse["clock_before"] is not None else None,
        "fell_below_50_ply": fell, "collapse_type": kind,
        "_end": ms[0]["end_time"], "_phase": collapse["phase"], "_zone": collapse["time_zone"],
        "_held": sum(1 for m in mine if first["ply"] <= m["ply"] < collapse["ply"]),
    }
