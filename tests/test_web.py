import chess
import chess.engine
import pytest
from starlette.testclient import TestClient

from chess_coach.db import connect
from chess_coach.web import create_app


class FakeEngine:
    """Judges every position as White-POV `cp` centipawns (default -50), whatever the move."""

    def __init__(self, cp=-50):
        self.cp = cp

    def analyse(self, board, limit):
        return {"score": chess.engine.PovScore(chess.engine.Cp(self.cp), chess.WHITE),
                "pv": [next(iter(board.legal_moves))]}

    def quit(self):
        pass


@pytest.fixture()
def client(tmp_path):
    """App over a real (temporary) database holding one analysed game with a blunder."""
    path = tmp_path / "games.db"
    conn = connect(path)
    conn.execute(
        """INSERT INTO games VALUES ('https://www.chess.com/game/live/1','me',1,'blitz','180','chess',1,'white',
        800,800,'resigned',?)""",
        ('[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening"]\n\n'
         '1. e4 {[%clk 0:03:00]} 1... e5 {[%clk 0:02:59]} 2. d4 {[%clk 0:02:58]} 2... exd4 {[%clk 0:02:57]}',),
    )
    fen = chess.Board().fen()
    for ply, (turn, cp) in enumerate([("white", 0), ("black", 20), ("white", 20), ("black", -900)]):
        conn.execute("INSERT INTO positions VALUES ('https://www.chess.com/game/live/1',?,?,?,?,?,?,?,?,?)",
                     (ply, fen, turn, "opening", "e4", "e2e4", cp, "d2d4", 16))
    conn.execute("INSERT INTO game_analysis VALUES ('https://www.chess.com/game/live/1',12,16,2,1.0,1)")
    conn.commit()
    conn.close()
    app = TestClient(create_app(path, default_username="me", engine_factory=FakeEngine,
                                allowed_hosts=["testserver"]))
    app.db_path = path
    return app


