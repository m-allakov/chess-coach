import chess
import pytest

from chess_coach import opportunities
from chess_coach.db import connect

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.mark.parametrize("fen, uci, expected", [
    # Ne5 can be taken by the d6 pawn and nothing recaptures: a whole knight is offered.
    ("rnbqkbnr/ppp1pppp/3p4/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1", "f3e5", 3),
    # Same move, but the d4 pawn recaptures: knight (3) for pawn (1) leaves 2 net.
    ("rnbqkbnr/ppp1pppp/3p4/8/3P4/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1", "f3e5", 2),
    # Nxe5 takes a knight, then dxe5 takes ours: an even trade is not a sacrifice.
    ("rnbqkb1r/ppp1pppp/3p4/4n3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1", "f3e5", 0),
    # Pawn moves and quiet developing moves never count.
    (START, "e2e4", 0),
    (START, "g1f3", 0),
])
def test_sacrificed_material(fen, uci, expected):
    assert opportunities.sacrificed_material(fen, uci) == expected


def _seed_opponent_blunder(conn, reply_cp):
    """User is white. Black blunders on ply 1 (eval 0 -> +600); `reply_cp` is the eval
    after white's reply on ply 2."""
    conn.execute(
        """INSERT INTO games VALUES ('o1','me',1,'blitz','180','chess',1,'white',800,800,'win',
        '[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening"]')"""
    )
    fen = chess.Board().fen()
    for ply, turn, cp in [(0, "white", 0), (1, "black", 0), (2, "white", 600), (3, "black", reply_cp)]:
        conn.execute("INSERT INTO positions VALUES ('o1',?,?,?,?,?,?,?,?,?)",
                     (ply, fen, turn, "middlegame", "x", "e2e4", cp, "e2e4", 16))
    conn.execute("INSERT INTO positions VALUES ('o1',4,?,?,?,?,?,?,?,?)",
                 (fen, "white", "middlegame", None, None, reply_cp, None, 16))
    conn.execute("INSERT INTO game_analysis VALUES ('o1',12,16,0,1.0,1)")
    conn.commit()


def test_missed_opportunity_when_the_reply_throws_the_win_away():
    conn = connect(":memory:")
    _seed_opponent_blunder(conn, reply_cp=-50)
    r = opportunities.missed_opportunities(conn, "me")
    assert r["opponent_blunders_you_could_use"] == 1
    assert r["missed"] == 1 and r["punished"] == 0
    assert len(r["worst_missed"]) == 1


def test_punished_when_the_reply_keeps_the_advantage():
    conn = connect(":memory:")
    _seed_opponent_blunder(conn, reply_cp=600)
    r = opportunities.missed_opportunities(conn, "me")
    assert r["punished"] == 1 and r["punish_rate"] == 1.0
    assert r["worst_missed"] == []


def test_no_games_gives_empty_reports():
    conn = connect(":memory:")
    assert opportunities.missed_opportunities(conn, "me")["opponent_blunders_you_could_use"] == 0
    assert opportunities.sacrifice_candidates(conn, "me")["candidates"] == 0
