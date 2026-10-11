"""Comparing a player with others of similar rating.

The reference numbers come from `benchmarks.json`: medians and quartiles per rating band,
built by scripts/build_benchmarks.py from public 3+0 blitz games. The file holds band-level
summaries only, no usernames and no games. The samples are small; every band reports how
many players it rests on so the numbers are never read as more certain than they are."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import coach
from .moves import my_moves

BENCHMARKS = Path(__file__).with_name("benchmarks.json")
TIME_CONTROL = "180"        # the benchmark games are 3+0, so the player is measured on 3+0 too
MIN_PLAYERS = 8             # bands built from fewer players are flagged as tentative

# metric -> (label, True when a lower value is better)
METRICS = {
    "score_pct": ("Score", False),
    "blunders_per_100_moves": ("Blunders per 100 moves", True),
    "avg_wp_loss": ("Win chance lost per move", True),
    "lost_on_time_pct_of_games": ("Games lost on time (%)", True),
    "winning_positions_not_won_pct": ("Winning positions not won (%)", True),
    "opening_blunders_per_100": ("Opening blunders per 100", True),
    "middlegame_blunders_per_100": ("Middlegame blunders per 100", True),
    "endgame_blunders_per_100": ("Endgame blunders per 100", True),
}


def load_benchmarks(path: Path = BENCHMARKS) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _midpoint(band: str) -> float:
    lo, hi = band.split("-")
    return (int(lo) + int(hi)) / 2


def closest_band(rating: float, bands: dict) -> str | None:
    return min(bands, key=lambda b: abs(_midpoint(b) - rating), default=None)


def position(value: float, band_stats: dict) -> str:
    """Where `value` sits against a band's middle half of players."""
    if band_stats.get("n", 0) < 4 or "p25" not in band_stats:
        return "unknown"
    if value < band_stats["p25"]:
        return "below"
    return "above" if value > band_stats["p75"] else "within"


def compare(user: dict | None, rating: float | None, bands: dict) -> dict:
    """Line the player's metrics up against every band, flagging the closest band."""
    if not user or not bands:
        return {"available": False, "note": "Not enough 3+0 blitz games, or no benchmark data."}
    near = closest_band(rating, bands) if rating else None
    rows = []
    for key, (label, lower_better) in METRICS.items():
        value = user.get(key)
        if value is None:
            continue
        per_band = {}
        for band, data in bands.items():
            stats = data.get(key, {})
            per_band[band] = {**stats, "players": data.get("players")}
        row = {"metric": key, "label": label, "lower_is_better": lower_better, "you": value, "bands": per_band}
        if near:
            row["against_closest_band"] = position(value, per_band[near])
        rows.append(row)
    return {
        "available": True,
        "your_games": user["games"],
        "your_rating": rating,
        "closest_band": near,
        "tentative_bands": [b for b, d in bands.items() if (d.get("players") or 0) < MIN_PLAYERS],
        "metrics": rows,
        "note": "Peers are public players sampled from a few countries and the titled-player lists, "
                "measured on 3+0 blitz only. Treat differences inside the middle half (p25-p75) as ordinary.",
    }


def peer_report(conn: sqlite3.Connection, username: str) -> dict:
    moves = [m for m in my_moves(conn, username, "blitz") if m["time_control"] == TIME_CONTROL]
    user = coach.peer_metrics(moves)
    rating = moves[0]["my_rating"] if moves else None
    return compare(user, rating, load_benchmarks())
