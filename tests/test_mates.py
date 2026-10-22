import pytest
from test_gameview import RUY, _add, _pgn, _seed_analysis

from chess_coach import gameview, opportunities, reports
from chess_coach.classify import classify, pct
from chess_coach.db import connect
from chess_coach.mates import MISSED_MATE_MAX, for_mover, is_known, mate_fields

KNOWN = dict(known_before=True, known_after=True)


def test_pct_rounds_halves_up_where_round_goes_to_even():
    assert [round(x, 1) for x in (2.25, 0.35, 71.25)] == [2.2, 0.3, 71.2]    # what we used to print
    assert [pct(x) for x in (2.25, 0.35, 71.25)] == [2.3, 0.4, 71.3]
    assert pct(97.45) == 97.5 and pct(97.4499) == 97.4
    assert pct(98.5, 0) == 99.0 and round(98.5) == 98
    assert pct(0.125, 2) == 0.13 and pct(12.0) == 12.0 and isinstance(pct(3), float)


def test_rounding_never_changes_a_classification():
    loss = 14.96                      # prints as 15.0, which is the blunder threshold
    assert pct(loss) == 15.0 and classify(loss) == "mistake" and classify(15.0) == "blunder"


def test_mate_count_is_the_mating_sides_moves_signed_from_whites_side():
    assert for_mover(3, "white") == 3 and for_mover(-3, "white") is None
    assert for_mover(-1, "black") == 1 and for_mover(1, "black") is None
    assert for_mover(0, "white") is None and for_mover(None, "black") is None


def test_missed_mate_for_both_colours():
    assert mate_fields("white", 3, None, **KNOWN) == {"mate_before_mover": 3, "missed_mate": 3, "mate_lengthened": None}
    assert mate_fields("black", -1, None, **KNOWN)["missed_mate"] == 1
    # the move threw the mate away and handed the opponent one
    assert mate_fields("black", -2, 4, **KNOWN)["missed_mate"] == 2
    assert mate_fields("white", None, None, **KNOWN) == {"mate_before_mover": None, "missed_mate": None,
                                                         "mate_lengthened": None}
    assert mate_fields("white", -3, None, **KNOWN)["mate_before_mover"] is None   # the opponent's mate


def test_mate_lengthened_boundary():
    # N -> N-1 is the normal shortening; N -> N (or more) lost a tempo
    assert mate_fields("white", 3, 2, **KNOWN) == {"mate_before_mover": 3, "missed_mate": None, "mate_lengthened": None}
    assert mate_fields("black", -3, -2, **KNOWN)["mate_lengthened"] is None
    assert mate_fields("white", 3, 3, **KNOWN)["mate_lengthened"] == {"from": 3, "to": 3}
    assert mate_fields("black", -3, -5, **KNOWN)["mate_lengthened"] == {"from": 3, "to": 5}
    assert mate_fields("white", 3, 3, **KNOWN)["missed_mate"] is None


def test_delivering_checkmate_is_neither_missed_nor_lengthened():
    assert mate_fields("black", -1, 0, **KNOWN) == {"mate_before_mover": 1, "missed_mate": None, "mate_lengthened": None}


def test_unknown_data_leaves_the_fields_null():
    assert mate_fields("white", 3, None, known_before=False, known_after=True)["mate_before_mover"] is None
    assert mate_fields("white", 3, None, known_before=True, known_after=False) == {
        "mate_before_mover": 3, "missed_mate": None, "mate_lengthened": None}
    assert not is_known(False, False, 20) and not is_known(False, True, 20)
    assert is_known(True, True, 997) and is_known(True, False, 20) and not is_known(True, False, 950)


# --- get_game ------------------------------------------------------------------------------

QUIET = [20] * 4 + [997] * 7          # White (the user) has a decided position from ply index 4 on


def _game(mate, cps=QUIET, complete=1, url="https://www.chess.com/game/live/111", color="white",
          end_time=100, pgn=None, tc="180+2"):
    conn = connect(":memory:")
    _add(conn, pgn or _pgn(RUY), url=url, color=color, end_time=end_time)
    _seed_analysis(conn, pgn or _pgn(RUY), cps, url=url, mate=mate)
    conn.execute("UPDATE game_detail SET complete = ?", (complete,))
    conn.commit()
    return conn


def _by_ply(conn, **kw):
    return {m["ply"]: m for m in gameview.get_game(conn, "111", **kw)["moves"]}


def test_missed_mate_is_reported_and_the_class_is_not_touched():
    m = _by_ply(_game({4: 3}))[5]                       # 3.Bb5 instead of the mate in 3
    assert (m["mate_before_mover"], m["missed_mate"], m["mate_lengthened"]) == (3, 3, None)
    assert m["eval_before"] == {"cp": None, "mate": 3} and m["class"] in ("best", "good")
    assert _by_ply(_game({4: 3}))[1]["missed_mate"] is None


def test_mate_lengthened_in_get_game():
    kept = _by_ply(_game({4: 3, 5: 3}))[5]            # still mating in 3 after a move: a tempo lost
    assert (kept["missed_mate"], kept["mate_lengthened"]) == (None, {"from": 3, "to": 3})
    normal = _by_ply(_game({4: 3, 5: 2}))[5]
    assert (normal["missed_mate"], normal["mate_lengthened"]) == (None, None)


