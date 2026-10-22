"""MCP server entry point."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import coach, gameview, opportunities, patterns, peers, reports
from .analysis import analyze_pending, backfill_details, default_engine_factory
from .analysis import status as analysis_progress
from .chesscom import ChessComClient
from .config import stockfish_path
from .db import connect
from .sync import sync_games as _sync_games

mcp = FastMCP("chess-coach")


@mcp.tool()
def sync_games(username: str, months: int = 3) -> dict:
    """Download a chess.com player's recent games into the local cache.

    Only standard chess is stored. Already-cached games are skipped, so it is
    safe to call repeatedly. Returns counts, not the games themselves.
    """
    client = ChessComClient()
    try:
        return _sync_games(connect(), client, username, months)
    finally:
        client.close()


@mcp.tool()
def analyze_games(username: str, batch_size: int = 25, max_seconds: float = 45) -> dict:
    """Analyse not-yet-analysed games with Stockfish, newest first.

    Works in small batches so one call stays fast; call again to continue.
    Returns progress counts only. Use sync_games first to download games.
    """
    return analyze_pending(
        connect(), default_engine_factory(stockfish_path()), username,
        limit=batch_size, max_seconds=max_seconds,
    )


@mcp.tool()
def analysis_status(username: str) -> dict:
    """How many of a player's cached games are analysed vs pending."""
    return analysis_progress(connect(), username)


@mcp.tool()
def backfill_game_details(username: str, batch_size: int = 25, max_seconds: float = 45) -> dict:
    """Add mate scores and best-line (PV) data to games analysed before they were stored,
    so get_game can show them. Re-searches only costly moves and near-decided positions;
    stored evaluations never change. Works in batches: call again until backfill_pending is 0."""
    return backfill_details(
        connect(), default_engine_factory(stockfish_path()), username,
        limit=batch_size, max_seconds=max_seconds,
    )


@mcp.tool()
def list_games(username: str, n: int = 10, time_class: str | None = None,
               color: str | None = None, opening: str | None = None,
               result: str | None = None) -> dict:
    """The player's cached games, newest first, to find a game_id for get_game.

    Filters: time_class (bullet/blitz/rapid), color ("white"/"black"), result from the
    player's side ("win"/"loss"/"draw"), opening (case-insensitive substring of the name).
    Each game has game_id, url, end_time (ISO 8601, UTC), time_control, time_class, color,
    opponent, opponent_rating, my_rating, result, termination, eco, opening, moves_count
    (full moves) and analyzed."""
    return gameview.list_games(connect(), username, n, time_class, color, opening, result)


@mcp.tool()
def get_game(game: str, ply_from: int | None = None, ply_to: int | None = None,
             only_flagged: bool = False, include_pv: bool = True,
             username: str | None = None) -> dict:
    """One cached game move by move, from the stored analysis (no engine is run).

    `game` is a game_id or a chess.com game link. Pass `username` so meta.my_color is set.
    ply_from/ply_to are an inclusive range. only_flagged keeps just inaccuracies, mistakes and
    blunders of either side, plus the ply before and after each (marked "context": true).

    Conventions:
    - ply 1 is White's first move, ply 2 Black's first; Black's n-th move is ply 2n.
    - eval_before/eval_after are always from WHITE's point of view: {"cp", "mate"}. cp is
      clipped to +-1000. When a mate is known, cp is null and mate is the number of moves
      (positive = White mates). mate 0 = the position is checkmate ("mated" names the loser).
    - win_chance_before/after and wp_loss (0-100 scale) are from the point of view of the side
      that MADE the move, the same as worst_moments. class is best/good/inaccuracy/mistake/
      blunder, using the same thresholds as the reports. No opening-book detection exists.
    - Every win chance, loss and percentage in every tool is rounded to 1 decimal, halves up,
      so the same move reads the same everywhere. Classes use the unrounded value.
    - mate_before_mover: N when the mover had a forced mate in N before moving. missed_mate: N
      when the move left no mate for the mover (class is NOT changed by this, a missed mate can
      still read "good"). mate_lengthened: {"from": N, "to": M} when a mate survives but takes
      more than N-1 moves. All null when there was no mate or the mate data is unknown (see
      meta.mate_data and the note). only_flagged also keeps a missed mate of 5 moves or fewer.
    - best_san/best_uci are null when the move played was the engine's best; best_pv_san
      (up to 5 plies, starts with the best move) is still given, or null if not stored.
    - clock_after is seconds left after the move; time_spent includes the increment.
      Both are null when the game has no clock data.
    - An unanalysed game returns SAN/UCI/FEN/clock with all evaluation fields null.
    """
    return gameview.get_game(connect(), game, ply_from, ply_to, only_flagged, include_pv, username)


