from chess_coach import threats


def test_a_piece_the_opponent_can_win_is_reported_hanging():
    # White has just moved; Black's bishop on b2 can take the undefended rook on a1.
    out = threats.lens("4k3/8/8/8/8/8/1b6/R3K3 b - - 0 1")
    assert out["verdict"] == "material"
    assert out["hanging"] == [{"square": "a1", "piece": "R", "gain": 5}]


def test_a_defended_piece_is_worth_less_to_take():
    # The king on b1 guards the rook on a1: bishop takes rook, king takes bishop wins Black only 5 - 3 = 2.
    out = threats.lens("4k3/8/8/8/8/8/1b6/RK6 b - - 0 1")
    assert out["hanging"] == [{"square": "a1", "piece": "R", "gain": 2}]


def test_mate_in_one_is_found_and_outranks_everything():
    # Back-rank mate: Black's rook on a8 plays ...Ra1# against the king on g1 hemmed in by its own pawns.
    out = threats.lens("r5k1/8/8/8/8/8/5PPP/6K1 b - - 0 1")
    assert out["mate_in_one"] == ["Ra1#"]
    assert out["verdict"] == "mate"


def test_a_quiet_position_is_clear():
    out = threats.lens("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
    assert out["verdict"] == "clear" and out["hanging"] == [] and out["checks"] == []


def test_checks_are_listed_when_nothing_hangs():
    out = threats.lens("4k3/8/8/8/8/8/8/R3K3 b - - 0 1")           # Black to move, no checks (only a king)
    assert out["verdict"] == "clear"
    out2 = threats.lens("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")          # White to move can check with the rook
    assert "Ra8+" in out2["checks"] and out2["verdict"] in ("check", "material")
