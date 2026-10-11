import chess
import chess.engine
import pytest

from chess_coach import trainer
from chess_coach.db import connect

START = chess.Board().fen()
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


class FakeEngine:
    def analyse(self, board, limit):
        return {"score": chess.engine.PovScore(chess.engine.Cp(-50), chess.WHITE), "pv": [next(iter(board.legal_moves))]}


def seed(conn):
    """White (the user): the opponent blunders on ply 1 (eval 0 -> +800), then White's reply on ply 2 gives most of it back."""
    conn.execute("INSERT INTO games VALUES ('https://www.chess.com/game/live/1','me',1,'blitz','180','chess',1,'white',"
                 "800,800,'resigned','')")
    rows = [(0, START, "white", "e2e4", "e2e4", 0), (1, AFTER_E4, "black", "e7e5", "e7e5", 0),
            (2, START, "white", "e2e4", "d2d4", 800), (3, START, "black", None, None, 100)]
    for ply, fen, turn, uci, best, cp in rows:
        conn.execute("INSERT INTO positions VALUES ('https://www.chess.com/game/live/1',?,?,?,?,?,?,?,?,?)",
                     (ply, fen, turn, "middlegame", "x", uci, cp, best, 16))
    conn.execute("INSERT INTO game_analysis VALUES ('https://www.chess.com/game/live/1',12,16,0,1.0,1)")
    conn.commit()


def test_erred_puzzles_show_the_position_after_the_opponents_mistake():
    conn = connect(":memory:")
    seed(conn)
    data = trainer.erred_puzzles(conn, "me")
    assert data["available"] == 1 and data["mode"] == "erred"
    puzzle = data["puzzles"][0]
    assert puzzle["id"] == "1:2" and puzzle["kind"] == "erred" and puzzle["fen"] == START
    assert puzzle["prev_uci"] == "e7e5" and puzzle["prev_san"] == "x"
    assert "best_uci" not in puzzle


def test_erred_puzzles_respect_exclusions():
    conn = connect(":memory:")
    seed(conn)
    assert trainer.erred_puzzles(conn, "me", exclude={"1:2"})["puzzles"] == []


def test_a_game_where_the_reply_was_fine_is_not_a_puzzle():
    conn = connect(":memory:")
    seed(conn)
    conn.execute("UPDATE positions SET cp = 800 WHERE ply = 3")        # the reply kept the full advantage
    conn.commit()
    assert trainer.erred_puzzles(conn, "me")["available"] == 0


def test_checking_an_answer_includes_the_threat_lens():
    conn = connect(":memory:")
    seed(conn)
    quiet = trainer.check(conn, FakeEngine(), "me", "1:2", "g1f3")
    assert quiet["after_your_move"]["verdict"] == "clear"
    best = trainer.check(conn, FakeEngine(), "me", "1:2", "d2d4")
    assert best["verdict"] == "best" and "after_your_move" in best


def test_the_lens_flags_a_move_that_hangs_a_piece():
    conn = connect(":memory:")
    seed(conn)
    # White's rook on a1 is attacked by Black's bishop on b2. A king step that does not guard it leaves it hanging.
    conn.execute("UPDATE positions SET fen = ?, best_uci = ? WHERE ply = 2",
                 ("r3k3/8/8/8/8/8/1b6/R3K3 w - - 0 1", "a1a8"))
    conn.commit()
    hang = trainer.check(conn, FakeEngine(), "me", "1:2", "e1d1")
    assert hang["after_your_move"]["verdict"] == "material"
    assert hang["after_your_move"]["hanging"][0] == {"square": "a1", "piece": "R", "gain": 5}
