"""Single-game views for the MCP client: a filtered game list, and one game move by move.

Everything here reads what is already stored; no engine is started."""

from __future__ import annotations

import io
import json
import re
import sqlite3
from datetime import datetime, timezone

import chess
import chess.pgn

from .analysis import PV_PLIES
from .classify import classify, mover_win_pct, pct
from .mates import MISSED_MATE_MAX, is_known, keeps_mate, mate_fields
from .motifs import MOTIF_VERSION, in_scope
from .moves import clock_fields, opening_names, outcome, parse_clocks

_HEADER = re.compile(r'\[(\w+) "([^"]*)"\]')
_ID = re.compile(r"(\d+)/?(?:[?#].*)?$")
_FLAGGED = {"inaccuracy", "mistake", "blunder"}
_RESULTS = {"win": "W", "loss": "L", "draw": "D"}
MAX_LIST = 200


def headers(pgn: str) -> dict[str, str]:
    return dict(_HEADER.findall(re.split(r"\r?\n\r?\n", pgn, maxsplit=1)[0]))


def game_id_of(game: str) -> str | None:
    """'185407637314' or any chess.com game link -> '185407637314'."""
    game = game.strip()
    if "/" not in game:
        return game if game.isdigit() else None
    m = _ID.search(game)
    return m.group(1) if m else None


def opening_label(pgn: str) -> str:
    """Full opening name from the ECOUrl, without the trailing move list chess.com appends."""
    full = opening_names(pgn)[1]
    return re.split(r"\.{3}|\d+\.", full)[0].strip() or full


def _iso(end_time: int) -> str:
    return datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(timespec="seconds")


def _count_plies(pgn: str) -> int:
    game = chess.pgn.read_game(io.StringIO(pgn))
    return sum(1 for _ in game.mainline_moves()) if game else 0


