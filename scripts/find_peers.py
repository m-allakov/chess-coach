"""Find active chess.com players in given blitz-rating bands for benchmarking.

chess.com has no "players rated X" endpoint, so this samples public player lists
(a few countries plus the titled-player lists for the top band) and checks each
player's public stats one request at a time.

Usage: python scripts/find_peers.py <out.json> [per_band] [max_checks]
"""

from __future__ import annotations

import json
import random
import sys
import time

import httpx

API = "https://api.chess.com/pub"
HEADERS = {"User-Agent": "chess-coach-mcp/0.1 (personal benchmarking project)"}
BANDS = {"700-900": (700, 900), "1100-1300": (1100, 1300), "1500-1700": (1500, 1700), "1900-2100": (1900, 2100)}
COUNTRIES = ["TR", "IS", "LT"]
TITLED = ["CM", "NM"]
MAX_AGE_DAYS = 45      # must have played blitz recently
MAX_RD = 90            # rating deviation: skip unsettled ratings


def players(client: httpx.Client, path: str) -> list[str]:
    r = client.get(path)
    r.raise_for_status()
    return r.json()["players"]


def blitz_rating(client: httpx.Client, name: str) -> tuple[int, int, int] | None:
    r = client.get(f"/player/{name}/stats")
    if r.status_code != 200:
        return None
    last = r.json().get("chess_blitz", {}).get("last")
    if not last or "rd" not in last:   # no deviation recorded: not an active, settled rating
        return None
    return last["rating"], last["rd"], last["date"]


def main() -> None:
    out, per_band = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10
    max_checks = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    rng = random.Random(2026)
    found = {band: [] for band in BANDS}
    with httpx.Client(base_url=API, headers=HEADERS, timeout=30) as client:
        pool = []
        for c in COUNTRIES:
            pool += [("country", p) for p in players(client, f"/country/{c}/players")]
            time.sleep(0.3)
        for t in TITLED:
            pool += [("titled", p) for p in players(client, f"/titled/{t}")]
            time.sleep(0.3)
        rng.shuffle(pool)
        print(f"pool: {len(pool)} names", flush=True)

        now = time.time()
        checked = 0
        for source, name in pool:
            if all(len(v) >= per_band for v in found.values()) or checked >= max_checks:
                break
            # Titled players only help the top band; skip them once it is full.
            if source == "titled" and len(found["1900-2100"]) >= per_band:
                continue
            checked += 1
            time.sleep(0.12)
            info = blitz_rating(client, name)
            if not info:
                continue
            rating, rd, last = info
            if rd > MAX_RD or now - last > MAX_AGE_DAYS * 86400:
                continue
            for band, (lo, hi) in BANDS.items():
                if lo <= rating <= hi and len(found[band]) < per_band:
                    found[band].append({"username": name, "rating": rating})
            if checked % 100 == 0:
                print(f"  checked {checked}: " + ", ".join(f"{b} {len(v)}" for b, v in found.items()), flush=True)

    json.dump(found, open(out, "w"), indent=1)
    print("\nchecked", checked, "players")
    for band, v in found.items():
        print(f"  {band:<10} {len(v)} players")


if __name__ == "__main__":
    main()
