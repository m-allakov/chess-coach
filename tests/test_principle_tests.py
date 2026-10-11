import chess

from chess_coach import principle_tests as pt
from chess_coach.db import connect


def seed_game(conn, n, castled_at, blunder_plies):
    """A 24-move game for the player (white). White's moves are e2e4 repeated in the record, which is
    enough for the tests: they look only at SAN text, move numbers and evaluations."""
    url = f"https://www.chess.com/game/live/{n}"
    conn.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (url, "me", n, "blitz", "180", "chess", 1, "white", 800, 800, "win", ""))
    fen = chess.Board().fen()
    cp = 0
    for ply in range(48):
        san = "O-O" if castled_at and ply == 2 * (castled_at - 1) else "e4"
        cp_next = cp - 600 if ply in blunder_plies else cp
        conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (url, ply, fen, "white" if ply % 2 == 0 else "black", "middlegame", san, "e2e4", cp, "d2d4", 16))
        cp = cp_next
    conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?)", (url, 48, fen, "white", "endgame", None, None, cp, None, 16))
    conn.execute("INSERT INTO game_analysis VALUES (?,12,16,0,1.0,1)", (url,))


def test_no_games_means_inconclusive_not_an_error():
    conn = connect(":memory:")
    result = pt.check(conn, "me", "am-024")
    assert result["verdict"] == "inconclusive" and "Too few" in result["summary"]
    assert pt.check(conn, "me", "no-such-principle") is None


def test_late_castlers_who_blunder_more_support_the_castling_principle():
    conn = connect(":memory:")
    for n in range(12):
        seed_game(conn, n, castled_at=6, blunder_plies=set())                          # castled early, no losses
    for n in range(100, 112):
        seed_game(conn, n, castled_at=None, blunder_plies={16, 18, 20, 22, 24, 26})    # never castled, many losses
    result = pt.check(conn, "me", "am-024")
    assert result["verdict"] == "supported"
    assert result["groups"]["castled_by_move_10"]["games"] == 12
    assert result["groups"]["not_castled_by_move_12"]["games"] == 12
    assert result["difference"]["change"] > 0


def test_report_attaches_the_advice_source_and_a_caveat():
    conn = connect(":memory:")
    rows = pt.report(conn, "me")
    assert {r["principle_id"] for r in rows} == set(pt.TESTS)
    for r in rows:
        assert r["advice"] and r["source"]["author"] and r["caveat"] and r["summary"]


def seed_swings(conn, n, careless):
    """Equal for 10 plies (no losses), then clearly winning (cp +800) for 20 plies. With `careless`, White
    drops 700 centipawns on every second winning move; otherwise it stays put."""
    url = f"https://www.chess.com/game/live/{n}"
    conn.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (url, "me", n, "blitz", "180", "chess", 1, "white", 800, 800, "win", ""))
    fen = chess.Board().fen()
    for ply in range(31):
        cp = 0 if ply < 10 else 800
        if careless and ply >= 10 and ply % 4 == 2:
            cp = 800                                   # the position before the careless move
        elif careless and ply >= 10 and ply % 4 == 3:
            cp = 100                                   # ...and after it
        conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (url, ply, fen, "white" if ply % 2 == 0 else "black", "middlegame", "e4", "e2e4", cp, "d2d4", 16))
    conn.execute("INSERT INTO game_analysis VALUES (?,12,16,0,1.0,1)", (url,))


def test_games_that_slip_more_when_winning_support_the_vigilance_principle():
    conn = connect(":memory:")
    for n in range(14):
        seed_swings(conn, n, careless=True)
    result = pt.check(conn, "me", "am-010")
    assert result["verdict"] == "supported" and result["difference"]["change"] > 0
    steady = connect(":memory:")
    for n in range(14):
        seed_swings(steady, n, careless=False)
    assert pt.check(steady, "me", "am-010")["verdict"] == "inconclusive"