def test_index_and_static_files_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "Chess Coach" in page.text
    for name in ("app.js", "board.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200


def test_config_reports_the_default_username(client):
    assert client.get("/api/config").json() == {"default_username": "me"}


@pytest.mark.parametrize("path, key", [
    ("/api/status", "games_total"),
    ("/api/coach", "weekly_focus"),
    ("/api/weakness", "by_phase"),
    ("/api/openings", "openings"),
    ("/api/clock", "by_time_zone"),
    ("/api/patterns", "tilt"),
    ("/api/moments", "blunders"),
])
def test_every_endpoint_returns_json_with_its_main_key(client, path, key):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert key in r.json()


def test_moments_carry_uci_moves_for_drawing_arrows(client):
    blunder = client.get("/api/moments").json()["blunders"]["blunders"][0]
    assert blunder["played_uci"] == "e2e4" and blunder["best_uci"] == "d2d4"


def test_username_can_be_overridden_and_is_validated(client):
    assert client.get("/api/status?username=someone").json()["games_total"] == 0
    bad = client.get("/api/status?username=../etc/passwd")
    assert bad.status_code == 400 and "error" in bad.json()


def test_filters_are_validated(client):
    assert client.get("/api/coach?time_class=correspondence").status_code == 400
    assert client.get("/api/weakness?last_n=abc").status_code == 400
    assert client.get("/api/weakness?time_class=blitz&last_n=10").status_code == 200


def test_missing_username_without_a_default_is_rejected(tmp_path):
    app = TestClient(create_app(tmp_path / "x.db", default_username="", allowed_hosts=["testserver"]))
    assert app.get("/api/status").status_code == 400


def test_season_has_curves_with_blunder_marks_and_daily_results(client):
    season = client.get("/api/season").json()
    game = season["games"][0]
    assert game["id"] == "1" and game["outcome"] == "L" and game["color"] == "white"
    assert len(game["curve"]) == 4 and game["curve"][0] == 50.0
    assert game["blunders"] == [3]                 # White's move 2 dropped the evaluation to -900
    assert season["days"][0]["games"] == 1 and season["days"][0]["L"] == 1


def test_trainer_lists_the_blunder_without_revealing_the_answer(client):
    data = client.get("/api/trainer").json()
    assert data["available"] == 1
    puzzle = data["puzzles"][0]
    assert puzzle["id"] == "1:2" and puzzle["color"] == "white"
    assert "best_uci" not in puzzle and "best" not in puzzle
    assert client.get("/api/trainer?exclude=1:2").json()["puzzles"] == []


def test_trainer_accepts_the_engines_move_and_judges_other_moves(client):
    best = client.post("/api/trainer/check", json={"id": "1:2", "uci": "d2d4"}).json()
    assert best["verdict"] == "best" and best["solved"] is True and best["best"] == "d4"
    other = client.post("/api/trainer/check", json={"id": "1:2", "uci": "g1f3"}).json()
    # The position was +20 cp (51.8% win chance); the fake engine rates the answer -50 cp (45.4%).
    assert other["verdict"] == "inaccurate" and other["solved"] is False
    assert 6 < other["win_chance_lost"] < 7
    assert other["played_in_game"] == "e4"


def test_trainer_rejects_illegal_unknown_and_malformed_answers(client):
    assert client.post("/api/trainer/check", json={"id": "1:2", "uci": "e2e5"}).status_code == 400
    assert client.post("/api/trainer/check", json={"id": "99:2", "uci": "d2d4"}).status_code == 400
    assert client.post("/api/trainer/check", json={"id": "1:2", "uci": "zz"}).status_code == 400
    assert client.post("/api/trainer/check", content=b"not json").status_code == 400


def test_cached_reports_refresh_when_another_game_is_analysed(client):
    assert client.get("/api/status").json()["games_analyzed"] == 1
    assert client.get("/api/status").json()["games_analyzed"] == 1      # served from the cache
    conn = connect(client.db_path)
    conn.execute("""INSERT INTO games VALUES ('https://www.chess.com/game/live/2','me',2,'blitz','180','chess',1,'white',
                    800,800,'win','')""")
    conn.execute("INSERT INTO game_analysis VALUES ('https://www.chess.com/game/live/2',12,16,0,1.0,2)")
    conn.commit()
    conn.close()
    assert client.get("/api/status").json()["games_analyzed"] == 2


def test_game_viewer_returns_every_position_with_graded_moves(client):
    g = client.get("/api/game?id=1").json()
    assert g["color"] == "white" and g["outcome"] == "L" and g["opening"] == "Kings Pawn Opening"
    assert len(g["plies"]) == 4
    first, third = g["plies"][0], g["plies"][2]
    assert first["san"] == "e4" and first["best_san"] == "d4" and first["mine"] is True
    assert third["kind"] == "blunder" and third["loss"] > 40        # white's second move: +20 -> -900 cp
    assert g["plies"][3]["kind"] is None                            # the final position has no move


def test_game_viewer_rejects_bad_and_unknown_ids(client):
    assert client.get("/api/game?id=abc").status_code == 400
    assert client.get("/api/game?id=999").status_code == 404
    assert client.get("/api/game?id=1&username=someone").status_code == 404


def test_static_assets_are_valid_utf8():
    # A patch once wrote a stray Windows-1252 byte into app.js; browsers then show a broken character.
    from pathlib import Path
    import chess_coach.web as web
    for path in Path(web.STATIC).iterdir():
        path.read_bytes().decode("utf-8")


def test_peers_endpoint_answers_even_without_enough_games(client):
    r = client.get("/api/peers")
    assert r.status_code == 200 and r.json()["available"] is False


def test_requests_for_foreign_host_names_are_refused(tmp_path):
    # DNS rebinding: a hostile domain that resolves to 127.0.0.1 must not be able to read the data.
    app = TestClient(create_app(tmp_path / "x.db", default_username="me"))   # default: localhost only
    assert app.get("/api/config", headers={"host": "evil.example"}).status_code == 400
    assert app.get("/api/config", headers={"host": "localhost:8765"}).status_code == 200
    assert app.get("/api/config", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_an_equally_good_alternative_is_good_not_best(tmp_path, client):
    # The engine (fake) rates any answer at +300 for White, better than the stored +20: no loss,
    # but it is not the engine's own move, so the verdict is "good", never "best".
    app = TestClient(create_app(client.db_path, default_username="me",
                                engine_factory=lambda: FakeEngine(cp=300), allowed_hosts=["testserver"]))
    r = app.post("/api/trainer/check", json={"id": "1:2", "uci": "g1f3"}).json()
    assert r["verdict"] == "good" and r["solved"] is True and r["win_chance_lost"] == 0
    assert app.post("/api/trainer/check", json={"id": "1:2", "uci": "d2d4"}).json()["verdict"] == "best"


def test_cache_is_refreshed_when_the_benchmark_file_changes(client, monkeypatch, tmp_path):
    import os
    from chess_coach import peers
    fake = tmp_path / "benchmarks.json"
    fake.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(peers, "BENCHMARKS", fake)
    calls = []
    monkeypatch.setattr(peers, "peer_report", lambda conn, username: calls.append(1) or {"available": False})

    client.get("/api/peers")
    client.get("/api/peers")
    assert len(calls) == 1                                    # second request came from the cache

    os.utime(fake, ns=(10**18, 10**18))                       # the benchmark file was replaced
    client.get("/api/peers")
    assert len(calls) == 2                                    # so the report was computed again
