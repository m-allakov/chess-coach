import chess

from chess_coach import reports
from chess_coach.classify import classify, mover_win_pct, win_pct
from chess_coach.db import connect
from chess_coach.moves import opening_names, outcome, parse_clocks, parse_time_control, time_zone


def test_win_pct_is_symmetric_and_bounded():
    assert win_pct(0) == 50
    assert abs(win_pct(300) + win_pct(-300) - 100) < 1e-9
    assert 95 < win_pct(1000) < 100
    assert mover_win_pct(300, "black") < 50


def test_same_cp_swing_matters_less_when_already_winning():
    equal = win_pct(0) - win_pct(-200)
    winning = win_pct(900) - win_pct(700)
    assert classify(equal) == "blunder"
    assert classify(winning) == "ok"


def test_opening_names_strip_move_lists():
    pgn = '[ECOUrl "https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation-6.Bg5-e6"]'
    assert opening_names(pgn) == ("Sicilian Defense", "Sicilian Defense Najdorf Variation")
    assert opening_names("[Event \"x\"]") == ("Unknown", "Unknown")


def test_outcome_mapping():
    assert [outcome(r) for r in ("win", "agreed", "resigned", "timeout")] == ["W", "D", "L", "L"]


def _seed(conn):
    """One analysed game: white (the user) blunders a queen on move 2."""
    conn.execute(
        """INSERT INTO games VALUES ('g1','me',1,'blitz','300','chess',1,'white',800,800,'resigned',
        '[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening"]')"""
    )
    fen = chess.Board().fen()
    cps = [0, 20, 20, -900]   # white-POV eval before ply 0..3
    turns = ["white", "black", "white", "black"]
    for ply, (cp, turn) in enumerate(zip(cps, turns)):
        conn.execute(
            "INSERT INTO positions VALUES ('g1',?,?,?,?,?,?,?,?,?)",
            (ply, fen, turn, "opening", "e4" if ply == 0 else "Qh5", "e2e4", cp, "e2e4", 16),
        )
    conn.execute("INSERT INTO game_analysis VALUES ('g1',12,16,2,1.0,1)")
    conn.commit()


def test_weakness_and_worst_moments_find_the_blunder():
    conn = connect(":memory:")
    _seed(conn)
    w = reports.weakness_report(conn, "me")
    assert w["games"] == 1 and w["record"]["L"] == 1
    assert w["overall"]["blunders_per_100_moves"] == 50.0   # 1 of the user's 2 moves
    worst = reports.worst_moments(conn, "me", n=3)
    assert worst["shown"] == 1
    assert worst["blunders"][0]["move_number"] == 2


def test_parse_time_control_and_zones():
    assert parse_time_control("180") == (180.0, 0.0)
    assert parse_time_control("60+1") == (60.0, 1.0)
    assert parse_time_control("1/86400") is None
    assert time_zone(100, 180) == "normal"
    assert time_zone(30, 180) == "low"       # 17% left
    assert time_zone(10, 180) == "critical"  # 5.6% left


def test_parse_clocks_reads_every_ply():
    pgn = "1. e4 {[%clk 0:03:00]} 1... e5 {[%clk 0:02:59.5]} 2. Nf3 {[%clk 0:02:58.2]}"
    assert parse_clocks(pgn) == [180.0, 179.5, 178.2]


def _seed_timed(conn):
    """3+0 blitz game, white = user, 4 plies, black to move last; evals make move 2 a blunder."""
    pgn = "1. e4 {[%clk 0:02:59]} 1... e5 {[%clk 0:02:58]} 2. Qh5 {[%clk 0:00:20]} 2... Nc6 {[%clk 0:02:50]}"
    conn.execute("INSERT INTO games VALUES ('t1','me',1,'blitz','180','chess',1,'white',800,800,'timeout',?)",
                 (pgn,))
    fen = chess.Board().fen()
    for ply, (cp, turn) in enumerate(zip([0, 0, 0, -900, -900], ["white", "black", "white", "black", "white"])):
        conn.execute("INSERT INTO positions VALUES ('t1',?,?,?,?,?,?,?,?,?)",
                     (ply, fen, turn, "opening", "x", "e2e4", cp, "e2e4", 16))
    conn.execute("INSERT INTO game_analysis VALUES ('t1',12,16,0,1.0,1)")
    conn.commit()


def test_time_pressure_report_counts_blunders_and_timeouts():
    conn = connect(":memory:")
    _seed_timed(conn)
    r = reports.time_pressure_report(conn, "me")
    # White still had 179s of 180s before the blunder, so it counts as made with plenty of time.
    assert r["games"] == 1
    assert r["blunders"]["total"] == 1
    assert r["blunders"]["with_plenty_of_time"] == 1
    assert r["lost_on_time"]["games"] == 1


def test_reports_handle_no_analysed_games():
    conn = connect(":memory:")
    assert reports.weakness_report(conn, "me")["games"] == 0
    assert reports.opening_report(conn, "me")["openings"] == []
