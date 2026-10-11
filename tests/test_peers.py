from chess_coach import peers


def _band(players, **metrics):
    return {"players": players, **{k: v for k, v in metrics.items()}}


def _stats(n, p25, median, p75):
    return {"n": n, "p25": p25, "median": median, "p75": p75}


BANDS = {
    "700-900": _band(20, blunders_per_100_moves=_stats(20, 8.0, 9.0, 10.0), score_pct=_stats(20, 45, 50, 55)),
    "1500-1700": _band(5, blunders_per_100_moves=_stats(5, 6.0, 7.0, 8.0), score_pct=_stats(5, 44, 50, 56)),
}


def test_closest_band_uses_the_midpoint_of_each_band():
    assert peers.closest_band(793, BANDS) == "700-900"
    assert peers.closest_band(1450, BANDS) == "1500-1700"
    assert peers.closest_band(900, {}) is None


def test_position_against_the_middle_half():
    s = _stats(20, 8.0, 9.0, 10.0)
    assert peers.position(7.0, s) == "below"
    assert peers.position(9.5, s) == "within"
    assert peers.position(12.0, s) == "above"


def test_a_band_with_too_few_players_gives_no_position():
    assert peers.position(5.0, _stats(3, 8.0, 9.0, 10.0)) == "unknown"
    assert peers.position(5.0, {}) == "unknown"


def test_compare_flags_the_closest_band_and_tentative_bands():
    user = {"games": 120, "blunders_per_100_moves": 11.0, "score_pct": 50.0}
    r = peers.compare(user, 793, BANDS)
    assert r["available"] and r["closest_band"] == "700-900"
    assert r["tentative_bands"] == ["1500-1700"]               # only 5 players behind it
    by_key = {m["metric"]: m for m in r["metrics"]}
    assert by_key["blunders_per_100_moves"]["against_closest_band"] == "above"
    assert by_key["score_pct"]["against_closest_band"] == "within"
    assert by_key["blunders_per_100_moves"]["lower_is_better"] is True
    assert "middlegame_blunders_per_100" not in by_key          # metrics the player lacks are skipped


def test_compare_without_data_says_so():
    assert peers.compare(None, 800, BANDS)["available"] is False
    assert peers.compare({"games": 50}, 800, {})["available"] is False


def test_missing_benchmark_file_is_not_an_error(tmp_path):
    assert peers.load_benchmarks(tmp_path / "nope.json") == {}
    broken = tmp_path / "bad.json"
    broken.write_text("{not json", encoding="utf-8")
    assert peers.load_benchmarks(broken) == {}


def test_shipped_benchmarks_hold_only_band_summaries():
    data = peers.load_benchmarks()
    assert data, "benchmarks.json should ship with the package"
    for band, summary in data.items():
        assert "-" in band and "players" in summary
        assert "username" not in str(summary).lower()
