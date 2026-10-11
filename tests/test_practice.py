from chess_coach import practice
from chess_coach.db import connect

NOW = 1_700_000_000_000
DAY = practice.MS_DAY


def test_a_correct_answer_moves_the_position_up_a_box_and_pushes_it_away():
    conn = connect(":memory:")
    assert practice.record(conn, "Me", "1:2", True, NOW) == {"box": 1, "due_ms": NOW + 1 * DAY}
    assert practice.record(conn, "me", "1:2", True, NOW) == {"box": 2, "due_ms": NOW + 3 * DAY}   # name is case-blind


def test_the_top_box_is_the_ceiling():
    conn = connect(":memory:")
    for _ in range(9):
        out = practice.record(conn, "me", "1:2", True, NOW)
    assert out["box"] == len(practice.INTERVALS_DAYS) - 1 and out["due_ms"] == NOW + 21 * DAY


def test_a_miss_sends_the_position_back_and_it_returns_within_minutes():
    conn = connect(":memory:")
    practice.record(conn, "me", "1:2", True, NOW)
    practice.record(conn, "me", "1:2", True, NOW)
    out = practice.record(conn, "me", "1:2", False, NOW)
    assert out == {"box": 0, "due_ms": NOW + practice.RETRY_MINUTES * practice.MS_MINUTE}


def test_resting_lists_only_positions_that_are_not_due_yet():
    conn = connect(":memory:")
    practice.record(conn, "me", "1:2", True, NOW)           # due in a day
    practice.record(conn, "me", "1:4", False, NOW)          # due in ten minutes
    assert practice.resting(conn, "me", NOW) == {"1:2", "1:4"}
    assert practice.resting(conn, "me", NOW + 20 * practice.MS_MINUTE) == {"1:2"}
    assert practice.resting(conn, "me", NOW + 2 * DAY) == set()
    assert practice.resting(conn, "someone", NOW) == set()


def test_summary_counts_answers_and_mastered_positions():
    conn = connect(":memory:")
    for _ in range(3):
        practice.record(conn, "me", "1:2", True, NOW)       # box 3
    practice.record(conn, "me", "1:4", False, NOW)
    assert practice.summary(conn, "me") == {"positions": 2, "answers": 4, "solved": 3, "mastered": 1}
    assert practice.summary(conn, "nobody")["positions"] == 0


def test_importing_browser_progress_never_overwrites_server_progress():
    conn = connect(":memory:")
    practice.record(conn, "me", "1:2", True, NOW)
    added = practice.import_progress(conn, "me", {
        "1:2": {"box": 4, "due": 5},                        # already known: must not change
        "3:6": {"box": 2, "due": NOW + DAY},
        "junk": {"box": 1, "due": 1},                       # not a puzzle id
        "5:8": "not an object",
        "7:8": {"box": "x", "due": 1},
    })
    assert added == 1
    assert practice.resting(conn, "me", NOW) == {"1:2", "3:6"}
    row = conn.execute("SELECT box FROM practice WHERE puzzle_id = '1:2'").fetchone()
    assert row["box"] == 1
