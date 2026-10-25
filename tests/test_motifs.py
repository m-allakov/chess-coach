import chess
import pytest
from test_gameview import SCHOLAR, RestrictedEngine, _add, _analysed_scholar, _pgn, _seed_analysis, RUY

from chess_coach import gameview, motifreport, reports
from chess_coach.analysis import analyze_pending
from chess_coach.db import connect
from chess_coach.motifs import in_scope, san_of, swing, tag_move
from chess_coach.tagging import pending_count, tag_pending


def tags(fen, played, best=None, before=(), after=(), threat=None, missed_mate=None):
    board = chess.Board(fen)
    return tag_move(board, chess.Move.from_uci(played), best, list(before), list(after), threat, missed_mate)


# --- the rules, one position each -------------------------------------------------------------

def test_swing_counts_both_sides_and_stops_at_an_illegal_move():
    board = chess.Board("4k3/8/8/3p4/8/3Q4/8/4K3 w - - 0 1")
    assert swing(board, ["d3d5", "e8e7"], 2, chess.WHITE) == 1                 # the queen takes a pawn
    assert swing(board, ["d3e4", "d5e4"], 2, chess.WHITE) == -9                # ...and loses itself
    assert swing(board, ["d3e4", "a1a2", "d5e4"], 3, chess.WHITE) == 0         # line breaks at the illegal move


def test_moved_piece_hangs():
    found = tags("4k3/8/8/3p4/8/3Q4/8/4K3 w - - 0 1", "d3e4", "d3d2", ["d3d2"], ["d5e4"])
    assert list(found) == ["moved_piece_hangs"]


def test_an_even_trade_is_not_a_hanging_piece():
    # Bxc6 dxc6: the bishop is recaptured, but it took a knight on the way
    found = tags("4k3/8/2n5/1B1p4/8/8/8/4K3 w - - 0 1", "b5c6", "b5e8", [], ["d5c6"])
    assert "moved_piece_hangs" not in found


def test_left_piece_hanging():
    # the rook on a1 is attacked by the bishop on g7 and the king walks away instead
    found = tags("4k3/6b1/8/8/8/8/8/R3K3 w - - 0 1", "e1e2", "a1a2", ["a1a2"], ["g7a1"])
    assert list(found) == ["left_piece_hanging"]


def test_missed_capture_needs_a_real_gain_the_played_move_did_not_get():
    fen = "4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1"
    assert list(tags(fen, "e1e2", "d1d5", ["d1d5"], ["d5a5"])) == ["missed_capture"]
    assert tags(fen, "d1d5", "d1d5", ["d1d5"], ["e8e7"]) == {}                 # it did capture
    pawn = "4k3/8/8/3p4/8/8/8/3RK3 w - - 0 1"                                   # a pawn is under the threshold
    assert "missed_capture" not in tags(pawn, "e1e2", "d1d5", ["d1d5"], ["e8e7"])


def test_missed_check_and_missed_mate():
    fen = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    assert list(tags(fen, "e1e2", "a1a8", ["a1a8"], ["e8e7"])) == ["missed_check"]
    assert tags(fen, "a1a8", "a1a8", ["a1a8"], ["e8e7"]) == {}
    assert tags(fen, "e1e2", None, [], [], missed_mate=3) == {"missed_mate": {"n": 3}}
    assert tags(fen, "e1e2", None, [], [], missed_mate=6) == {}


def test_allowed_fork_with_and_without_check():
    with_check = tags("4k3/8/8/8/1n6/8/8/R3K2R w - - 0 1", "h1h2", "h1h2", [], ["b4c2", "e1d1", "c2a1"])
    assert with_check == {"allowed_fork": {"with_check": True}}
    quiet = tags("4k3/8/8/8/1n6/4B3/8/R5K1 w - - 0 1", "g1h1", "g1h1", [], ["b4c2", "a1b1", "c2e3"])
    assert quiet == {"allowed_fork": {"with_check": False}}
    # one attacked piece is not a fork
    assert tags("4k3/8/8/8/1n6/8/8/R5K1 w - - 0 1", "g1h1", None, [], ["b4c2", "a1b1", "c2a3"]) == {}


def test_allowed_discovered_attack():
    found = tags("b3k3/8/8/3n4/8/2P5/8/4K2Q w - - 0 1", "e1e2", "e1e2", [], ["d5c3", "e2e3", "a8h1"])
    assert list(found) == ["allowed_discovered_attack"]


