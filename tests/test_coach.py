import pytest

from chess_coach import coach
from chess_coach.coach import _trend, _window_metrics, classify_non_win, game_summaries
from chess_coach.db import connect


def _game(**over):
    base = {"url": "g", "outcome": "L", "result_code": "resigned", "opening_family": "x",
            "time_class": "blitz", "moves": 30, "peak_wp": 50.0, "opening_exit_wp": 50.0,
            "biggest_loss": 5.0, "blunders": 0, "final_wp": 50.0, "min_wp": 50.0, "min_wp_early": 50.0, "end_time": 0}
    return {**base, **over}


@pytest.mark.parametrize("over, cause", [
    ({"result_code": "timeout", "peak_wp": 95.0}, "time"),              # clock beats everything
    ({"result_code": "timeout", "final_wp": 10.0}, "gradual"),          # already lost: not the clock's fault
    ({"result_code": "timeout", "final_wp": 10.0, "peak_wp": 80.0}, "thrown_win"),
    ({"peak_wp": 80.0, "biggest_loss": 40.0}, "thrown_win"),            # had it, then lost it
    ({"opening_exit_wp": 30.0, "biggest_loss": 40.0}, "opening"),       # worse out of the book
    ({"biggest_loss": 25.0}, "blunder"),
    ({}, "gradual"),
    ({"opening_exit_wp": None}, "gradual"),                             # game with no opening moves
])
def test_primary_cause_follows_the_priority_order(over, cause):
    assert classify_non_win(_game(**over)) == cause


def _move(url, ply, phase="middlegame", loss=0.0, before=50.0, after=50.0, kind="ok", outcome="L"):
    return {"url": url, "ply": ply, "phase": phase, "wp_loss": loss, "wp_before": before,
            "wp_after": after, "kind": kind, "outcome": outcome, "result_code": "resigned", "end_time": 0,
            "opening_family": "Test", "time_class": "blitz"}


def test_game_summaries_collects_peak_opening_exit_and_biggest_loss():
    moves = [
        _move("a", 0, "opening", after=60.0),
        _move("a", 2, "opening", before=60.0, after=45.0),
        _move("a", 4, loss=30.0, before=85.0, after=55.0, kind="blunder"),
        _move("b", 0),
    ]
    a, b = game_summaries(moves)
    assert a["url"] == "a" and b["url"] == "b"            # order of first appearance is kept
    assert a["opening_exit_wp"] == 45.0
    assert a["peak_wp"] == 85.0
    assert a["biggest_loss"] == 30.0 and a["blunders"] == 1
    assert b["opening_exit_wp"] is None


def _many(n, **over):
    return [_game(**over) for _ in range(n)]


def test_trend_reports_a_large_change_on_big_samples():
    recent = _many(150, outcome="W") + _many(50, outcome="L")      # 75%
    earlier = _many(100, outcome="W") + _many(100, outcome="L")    # 50%
    assert _trend(recent, earlier)["score_pct"]["verdict"] == "better"


def test_trend_ignores_a_change_that_noise_could_explain():
    # 55% vs 50% over 100 games each is well inside sampling noise.
    recent = _many(55, outcome="W") + _many(45, outcome="L")
    earlier = _many(50, outcome="W") + _many(50, outcome="L")
    t = _trend(recent, earlier)["score_pct"]
    assert t["change"] == 5.0 and t["verdict"] == "no clear change"


def test_trend_direction_is_inverted_for_lower_is_better_metrics():
    # Per-game rates vary (12 or 24 per 100 moves) around a clearly higher level than 4 or 8.
    recent = _many(100, blunders=6, moves=50) + _many(100, blunders=12, moves=50)
    earlier = _many(100, blunders=2, moves=50) + _many(100, blunders=4, moves=50)
    assert _trend(recent, earlier)["blunders_per_100_moves"]["verdict"] == "worse"


def test_identical_windows_show_no_clear_change():
    a = _many(50, outcome="W", blunders=1, moves=100)
    t = _trend(a, list(a))
    assert all(v["verdict"] == "no clear change" for v in t.values())


def test_coach_plan_without_games_says_so():
    assert coach.coach_plan(connect(":memory:"), "nobody")["games"] == 0