@mcp.tool()
def weakness_report(username: str, time_class: str | None = None, last_n: int | None = None) -> dict:
    """Where a player loses most: by game phase, colour and time control, and how
    often winning positions are thrown away. Optionally limit to a time class
    (bullet/blitz/rapid) and/or the most recent N analysed games."""
    return reports.weakness_report(connect(), username, time_class, last_n)


@mcp.tool()
def opening_report(username: str, min_games: int = 3, time_class: str | None = None,
                   last_n: int | None = None) -> dict:
    """Results and opening-phase quality per opening and colour, worst first."""
    return reports.opening_report(connect(), username, min_games, time_class, last_n)


@mcp.tool()
def time_pressure_report(username: str, time_class: str | None = None,
                         last_n: int | None = None) -> dict:
    """How the clock affects a player's results: errors by time-pressure zone, hasty vs
    considered blunders, time spent per phase, and games lost on time."""
    return reports.time_pressure_report(connect(), username, time_class, last_n)


@mcp.tool()
def missed_opportunities(username: str, n: int = 5, time_class: str | None = None,
                         last_n: int | None = None) -> dict:
    """How often the player used the opponent's blunders, and the costliest chances missed.
    `missed_mates` lists forced mates the player had and let go: shortest mate first, then newest."""
    return opportunities.missed_opportunities(connect(), username, n, time_class, last_n)


@mcp.tool()
def sacrifice_candidates(username: str, n: int = 10, time_class: str | None = None,
                         last_n: int | None = None) -> dict:
    """Sound moves that put a piece on offer. Approximates chess.com's 'brilliant'
    label but over-reports (about ten false candidates per real brilliant)."""
    return opportunities.sacrifice_candidates(connect(), username, n, time_class, last_n)


@mcp.tool()
def coach_plan(username: str, time_class: str | None = None, recent_games: int = 100) -> dict:
    """Start here. Why the player's recent games were not won (one primary cause each),
    one focus for the coming week with the evidence, and the trend against earlier games.
    Use the other reports to drill into the focus area."""
    return coach.coach_plan(connect(), username, time_class, recent_games)


@mcp.tool()
def tilt_report(username: str, time_class: str | None = None, session_gap_minutes: float = 30) -> dict:
    """Does the player play worse after losses, or later in a session? Games are grouped into
    sessions (a pause longer than the gap starts a new one). Differences are reported
    only when they are larger than sampling noise."""
    return patterns.tilt_report(connect(), username, time_class, session_gap_minutes)


@mcp.tool()
def time_of_day_report(username: str, time_class: str | None = None) -> dict:
    """Results and blunder rate by time of day (local clock) and weekday vs weekend."""
    return patterns.time_of_day_report(connect(), username, time_class)


@mcp.tool()
def resilience_report(username: str, time_class: str | None = None) -> dict:
    """How often the player saved (won or drew) a game after a clearly worse position."""
    return patterns.resilience_report(connect(), username, time_class)


@mcp.tool()
def peer_comparison(username: str) -> dict:
    """Compare the player's 3+0 blitz numbers with players of other rating bands (medians and
    quartiles from public games). Sample sizes are listed per band; thin bands are flagged
    tentative and give no verdict."""
    return peers.peer_report(connect(), username)


@mcp.tool()
def worst_moments(username: str, n: int = 5, time_class: str | None = None,
                  last_n: int | None = None) -> dict:
    """The player's costliest confirmed blunders with FEN, move played and best move."""
    return reports.worst_moments(connect(), username, n, time_class, last_n)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