def test_allowed_pin():
    found = tags("1r4k1/8/8/8/8/4N3/P7/4K3 w - - 0 1", "a2a3", "a2a3", [], ["b8e8", "a3a4", "e8e3"])
    assert list(found) == ["allowed_pin_or_skewer"]


def test_a_small_loss_does_not_count_as_a_tactic():
    # the same pin, but the pinned knight is never won
    assert tags("1r4k1/8/8/8/8/4N3/P7/4K3 w - - 0 1", "a2a3", "a2a3", [], ["b8e8", "a3a4", "e8e7"]) == {}


def test_ignored_threat_only_when_the_move_leaves_it_in_place():
    fen = "r3k3/8/8/8/8/8/8/N3K3 w - - 0 1"          # the rook on a8 hits the loose knight on a1
    ignored = tags(fen, "e1e2", None, [], ["a8a1"], threat=["a8a1"])
    assert "ignored_threat" in ignored
    met = tags(fen, "a1b3", None, [], ["a8a2"], threat=["a8a1"])
    assert "ignored_threat" not in met
    assert "ignored_threat" not in tags(fen, "e1e2", None, [], ["a8a1"], threat=None)


def test_no_threat_probe_while_in_check():
    check = "4k3/8/8/8/8/8/4r3/4K3 w - - 0 1"        # white is in check; a null move is illegal
    assert "ignored_threat" not in tags(check, "e1e2", None, [], [], threat=["e2e1"])


def test_scope_is_mistakes_blunders_and_short_missed_mates():
    assert in_scope("mistake", None) and in_scope("blunder", None) and not in_scope("inaccuracy", None)
    assert in_scope("ok", 5) and not in_scope("ok", 6) and not in_scope("ok", None)


def test_san_of():
    fen = chess.STARTING_FEN
    assert san_of(fen, "e2e4") == "e4" and san_of(fen, "e2e5") is None and san_of(fen, None) is None


# --- tagging a stored game --------------------------------------------------------------------

def test_tagging_stores_tags_and_get_game_tells_pending_from_done():
    conn = _analysed_scholar()
    before = gameview.get_game(conn, "111")
    blunder = next(m for m in before["moves"] if m["san"] == "Qxf7+")
    assert blunder["motifs"] is None and before["meta"]["motifs_tagged"] is False
    assert next(m for m in before["moves"] if m["san"] == "e4")["motifs"] == []      # too cheap to tag
    assert pending_count(conn, "me") == 1

    out = tag_pending(conn, RestrictedEngine, "me", workers=1)
    assert out["tagged_now"] == 1 and out["motif_pending"] == 0 and out["engine_searches"]["best_line_fills"] == 0
    assert out["engine_searches"]["threat_probes"] >= 1

    after = gameview.get_game(conn, "111")
    blunder = next(m for m in after["moves"] if m["san"] == "Qxf7+")
    assert blunder["motifs"] == ["moved_piece_hangs"] and after["meta"]["motifs_tagged"] is True
    assert tag_pending(conn, RestrictedEngine, "me", workers=1)["tagged_now"] == 0


def test_a_missing_best_line_is_searched_once_and_kept():
    conn = _analysed_scholar()
    conn.execute("UPDATE position_detail SET pv = '' WHERE ply > 0")       # lines never stored
    conn.commit()
    first = tag_pending(conn, RestrictedEngine, "me", workers=1)
    assert first["engine_searches"]["best_line_fills"] > 0
    conn.execute("DELETE FROM motif_game")                                 # e.g. the rules changed
    conn.commit()
    again = tag_pending(conn, RestrictedEngine, "me", workers=1)
    assert again["tagged_now"] == 1 and again["engine_searches"] == {"best_line_fills": 0, "threat_probes": 0}
    assert gameview.get_game(conn, "111")["moves"][4]["motifs"] == ["moved_piece_hangs"]


def test_an_untagged_costly_move_is_labelled_untagged():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    _seed_analysis(conn, _pgn(RUY), [20, 20, 20, 20, 20, -300, -300, -300, -300, -300, -300])
    tag_pending(conn, RestrictedEngine, "me", workers=1)
    move = gameview.get_game(conn, "111")["moves"][4]
    assert move["class"] == "blunder" and move["motifs"] == ["untagged"]      # nothing tactical in this cost


# --- motif_report ------------------------------------------------------------------------------

