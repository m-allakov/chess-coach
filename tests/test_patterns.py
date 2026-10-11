from datetime import datetime

from chess_coach import patterns
from chess_coach.stats import compare_rates

MIN = 60


def _g(t, outcome="W", moves=40, blunders=0, **over):
    base = {"end_time": t, "outcome": outcome, "moves": moves, "blunders": blunders,
            "result_code": "resigned", "min_wp": 50.0, "min_wp_early": 50.0}
    return {**base, **over}


def test_compare_means_uses_game_to_game_spread():
    from chess_coach.stats import compare_means
    steady_low = [4.0, 5.0, 6.0] * 10
    steady_high = [9.0, 10.0, 11.0] * 10
    assert compare_means(steady_high, steady_low)["clear"] is True
    noisy_a = [0.0, 20.0] * 15          # a difference of 1 hidden inside a huge spread
    noisy_b = [1.0, 19.0] * 15
    assert compare_means(noisy_a, noisy_b)["clear"] is False
    assert compare_means([9.0] * 3, [1.0] * 3)["clear"] is False      # too few games


def test_compare_rates_calls_only_large_well_sampled_differences_clear():
    assert compare_rates(75, 200, 50, 200)["clear"] is True
    assert compare_rates(55, 100, 50, 100)["clear"] is False        # inside the noise
    assert compare_rates(100, 3, 0, 3)["clear"] is False            # tiny samples never count
    assert compare_rates(50, 500, 50, 500)["clear"] is False        # no difference


def test_split_sessions_breaks_on_long_pauses():
    games = [_g(0), _g(10 * MIN), _g(45 * MIN), _g(46 * MIN), _g(200 * MIN)]
    sizes = [len(s) for s in patterns.split_sessions(games, gap_minutes=30)]
    assert sizes == [2, 2, 1]


def test_loss_streaks_reset_after_a_win_and_between_sessions():
    # one session: L L W L, then (after a long break) a new session starting with L
    games = [_g(0, "L"), _g(5 * MIN, "L"), _g(10 * MIN, "W"), _g(15 * MIN, "L"),
             _g(500 * MIN, "L")]
    r = patterns.tilt_from_games(games)
    by = r["after_consecutive_losses"]
    # game 1: 0 prior losses; game 2: after 1; game 3: after 2; game 4: streak reset by the win;
    # game 5: new session
    assert (by["0"]["games"], by["1"]["games"], by["2"]["games"], by["3+"]["games"]) == (3, 1, 1, 0)
    assert r["sessions"] == 2


def test_tilt_is_reported_only_when_it_is_clearly_bigger_than_noise():
    # 200 games after a loss lose far more than 200 games not after a loss.
    games, t = [], 0
    for i in range(100):                                   # sessions of: W, L, L, L, then a pause
        noise = i % 3                                      # games differ a little from each other
        games += [_g(t, "W", blunders=2 + noise), _g(t + 5 * MIN, "L", blunders=2 + noise),
                  _g(t + 10 * MIN, "L", blunders=2 + noise), _g(t + 15 * MIN, "L", blunders=9 + noise)]
        t += 500 * MIN
    r = patterns.tilt_from_games(games)
    after2 = r["after_consecutive_losses"]["2"]["vs_games_not_after_a_loss"]
    assert after2["blunders_per_100_moves"]["verdict"] == "worse"
    assert r["clear_signs_of_tilt"] != "none that rise above sampling noise"


def test_small_samples_report_no_tilt():
    games = [_g(0, "L"), _g(5 * MIN, "L", blunders=9), _g(10 * MIN, "L", blunders=9),
             _g(15 * MIN, "L", blunders=9)]
    assert patterns.tilt_from_games(games)["clear_signs_of_tilt"] == "none that rise above sampling noise"


def test_time_of_day_buckets_use_the_local_clock():
    morning = datetime(2026, 1, 5, 9, 30).timestamp()      # a Monday
    saturday_night = datetime(2026, 1, 10, 23, 0).timestamp()
    r = patterns.time_of_day_from_games([_g(morning), _g(saturday_night, "L")])
    assert r["by_time_of_day"]["morning 06-12"]["games"] == 1
    assert r["by_time_of_day"]["evening 18-24"]["games"] == 1
    assert r["weekday_vs_weekend"]["weekday"]["games"] == 1
    assert r["weekday_vs_weekend"]["weekend"]["games"] == 1


def test_resilience_counts_saved_games_among_clearly_worse_ones():
    games = [_g(0, "W", min_wp_early=10.0), _g(1, "L", min_wp_early=5.0), _g(2, "D", min_wp_early=19.9),
             _g(3, "W", min_wp_early=40.0), _g(4, "L", min_wp_early=20.1)]
    r = patterns.resilience_from_games(games)
    assert r["games_clearly_worse_with_play_left"] == 3
    assert (r["won"], r["drawn"], r["lost"]) == (1, 1, 1)
    assert r["saved_pct"] == 66.7


def test_a_game_that_only_ended_badly_is_not_counted_as_recoverable():
    # Dropped to 3% only in the closing moves: min_wp is low but min_wp_early is fine.
    r = patterns.resilience_from_games([_g(0, "L", min_wp=3.0, min_wp_early=55.0)])
    assert r["games_clearly_worse_with_play_left"] == 0


def test_reports_handle_no_games():
    from chess_coach.db import connect
    conn = connect(":memory:")
    assert patterns.tilt_report(conn, "x")["games"] == 0
    assert patterns.time_of_day_report(conn, "x")["games"] == 0
    assert patterns.resilience_report(conn, "x")["games"] == 0
