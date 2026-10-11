import chess

from chess_coach import evidence
from chess_coach.db import connect

# Black's bishop on b2 can capture White's rook on a1.
HANGS_A_ROOK = "4k3/8/8/8/8/8/1b6/R3K3 b - - 0 1"
START = chess.Board().fen()


def seed(conn, uci_reply="b2a1", result="resigned", wp_cps=(600, 100, -400)):
    """White (the user) stands better, blunders on ply 0, and the opponent replies on ply 1."""
    conn.execute("INSERT INTO games VALUES ('https://www.chess.com/game/live/1','me',1,'blitz','180','chess',1,'white',"
                 "800,800,?, '[ECOUrl \"https://www.chess.com/openings/Kings-Pawn-Opening\"]')", (result,))
    rows = [(0, START, "white", "e2e4", "d2d4", wp_cps[0]),
            (1, HANGS_A_ROOK, "black", uci_reply, uci_reply, wp_cps[1]),
            (2, START, "white", None, None, wp_cps[2])]
    for ply, fen, turn, uci, best, cp in rows:
        conn.execute("INSERT INTO positions VALUES ('https://www.chess.com/game/live/1',?,?,?,?,?,?,?,?,?)",
                     (ply, fen, turn, "middlegame", "x", uci, cp, best, 16))
    conn.execute("INSERT INTO game_analysis VALUES ('https://www.chess.com/game/live/1',12,16,0,1.0,1)")
    conn.commit()


def test_a_blunder_after_a_won_position_is_profiled_with_the_opponents_reply():
    conn = connect(":memory:")
    seed(conn)
    p = evidence.decisive_blunders(conn, "me")
    assert p["games"] == 1
    assert p["opponent_next_move"] == {"won_piece": 1}      # the bishop took the rook
    assert p["material_or_mate_share"] == 1.0
    assert p["opponent_found_best_reply_share"] == 1.0
    assert p["phase"] == {"middlegame": 1}


def test_a_won_game_is_not_a_thrown_win():
    conn = connect(":memory:")
    seed(conn, result="win")
    assert evidence.decisive_blunders(conn, "me") == {"games": 0}


def test_a_game_that_was_never_winning_is_ignored():
    conn = connect(":memory:")
    seed(conn, wp_cps=(0, -300, -500))
    assert evidence.decisive_blunders(conn, "me") == {"games": 0}


def test_reply_kinds():
    assert evidence._reply_kind(HANGS_A_ROOK, "b2a1") == "won_piece"
    assert evidence._reply_kind(START, "e2e4") == "quiet"
    assert evidence._reply_kind(START, None) == "quiet"
    pawn_grab = "4k3/8/8/3p4/4P3/8/8/4K3 b - - 0 1"
    assert evidence._reply_kind(pawn_grab, "d5e4") == "won_pawn"
    back_rank = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"
    assert evidence._reply_kind(back_rank, "a1a8") == "mated"
    check = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    assert evidence._reply_kind(check, "a1a8") == "gave_check"