def test_motif_report_counts_shares_and_pending():
    conn = _analysed_scholar()
    early = motifreport.motif_report(conn, "me")
    assert early["tagged_moves"] == 0 and early["pending_moves"] >= 1 and "tag_motifs" in early["note"]

    tag_pending(conn, RestrictedEngine, "me", workers=1)
    report = motifreport.motif_report(conn, "me")
    assert report["pending_moves"] == 0 and report["pending_games"] == 0
    assert report["tagged_moves"] >= 1
    hang = report["tags"]["moved_piece_hangs"]
    assert hang["count"] == 1 and hang["avg_wp_loss"] > 50
    assert report["by_phase"]["opening"]["tags"]["moved_piece_hangs"] == 1
    assert report["by_color"]["white"]["moves"] >= 1
    example = report["examples"]["moved_piece_hangs"]["costliest"][0]
    assert example["played_san"] == "Qxf7+" and example["move_no"] == 3 and example["url"].endswith("/111")
    assert set(example) == {"url", "move_no", "color", "fen", "played_san", "best_san", "wp_loss",
                            "time_spent", "clock_before"}
    assert "more than 100" in report["definitions"]["share_pct"]
    with pytest.raises(ValueError):
        motifreport.motif_report(conn, "me", min_class="inaccuracy")
    assert motifreport.motif_report(conn, "me", color="black")["tagged_moves"] == 0   # the user was White
    assert motifreport.motif_report(conn, "me", phase="endgame")["tagged_moves"] == 0


# --- conversion_report -------------------------------------------------------------------------

def _winning_then_lost(result="resigned", cps=None, url="https://www.chess.com/game/live/111"):
    conn = connect(":memory:")
    pgn = _pgn(RUY)
    conn.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (url, "me", 100, "blitz", "180+2", "chess", 1, "white", 800, 900, result, pgn))
    _seed_analysis(conn, pgn, cps or [600] * 5 + [-600] * 6, url=url)
    return conn


def test_conversion_counts_match_weakness_report():
    conn = _winning_then_lost()
    report = motifreport.conversion_report(conn, "me")
    weak = reports.weakness_report(conn, "me")["winning_positions"]
    assert report["summary"]["games_reaching_winning_position"] == weak["games_reaching_winning_position"] == 1
    assert report["summary"]["of_which_not_won"] == weak["of_which_not_won"] == 1
    assert report["summary"]["not_won_pct"] == 100.0


def test_conversion_game_details():
    game = motifreport.conversion_report(_winning_then_lost(), "me")["games"][0]
    assert (game["result"], game["termination"], game["collapse_type"]) == ("loss", "resigned", "single")
    assert game["peak_ply"] == 1 and game["peak_win_chance"] > 85
    assert game["collapse_ply"] == 5 and game["collapse_san"] == "Bb5" and game["collapse_wp_loss"] > 70
    assert game["fell_below_50_ply"] == 5
    assert game["collapse_motifs"] is None            # a costly move in a game not tagged yet
    assert game["url"].endswith("/111") and set(game) >= {"collapse_best_san", "clock_at_peak", "clock_at_collapse"}


def test_conversion_types_time_gradual_and_a_won_game():
    timed = motifreport.conversion_report(_winning_then_lost("timeout", [600] * 11), "me")
    assert timed["games"][0]["collapse_type"] == "time" and timed["games"][0]["fell_below_50_ply"] is None
    # the fall is spread over many moves: no single move reaches half of it
    steps = [600, 600, 520, 520, 440, 440, 360, 360, 280, 280, 200]
    slow = motifreport.conversion_report(_winning_then_lost(cps=steps), "me")["games"][0]
    assert slow["collapse_type"] == "gradual" and slow["fell_below_50_ply"] is None
    won = motifreport.conversion_report(_winning_then_lost("win"), "me")
    assert won["summary"]["games_reaching_winning_position"] == 1 and won["summary"]["of_which_not_won"] == 0
    assert won["games"] == []
    assert motifreport.conversion_report(_winning_then_lost(), "me", color="black")["summary"][
        "games_reaching_winning_position"] == 0


def test_conversion_breakdowns():
    report = motifreport.conversion_report(_winning_then_lost(), "me")
    assert report["by_result"] == {"loss": 1} and report["by_termination"] == {"resigned": 1}
    assert report["by_collapse_type"] == {"single": 1} and report["by_collapse_phase"] == {"opening": 1}
    assert report["avg_moves_from_threshold_to_collapse"] == 2.0       # white's 1st, 2nd moves, then the collapse
    assert report["collapse_motifs"]["pending_collapses"] == 1
