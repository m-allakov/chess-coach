"""End-to-end smoke test: start the MCP server over stdio and call its tools the way a
real MCP client (Claude Desktop/Code) would.

Usage: python scripts/smoke_mcp.py <db> [username]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DB = sys.argv[1]
USER = sys.argv[2] if len(sys.argv) > 2 else "sample_player"

CALLS = [
    ("analysis_status", {"username": USER}),
    ("coach_plan", {"username": USER}),
    ("peer_comparison", {"username": USER}),
    ("tilt_report", {"username": USER, "time_class": "blitz"}),
    ("time_of_day_report", {"username": USER}),
    ("resilience_report", {"username": USER}),
    ("weakness_report", {"username": USER, "last_n": 100}),
    ("opening_report", {"username": USER, "min_games": 8, "time_class": "blitz"}),
    ("time_pressure_report", {"username": USER, "time_class": "blitz"}),
    ("worst_moments", {"username": USER, "n": 3}),
    ("missed_opportunities", {"username": USER, "n": 3}),
    ("sacrifice_candidates", {"username": USER, "n": 3}),
    ("list_games", {"username": USER, "n": 3, "time_class": "blitz"}),
    ("get_game", {"game": "185407637314", "username": USER, "only_flagged": True}),
    ("get_game", {"game": "https://www.chess.com/game/live/185407637314", "username": USER}),
    ("backfill_game_details", {"username": USER, "batch_size": 1, "max_seconds": 60}),
    ("sync_games", {"username": USER, "months": 1}),
    ("analyze_games", {"username": USER, "batch_size": 3, "max_seconds": 60}),
]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "chess_coach.server"],
        env={**os.environ, "CHESS_COACH_DB": DB},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"server exposes {len(tools)} tools: {', '.join(t.name for t in tools)}\n")
            print(f"{'tool':<22}{'seconds':>8}{'chars':>8}  result")
            failures = 0
            for name, args in CALLS:
                t0 = time.perf_counter()
                result = await session.call_tool(name, args)
                secs = time.perf_counter() - t0
                text = "".join(c.text for c in result.content if getattr(c, "text", None))
                ok = not result.isError
                try:
                    json.loads(text)
                except ValueError:
                    ok = False
                failures += not ok
                preview = text[:90].replace("\n", " ")
                print(f"{name:<22}{secs:>8.1f}{len(text):>8}  {'OK ' if ok else 'FAIL'} {preview}")
            print(f"\n{len(CALLS) - failures}/{len(CALLS)} calls returned valid JSON")


if __name__ == "__main__":
    asyncio.run(main())