def test_black_mating_and_delivering_it():
    fools = _pgn("1. f3 e5 2. g4 Qh4# 0-1", tc="180")
    conn = _game({3: -1, 4: 0}, cps=[20, 20, 20, -997, -1000], pgn=fools, color="black")
    last = _by_ply(conn)[4]
    assert last["san"] == "Qh4#" and last["mate_before_mover"] == 1
    assert last["missed_mate"] is None and last["mate_lengthened"] is None


def test_no_mate_data_means_unknown_not_absent():
    conn = _game({4: 3})
    conn.execute("DELETE FROM game_detail")
    conn.commit()
    out = gameview.get_game(conn, "111")
    assert out["meta"]["mate_data"] == "missing" and "unknown" in out["note"]
    assert all(m["mate_before_mover"] is None and m["missed_mate"] is None for m in out["moves"])


def test_backfilled_game_trusts_the_absence_of_a_row_only_below_900_cp():
    # partial: row for ply index 4 (mate 3) exists; index 5 has no row at cp 20 => no mate there.
    cps = [20, 20, 20, 20, 997, 20, 20, 20, 997, 997, 997]
    conn = _game({4: 3}, cps=cps, complete=0)
    conn.execute("DELETE FROM position_detail WHERE ply NOT IN (4)")
    conn.commit()
    moves = _by_ply(conn)
    assert moves[5]["missed_mate"] == 3                     # position after is quiet: known, no mate
    # index 8 is decided (997) but has no row: its mate status is unknown, so nothing is claimed
    assert moves[9]["mate_before_mover"] is None and moves[8]["missed_mate"] is None
    assert gameview.get_game(conn, "111")["meta"]["mate_data"] == "partial"


def test_only_flagged_counts_a_short_missed_mate_but_not_a_long_one():
    assert MISSED_MATE_MAX == 5
    conn = _game({4: 3, 8: 11}, cps=[997] * 11)
    plies = [m["ply"] for m in gameview.get_game(conn, "111", only_flagged=True)["moves"]]
    # ply 5 (mate in 3) is flagged and brings its neighbours; ply 9 (mate in 11) is not flagged at all
    assert plies == [4, 5, 6]
    flagged = [m for m in gameview.get_game(conn, "111", only_flagged=True)["moves"] if not m.get("context")]
    assert [m["ply"] for m in flagged] == [5] and flagged[0]["class"] != "blunder"


def test_win_chance_matches_worst_moments_exactly():
    conn = _game(None, cps=[20, 20, 20, 20, 20, -300, -300, -300, -300, -300, -300])
    move = _by_ply(conn)[5]
    worst = reports.worst_moments(conn, "me")["blunders"][0]
    assert move["class"] == "blunder" and worst["played"] == move["san"]
    assert (worst["win_chance_before"], worst["win_chance_after"]) == (move["win_chance_before"], move["win_chance_after"])
    assert move["win_chance_before"] != round(move["win_chance_before"])   # one decimal, not a whole number


# --- missed_opportunities -------------------------------------------------------------------

def _three_games():
    conn = connect(":memory:")
    for url, end, mate_n, tc in (("https://www.chess.com/game/live/1", 100, 3, "blitz"),
                                 ("https://www.chess.com/game/live/2", 300, 2, "rapid"),
                                 ("https://www.chess.com/game/live/3", 200, 3, "blitz")):
        conn.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (url, "me", end, tc, "180+2", "chess", 1, "white", 800, 900, "resigned", _pgn(RUY)))
        _seed_analysis(conn, _pgn(RUY), QUIET, url=url, mate={4: mate_n}, best={4: "f1c4"})
    conn.commit()
    return conn


def test_missed_mates_section_orders_shortest_then_newest():
    section = opportunities.missed_opportunities(_three_games(), "me")["missed_mates"]
    assert section["total"] == 3 and section["short"] == 3
    assert [(m["game"][-1], m["mate_n"]) for m in section["missed"]] == [("2", 2), ("3", 3), ("1", 3)]
    first = section["missed"][0]
    assert first["move_no"] == 3 and first["played_san"] == "Bb5" and first["best_san"] == "Bc4"
    assert set(first) == {"game", "move_no", "fen", "played_san", "best_san", "mate_n", "time_spent", "clock_after"}
    assert section["by_phase"]["opening"] == {"total": 3, "short": 3}
    assert section["by_phase"]["endgame"] == {"total": 0, "short": 0}


def test_missed_mates_follow_the_existing_filters():
    conn = _three_games()
    ids = lambda **kw: [m["game"][-1] for m in opportunities.missed_opportunities(conn, "me", **kw)["missed_mates"]["missed"]]
    assert ids(n=1) == ["2"]
    assert ids(time_class="blitz") == ["3", "1"]
    assert ids(last_n=1) == ["2"]                   # the newest analysed game only
    assert opportunities.missed_opportunities(conn, "me", n=1)["missed_mates"]["total"] == 3   # n only trims the list