def list_games(conn: sqlite3.Connection, username: str, n: int = 10, time_class: str | None = None,
               color: str | None = None, opening: str | None = None,
               result: str | None = None) -> dict:
    if color not in (None, "white", "black"):
        raise ValueError("color must be 'white' or 'black'")
    if result not in (None, *_RESULTS):
        raise ValueError("result must be 'win', 'loss' or 'draw'")
    n = max(1, min(n, MAX_LIST))
    where, args = ["username = ?", "rules = 'chess'"], [username.lower()]
    for column, value in (("time_class", time_class), ("color", color)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    rows = conn.execute(
        f"""SELECT g.*, g.url IN (SELECT url FROM game_analysis) AS analyzed FROM games g
            WHERE {' AND '.join(where)} ORDER BY end_time DESC""", args)
    needle = opening.lower() if opening else None
    games = []
    for r in rows:
        if result and outcome(r["result"]) != _RESULTS[result]:
            continue
        label = opening_label(r["pgn"])
        if needle and needle not in f"{label} {opening_names(r['pgn'])[0]}".lower():
            continue
        h = headers(r["pgn"])
        opponent = h.get("Black" if r["color"] == "white" else "White")
        games.append({
            "game_id": r["url"].rsplit("/", 1)[-1], "url": r["url"], "end_time": _iso(r["end_time"]),
            "time_control": r["time_control"], "time_class": r["time_class"], "color": r["color"],
            "opponent": opponent, "opponent_rating": r["opp_rating"], "my_rating": r["my_rating"],
            "result": {"W": "win", "D": "draw", "L": "loss"}[outcome(r["result"])],
            "termination": h.get("Termination"), "eco": h.get("ECO"), "opening": label,
            "moves_count": (_count_plies(r["pgn"]) + 1) // 2, "analyzed": bool(r["analyzed"]),
        })
        if len(games) == n:
            break
    return {"count": len(games), "games": games}


def _eval(cp: int, mate: int | None) -> dict:
    """White-POV evaluation. A mate is reported as such and never as a big cp number."""
    if mate is None:
        return {"cp": cp, "mate": None}
    if mate == 0:   # the position is checkmate; cp's sign says who delivered it
        return {"cp": None, "mate": 0, "mated": "black" if cp > 0 else "white"}
    return {"cp": None, "mate": mate}


def _san(board: chess.Board, uci: str) -> str | None:
    """SAN of a stored UCI move, or None if it is malformed or illegal here."""
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    return board.san(move) if move in board.legal_moves else None


def _pv_san(board: chess.Board, ucis: list[str]) -> list[str]:
    board, out = board.copy(), []
    for uci in ucis[:PV_PLIES]:
        san = _san(board, uci)
        if san is None:
            break
        out.append(san)
        board.push(chess.Move.from_uci(uci))
    return out


def _find(conn: sqlite3.Connection, game: str) -> sqlite3.Row:
    gid = game_id_of(game)
    row = conn.execute("SELECT * FROM games WHERE url LIKE ? ORDER BY end_time DESC",
                       (f"%/{gid}",)).fetchone() if gid else None
    if row is None:
        raise ValueError(f"Game '{game}' is not in the local cache. Call sync_games for the "
                         "player first (and check the id or link).")
    return row


def get_game(conn: sqlite3.Connection, game: str, ply_from: int | None = None,
             ply_to: int | None = None, only_flagged: bool = False, include_pv: bool = True,
             username: str | None = None) -> dict:
    g = _find(conn, game)
    url = g["url"]
    h = headers(g["pgn"])
    parsed = chess.pgn.read_game(io.StringIO(g["pgn"]))
    played = list(parsed.mainline_moves())

    run = conn.execute("SELECT * FROM game_analysis WHERE url = ?", (url,)).fetchone()
    detail = conn.execute("SELECT engine, complete FROM game_detail WHERE url = ?", (url,)).fetchone()
    rows = {r["ply"]: r for r in conn.execute("SELECT * FROM positions WHERE url = ?", (url,))} if run else {}
    extra = {r["ply"]: r for r in conn.execute("SELECT * FROM position_detail WHERE url = ?", (url,))}
    analyzed = bool(run) and len(rows) == len(played) + 1

    tagged = {r["ply"]: r for r in conn.execute(
        "SELECT ply, tags, details FROM move_motif WHERE url = ? AND version = ?", (url, MOTIF_VERSION))}
    motifs_done = conn.execute("SELECT 1 FROM motif_game WHERE url = ? AND version = ?",
                               (url, MOTIF_VERSION)).fetchone() is not None

    clocks = parse_clocks(g["pgn"])
    clocks = clocks if len(clocks) >= len(played) else None
    base = {"time_control": g["time_control"]}

    moves, board = [], parsed.board()
    for i, move in enumerate(played):
        san, fen_before = board.san(move), board.fen()
        timing = clock_fields({**base, "ply": i}, clocks)
        m = {
            "ply": i + 1, "move_no": i // 2 + 1, "color": "white" if board.turn else "black",
            "san": san, "uci": move.uci(), "fen_before": fen_before,
            "eval_before": None, "eval_after": None, "win_chance_before": None,
            "win_chance_after": None, "wp_loss": None, "class": None,
            "mate_before_mover": None, "missed_mate": None, "mate_lengthened": None, "motifs": None,
            "best_san": None, "best_uci": None,
            "clock_after": round(clocks[i], 1) if clocks else None,
            "time_spent": round(timing["time_spent"], 1) if timing["time_spent"] is not None else None,
        }
        if include_pv:
            m["best_pv_san"] = None
        if analyzed:
            p, q = rows[i], rows[i + 1]
            wp0, wp1 = mover_win_pct(p["cp"], p["turn"]), mover_win_pct(q["cp"], p["turn"])
            loss = max(0.0, wp0 - wp1)
            kind = classify(loss)
            m.update({
                "eval_before": _eval(p["cp"], extra[i]["mate"] if i in extra else None),
                "eval_after": _eval(q["cp"], extra[i + 1]["mate"] if i + 1 in extra else None),
                "win_chance_before": pct(wp0), "win_chance_after": pct(wp1),
                "wp_loss": pct(loss),
                "class": ("best" if move.uci() == p["best_uci"] else "good") if kind == "ok" else kind,
            })
            mates = (p["turn"], extra[i]["mate"] if i in extra else None,
                     extra[i + 1]["mate"] if i + 1 in extra else None,
                     is_known(detail is not None, i in extra, p["cp"]),
                     is_known(detail is not None, i + 1 in extra, q["cp"]))
            m.update(mate_fields(*mates))
            mating = keeps_mate(*mates)       # as fast a mate as the engine's: not worse than its pick
            if mating and kind == "ok":
                m["class"] = "best"
            if i in tagged:
                m["motifs"] = json.loads(tagged[i]["tags"])
                details = json.loads(tagged[i]["details"])
                if details:
                    m["motif_details"] = details
            elif not in_scope(kind, m["missed_mate"]):
                m["motifs"] = []          # nothing to tag on a move this cheap
            if p["best_uci"] and p["best_uci"] != move.uci() and not mating:
                m["best_uci"] = p["best_uci"]
                m["best_san"] = _san(board, p["best_uci"])
            if include_pv and i in extra and extra[i]["pv"]:
                m["best_pv_san"] = _pv_san(board, extra[i]["pv"].split())
        moves.append(m)
        board.push(move)

    shown = moves
    if only_flagged:
        flagged = {m["ply"] for m in moves if m["class"] in _FLAGGED
                   or (m["missed_mate"] and m["missed_mate"] <= MISSED_MATE_MAX)}
        keep = {q for p in flagged for q in (p - 1, p, p + 1)}
        shown = [{**m, **({} if m["ply"] in flagged else {"context": True})}
                 for m in moves if m["ply"] in keep]
    shown = [m for m in shown
             if (ply_from is None or m["ply"] >= ply_from) and (ply_to is None or m["ply"] <= ply_to)]

    me = (username or "").lower()
    meta = {
        "game_id": url.rsplit("/", 1)[-1], "url": url, "end_time": _iso(g["end_time"]),
        "time_control": g["time_control"], "time_class": g["time_class"],
        "white": h.get("White"), "black": h.get("Black"),
        "white_elo": int(h["WhiteElo"]) if h.get("WhiteElo") else None,
        "black_elo": int(h["BlackElo"]) if h.get("BlackElo") else None,
        "my_color": next((c for c in ("white", "black") if me and h.get(c.title(), "").lower() == me), None),
        "result": h.get("Result"), "termination": h.get("Termination"), "eco": h.get("ECO"),
        "opening": opening_label(g["pgn"]), "analyzed": analyzed,
        "engine": (detail["engine"] if detail and detail["engine"] else "Stockfish") if analyzed else None,
        "engine_depth": run["deep_depth"] if analyzed else None,
        "total_plies": len(played),
        "motifs_tagged": motifs_done,
    }
    out = {"meta": meta, "moves": shown}
    if analyzed:
        meta["mate_data"] = "missing" if not detail else "complete" if detail["complete"] else "partial"
        if meta["mate_data"] == "missing":
            out["note"] = (
                "Analysed before mate and variation data were stored: cp is clipped at +-1000 and "
                "may hide a mate, and best_pv_san is only filled for costly moves. The mate fields "
                "(mate_before_mover, missed_mate, mate_lengthened) are null because they are "
                "unknown, not because there was no mate. Run backfill_game_details to fill them in.")
        elif meta["mate_data"] == "partial":
            out["note"] = (
                "Backfilled: mate and best_pv_san exist only for costly moves and positions at 900 cp "
                "or more (null elsewhere). Below that no mate is assumed, so the mate fields can be "
                "trusted; best_pv_san is simply absent for quiet moves.")
    elif only_flagged:
        out["note"] = "This game has not been analysed, so no move can be flagged. Run analyze_games."
    return out
