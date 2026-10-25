from chess_coach.chesscom import game_row
from chess_coach.db import connect, insert_games
from chess_coach.sync import sync_games

PGN = '[Event "Live Chess"]\n\n1. e4 e5 1-0'


def _game(white="sample_player", black="rival", rules="chess", url="https://www.chess.com/game/live/1"):
    return {
        "url": url,
        "pgn": PGN,
        "end_time": 1700000000,
        "time_class": "blitz",
        "time_control": "300",
        "rules": rules,
        "rated": True,
        "white": {"username": white, "rating": 800, "result": "win"},
        "black": {"username": black, "rating": 790, "result": "resigned"},
    }


class FakeClient:
    def archives(self, username):
        return ["m1", "m2"]

    def month(self, url):
        return [_game(url=f"https://x/{url}/a"), _game(rules="chess960", url=f"https://x/{url}/b")]


def test_game_row_uses_players_perspective():
    row = game_row(_game(white="rival", black="Sample_Player"), "sample_player")
    assert row["color"] == "black"
    assert row["my_rating"] == 790


def test_game_row_skips_games_without_the_player():
    assert game_row(_game(white="a", black="b"), "sample_player") is None


def test_sync_stores_only_standard_chess_and_is_idempotent():
    conn = connect(":memory:")
    first = sync_games(conn, FakeClient(), "sample_player", months=2)
    assert first["games_new"] == 2
    second = sync_games(conn, FakeClient(), "sample_player", months=2)
    assert second["games_new"] == 0


def test_insert_games_counts_only_new_rows():
    conn = connect(":memory:")
    row = game_row(_game(), "sample_player")
    assert insert_games(conn, "sample_player", [row]) == 1
    assert insert_games(conn, "sample_player", [row]) == 0
