"""Local web dashboard: the same reports as the MCP server, as a page.

Serves a static single-page app plus a small JSON API. Binds to 127.0.0.1 only: there
is no login, so it must not be reachable from other machines.

Run: python -m chess_coach.web [--port 8765] [--username NAME]
"""

from __future__ import annotations

import argparse
import os
import re
import threading
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import analysis, coach, opportunities, patterns, peers, practice, reports, timeline, trainer
from .config import stockfish_path
from .db import DEFAULT_DB, connect
from .moves import my_moves

STATIC = Path(__file__).with_name("web_static")
_USERNAME = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
_TIME_CLASSES = {"bullet", "blitz", "rapid"}


LOCAL_HOSTS = ["127.0.0.1", "localhost"]


class NoCacheStatic(StaticFiles):
    """Static files that the browser must revalidate on every load. This is a local app that changes
    often; a cached script from before an update would otherwise keep running with stale logic."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(db_path: str | Path | None = None, default_username: str = "",
               engine_factory=None, allowed_hosts: list[str] | None = None) -> Starlette:
    """`engine_factory` builds the engine used to judge trainer answers; it is started
    on first use and shared (one answer is judged at a time).

    Requests are only accepted for local host names. The server has no login, and a web
    page on another site could otherwise reach it through DNS rebinding (a hostile name
    that resolves to 127.0.0.1) and read your games."""
    db = db_path or DEFAULT_DB
    engine_box: dict = {"engine": None}
    engine_lock = threading.Lock()

    def judge(username: str, pid: str, uci: str) -> dict:
        # Runs in a worker thread: the connection must be opened here, not in the request
        # thread, because SQLite connections cannot cross threads.
        conn = connect(db)
        try:
            with engine_lock:
                if engine_box["engine"] is None:
                    factory = engine_factory or analysis.default_engine_factory(
                        stockfish_path(), threads=1, hash_mb=64)
                    engine_box["engine"] = factory()
                result = trainer.check(conn, engine_box["engine"], username, pid, uci)
            if "error" not in result:
                practice.record(conn, username, pid, result["solved"])
            return result
        finally:
            conn.close()

    def params(request: Request) -> tuple[str, str | None, int | None]:
        username = request.query_params.get("username") or default_username
        if not _USERNAME.match(username or ""):
            raise HTTPException(400, "Missing or invalid username")
        time_class = request.query_params.get("time_class") or None
        if time_class and time_class not in _TIME_CLASSES:
            raise HTTPException(400, "time_class must be bullet, blitz or rapid")
        last_n = request.query_params.get("last_n")
        if last_n and not last_n.isdigit():
            raise HTTPException(400, "last_n must be a number")
        return username, time_class, int(last_n) if last_n else None

    cache: dict[tuple, tuple] = {}
    cache_lock = threading.Lock()

    def analysis_version(conn) -> tuple:
        """Changes whenever a game is analysed or the benchmark file is replaced, which is
        when cached reports go stale."""
        analysed = tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(analyzed_at), 0) FROM game_analysis").fetchone())
        try:
            bench = peers.BENCHMARKS.stat().st_mtime_ns
        except OSError:
            bench = 0
        return (*analysed, bench)

    def load(name: str, fn, username: str, time_class, last_n) -> dict:
        # Runs in a worker thread, so the connection is opened here (SQLite connections
        # cannot cross threads). Reports are pure functions of the database: results are
        # reused until the number of analysed games changes.
        conn = connect(db)
        try:
            version = analysis_version(conn)
            key = (name, username, time_class, last_n)
            with cache_lock:
                hit = cache.get(key)
            if hit and hit[0] == version:
                return hit[1]
            payload = fn(conn, username, time_class, last_n)
            with cache_lock:
                if len(cache) >= 96:
                    cache.pop(next(iter(cache)))
                cache[key] = (version, payload)
            return payload
        finally:
            conn.close()

    def api(name: str, fn):
        """Wrap a report function: validate params, compute off the event loop, return JSON."""
        async def endpoint(request: Request) -> JSONResponse:
            username, time_class, last_n = params(request)
            return JSONResponse(await run_in_threadpool(load, name, fn, username, time_class, last_n))
        return endpoint

    def patterns_payload(c, u, tc, n) -> dict:
        games = coach.game_summaries(my_moves(c, u, tc))   # read the moves once for all three
        if not games:
            return {"tilt": {"games": 0}, "time_of_day": {"games": 0}, "resilience": {"games": 0}}
        return {
            "tilt": patterns.tilt_from_games(games),
            "time_of_day": patterns.time_of_day_from_games(games),
            "resilience": patterns.resilience_from_games(games),
        }

    async def game_view(request: Request) -> JSONResponse:
        username, _, _ = params(request)
        game_id = request.query_params.get("id", "")
        if not game_id.isdigit():
            raise HTTPException(400, "id must be a game number")

        def fetch():
            conn = connect(db)      # opened in the worker thread
            try:
                return timeline.game_detail(conn, username, game_id)
            finally:
                conn.close()

        detail = await run_in_threadpool(fetch)
        if detail is None:
            raise HTTPException(404, "Game not found or not analysed")
        return JSONResponse(detail)

    async def trainer_list(request: Request) -> JSONResponse:
        username, time_class, _ = params(request)
        count = min(max(int(request.query_params.get("n", "12")), 1), 50)             if request.query_params.get("n", "12").isdigit() else 12
        exclude = {x for x in request.query_params.get("exclude", "").split(",") if x}
        conn = connect(db)
        try:
            mode = request.query_params.get("mode", "own")
            if mode not in ("own", "erred"):
                raise HTTPException(400, "mode must be own or erred")
            pick = trainer.erred_puzzles if mode == "erred" else trainer.puzzles
            exclude |= practice.resting(conn, username)
            data = pick(conn, username, count, time_class, exclude=exclude)
            data["practice"] = practice.summary(conn, username)
            return JSONResponse(data)
        finally:
            conn.close()

    async def trainer_check(request: Request) -> JSONResponse:
        username, _, _ = params(request)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Body must be JSON")
        pid, uci = str(body.get("id", "")), str(body.get("uci", ""))
        result = await run_in_threadpool(judge, username, pid, uci)
        return JSONResponse(result, status_code=400 if "error" in result else 200)

    async def practice_import(request: Request) -> JSONResponse:
        username, _, _ = params(request)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Body must be an object of puzzle ids")
        conn = connect(db)
        try:
            return JSONResponse({"imported": practice.import_progress(conn, username, body)})
        finally:
            conn.close()

    routes = [
        Route("/api/config", lambda request: JSONResponse({"default_username": default_username})),
        Route("/api/status", api("status", lambda c, u, tc, n: analysis.status(c, u))),
        Route("/api/coach", api("coach", lambda c, u, tc, n: coach.coach_plan(c, u, tc))),
        Route("/api/weakness", api("weakness", lambda c, u, tc, n: reports.weakness_report(c, u, tc, n))),
        Route("/api/openings", api("openings", lambda c, u, tc, n: reports.opening_report(c, u, 5, tc, n))),
        Route("/api/clock", api("clock", lambda c, u, tc, n: reports.time_pressure_report(c, u, tc, n))),
        Route("/api/patterns", api("patterns", patterns_payload)),
        Route("/api/moments", api("moments", lambda c, u, tc, n: {
            "blunders": reports.worst_moments(c, u, 8, tc, n),
            "missed": opportunities.missed_opportunities(c, u, 6, tc, n),
        })),
        Route("/api/season", api("season", lambda c, u, tc, n: {
            "games": timeline.game_curves(c, u, n or 60, tc),
            "days": timeline.daily_results(c, u, 140, tc),
        })),
        Route("/api/peers", api("peers", lambda c, u, tc, n: peers.peer_report(c, u))),
        Route("/api/game", game_view),
        Route("/api/trainer", trainer_list),
        Route("/api/trainer/check", trainer_check, methods=["POST"]),
        Route("/api/practice/import", practice_import, methods=["POST"]),
        Route("/", lambda request: FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})),
        Mount("/static", NoCacheStatic(directory=STATIC), name="static"),
    ]

    async def error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return Starlette(routes=routes, exception_handlers={HTTPException: error},
                     middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)])


def main() -> None:
    parser = argparse.ArgumentParser(prog="chess-coach-web")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--username", default=os.environ.get("CHESS_COACH_USER", ""),
                        help="chess.com username shown by default (or set CHESS_COACH_USER)")
    parser.add_argument("--db", default=None, help="path to games.db (default: CHESS_COACH_DB or ~/.chess_coach)")
    args = parser.parse_args()
    # 127.0.0.1 on purpose: no authentication, never expose this beyond the local machine.
    uvicorn.run(create_app(args.db, default_username=args.username), host="127.0.0.1", port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
