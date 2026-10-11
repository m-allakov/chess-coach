"""Check the two-pass analysis against a deep reference search.

For a random sample of already-analysed games, every position is re-searched
at depth 12 (fast pass only) and depth 20 (reference). Move classifications
from three sources are compared with the reference:

  fast       depth 12 everywhere
  two-pass   what is stored in the database (depth 12, costly moves at 16)
  reference  depth 20 everywhere

Usage: python scripts/validate_two_pass.py <db> <stockfish.exe> [n_games] [min_rating max_rating]
"""

from __future__ import annotations

import random
import sqlite3
import sys
import time
from collections import Counter

import chess
import chess.engine

from chess_coach.analysis import search
from chess_coach.classify import classify, mover_win_pct

FAST, REF = 12, 20
KINDS = ("ok", "inaccuracy", "mistake", "blunder")


def kinds(cps: list[int], turns: list[str]) -> list[str]:
    out = []
    for i in range(len(cps) - 1):
        before = mover_win_pct(cps[i], turns[i])
        after = mover_win_pct(cps[i + 1], turns[i])
        out.append(classify(max(0.0, before - after)))
    return out


def compare(name: str, got: list[str], ref: list[str]) -> None:
    n = len(ref)
    agree = sum(g == r for g, r in zip(got, ref)) / n
    off_by_two = sum(abs(KINDS.index(g) - KINDS.index(r)) >= 2 for g, r in zip(got, ref)) / n

    def pr(level: str) -> tuple[float, float]:
        rank = KINDS.index(level)
        g = {i for i, k in enumerate(got) if KINDS.index(k) >= rank}
        r = {i for i, k in enumerate(ref) if KINDS.index(k) >= rank}
        hit = len(g & r)
        return (hit / len(g) if g else 1.0, hit / len(r) if r else 1.0)

    bp, br = pr("blunder")
    mp, mr = pr("mistake")
    print(f"{name:<9} | same class {agree:6.1%} | off by 2+ {off_by_two:5.1%} | "
          f"blunder P {bp:5.1%} R {br:5.1%} | mistake+ P {mp:5.1%} R {mr:5.1%}")


def main() -> None:
    db, sf = sys.argv[1], sys.argv[2]
    n_games = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    lo = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    hi = int(sys.argv[5]) if len(sys.argv) > 5 else 9999

    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    # Rating of the player whose analysed games these are; selects a rating band.
    urls = [r[0] for r in conn.execute(
        "SELECT g.url FROM games g JOIN game_analysis a ON a.url = g.url "
        "WHERE g.my_rating BETWEEN ? AND ?", (lo, hi))]
    print(f"{len(urls)} analysed games rated {lo}-{hi}", flush=True)
    random.Random(11).shuffle(urls)
    urls = urls[:n_games]

    engine = chess.engine.SimpleEngine.popen_uci(sf)
    engine.configure({"Threads": 4, "Hash": 256})

    fast_all, two_all, ref_all = [], [], []
    t0 = time.perf_counter()
    for k, url in enumerate(urls, 1):
        rows = conn.execute("SELECT fen, turn, cp FROM positions WHERE url=? ORDER BY ply", (url,)).fetchall()
        turns = [r["turn"] for r in rows]
        stored = [r["cp"] for r in rows]
        fast = [search(engine, chess.Board(r["fen"]), FAST)[0] for r in rows]
        ref = [search(engine, chess.Board(r["fen"]), REF)[0] for r in rows]
        fast_all += kinds(fast, turns)
        two_all += kinds(stored, turns)
        ref_all += kinds(ref, turns)
        print(f"[{k}/{len(urls)}] {len(rows)-1} moves, {time.perf_counter()-t0:.0f}s", flush=True)
    engine.quit()

    print(f"\n{len(urls)} games, {len(ref_all)} moves. Reference = depth {REF}")
    print("reference mix:", dict(Counter(ref_all)))
    print()
    compare("fast", fast_all, ref_all)
    compare("two-pass", two_all, ref_all)


if __name__ == "__main__":
    main()
