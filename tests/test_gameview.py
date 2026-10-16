import io

import chess
import chess.engine
import chess.pgn
import pytest
from test_analysis import MaterialEngine

from chess_coach import gameview
from chess_coach.analysis import analyze_pending, analyse_game, backfill_details, search_detail
from chess_coach.chesscom import game_row
from chess_coach.db import connect, insert_games
from chess_coach.moves import clock_fields

URL = "https://www.chess.com/game/live/111"


def _pgn(movetext, white="me", black="op", tc="180+2", eco="Kings-Pawn-Opening-Ruy-Lopez-3.Bb5", result="0-1"):
    head = (f'[Event "Live Chess"]\n[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n'
            f'[ECO "C60"]\n[ECOUrl "https://www.chess.com/openings/{eco}"]\n[WhiteElo "800"]\n'
            f'[BlackElo "900"]\n[TimeControl "{tc}"]\n[Termination "{black} won by resignation"]\n\n')
    return head + movetext


def clk(seconds):
    return "{[%clk 0:0" + f"{int(seconds // 60)}:{seconds % 60:04.1f}" + "]}"


# 1.e4 e5 2.Qh5 Nc6 3.Qxf7+ Kxf7, 3+2, with clocks.
CLOCKS = [180.0, 179.0, 178.0, 177.0, 170.0, 170.5]
SCHOLAR = _pgn(" ".join(f"{n}. {w} {clk(CLOCKS[2 * n - 2])} {n}... {b} {clk(CLOCKS[2 * n - 1])}"
                        for n, (w, b) in enumerate((("e4", "e5"), ("Qh5", "Nc6"), ("Qxf7+", "Kxf7")), 1))
               + " 0-1")
RUY = ("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 0-1")


def _add(conn, pgn, url=URL, color="white", end_time=100, time_class="blitz", tc="180+2",
         result="resigned", my=800, opp=900):
    conn.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (url, "me", end_time, time_class, tc, "chess", 1, color, my, opp, result, pgn))
    conn.commit()


def _seed_analysis(conn, pgn, cps, url=URL, best=None, pv=None, mate=None):
    """Store a hand-made analysis: cps are white-POV evals before each ply (len = plies + 1)."""
    board = chess.Board()
    moves = list(chess.pgn.read_game(io.StringIO(pgn)).mainline_moves())
    rows = []
    for i, cp in enumerate(cps):
        move = moves[i] if i < len(moves) else None
        rows.append((url, i, board.fen(), "white" if board.turn else "black", "opening",
                     board.san(move) if move else None, move.uci() if move else None, cp,
                     (best or {}).get(i, move.uci() if move else None), 16))
        if move:
            board.push(move)
    conn.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO position_detail VALUES (?,?,?,?)",
                     [(url, i, (mate or {}).get(i), (pv or {}).get(i)) for i in range(len(cps))])
    conn.execute("INSERT INTO game_analysis VALUES (?,12,16,0,1.0,1)", (url,))
    conn.execute("INSERT INTO game_detail VALUES (?,?,1)", (url, "Stockfish 17"))
    conn.commit()


def _analysed_scholar(engine=MaterialEngine):
    conn = connect(":memory:")
    _add(conn, SCHOLAR, color="white")
    analyze_pending(conn, engine, "me", workers=1)
    return conn


def test_ply_numbering_and_move_numbers():
    conn = _analysed_scholar()
    moves = gameview.get_game(conn, "111", username="me")["moves"]
    assert [m["ply"] for m in moves] == [1, 2, 3, 4, 5, 6]
    assert [(m["move_no"], m["color"]) for m in moves][:2] == [(1, "white"), (1, "black")]
    # Black's n-th move is ply 2n.
    assert (moves[5]["san"], moves[5]["ply"], moves[5]["move_no"], moves[5]["color"]) == ("Kxf7", 6, 3, "black")
    assert moves[4]["san"] == "Qxf7+" and moves[4]["uci"] == "h5f7"


def test_clock_after_and_time_spent_include_the_increment():
    moves = gameview.get_game(_analysed_scholar(), "111")["moves"]
    assert [m["clock_after"] for m in moves] == CLOCKS
    # first move: start 180 + 2s increment - 180.0 left; later: own previous clock + 2 - clock now
    assert [m["time_spent"] for m in moves] == [2.0, 3.0, 4.0, 4.0, 10.0, 8.5]


def test_clock_fields_without_clocks_or_daily_games():
    assert clock_fields({"ply": 0, "time_control": "180"}, None)["time_spent"] is None
    assert clock_fields({"ply": 0, "time_control": "1/86400"}, [10.0])["time_spent"] is None


def test_game_without_clock_comments_gives_null_clock_fields():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    moves = gameview.get_game(conn, "111")["moves"]
    assert len(moves) == 10
    assert all(m["clock_after"] is None and m["time_spent"] is None for m in moves)


def test_unknown_game_says_to_sync_first():
    with pytest.raises(ValueError, match="sync_games"):
        gameview.get_game(connect(":memory:"), "999")
    with pytest.raises(ValueError, match="sync_games"):
        gameview.get_game(connect(":memory:"), "not-a-game")


def test_id_and_url_give_the_same_game():
    conn = _analysed_scholar()
    by_id = gameview.get_game(conn, "111", username="me")
    assert gameview.get_game(conn, URL, username="me") == by_id
    assert gameview.get_game(conn, URL + "?tab=review", username="me") == by_id
    assert gameview.get_game(conn, "https://www.chess.com/analysis/game/live/111", username="me") == by_id


def test_unanalysed_game_returns_moves_without_evaluations():
    conn = connect(":memory:")
    _add(conn, SCHOLAR)
    out = gameview.get_game(conn, "111", username="ME")
    assert out["meta"]["analyzed"] is False and out["meta"]["my_color"] == "white"
    assert out["meta"]["engine"] is None and out["meta"]["total_plies"] == 6
    m = out["moves"][2]
    assert m["san"] == "Qh5" and m["fen_before"].startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3")
    assert m["eval_before"] is None and m["class"] is None and m["win_chance_before"] is None
    assert gameview.get_game(conn, "111", only_flagged=True)["moves"] == []


def test_meta_comes_from_the_pgn_and_my_color_needs_a_username():
    conn = _analysed_scholar()
    meta = gameview.get_game(conn, "111")["meta"]
    assert meta["my_color"] is None
    assert (meta["white"], meta["black"], meta["white_elo"], meta["black_elo"]) == ("me", "op", 800, 900)
    assert (meta["time_class"], meta["time_control"], meta["result"], meta["eco"]) == ("blitz", "180+2", "0-1", "C60")
    assert meta["end_time"] == "1970-01-01T00:01:40+00:00" and meta["opening"] == "Kings Pawn Opening Ruy Lopez"
    assert meta["analyzed"] and meta["mate_data"] == "complete" and meta["engine_depth"] == 16
    assert gameview.get_game(conn, "111", username="op")["meta"]["my_color"] == "black"


def test_win_chances_are_from_the_movers_side_and_evals_from_white():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    cps = [20, 20, 20, 20, 20, -300, -300, -300, -300, -300, -300]
    _seed_analysis(conn, _pgn(RUY), cps)
    white_blunder = gameview.get_game(conn, "111")["moves"][4]      # 3.Bb5, cp 20 -> -300
    assert white_blunder["eval_before"] == {"cp": 20, "mate": None}
    assert white_blunder["eval_after"] == {"cp": -300, "mate": None}
    assert white_blunder["class"] == "blunder"
    assert white_blunder["win_chance_before"] > 50 > white_blunder["win_chance_after"]
    assert white_blunder["wp_loss"] == pytest.approx(
        white_blunder["win_chance_before"] - white_blunder["win_chance_after"], abs=0.11)   # each is rounded
    # Black's reply keeps the evaluation at -300 for White: good for Black, 0 loss, mover win chance > 50.
    black_reply = gameview.get_game(conn, "111")["moves"][5]
    assert black_reply["eval_before"]["cp"] == -300 and black_reply["win_chance_before"] > 50
    assert black_reply["wp_loss"] == 0.0


def test_classes_best_good_and_best_move_fields():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    cps = [20] * 11
    # ply index 0 played the engine's move (best), index 1 played something else with no cost (good)
    _seed_analysis(conn, _pgn(RUY), cps, best={1: "d7d5"}, pv={0: "e2e4 e7e5 g1f3", 1: "d7d5 e4d5 d8d5"})
    moves = gameview.get_game(conn, "111")["moves"]
    assert moves[0]["class"] == "best" and moves[0]["best_san"] is None and moves[0]["best_uci"] is None
    assert moves[0]["best_pv_san"] == ["e4", "e5", "Nf3"]          # still given when the move was best
    assert moves[1]["class"] == "good" and moves[1]["best_san"] == "d5" and moves[1]["best_uci"] == "d7d5"
    assert moves[1]["best_pv_san"] == ["d5", "exd5", "Qxd5"]
    assert "best_pv_san" not in gameview.get_game(conn, "111", include_pv=False)["moves"][1]


def test_pv_is_converted_to_san_and_capped_at_five_plies():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    pv = "f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1"
    _seed_analysis(conn, _pgn(RUY), [20] * 11, pv={4: pv})
    assert gameview.get_game(conn, "111")["moves"][4]["best_pv_san"] == ["Bb5", "a6", "Ba4", "Nf6", "O-O"]


def test_mate_is_kept_apart_from_cp():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    _seed_analysis(conn, _pgn(RUY), [20] * 4 + [1000, -1000] + [20] * 5, mate={4: 3, 5: -2})
    m = gameview.get_game(conn, "111")["moves"][4]
    assert m["eval_before"] == {"cp": None, "mate": 3}          # positive = White mates
    assert m["eval_after"] == {"cp": None, "mate": -2}


def test_search_detail_reports_mate_and_truncates_the_pv():
    class MateEngine:
        def analyse(self, board, limit, **kw):
            line = list(board.legal_moves)[:1]
            return {"score": chess.engine.PovScore(chess.engine.Mate(-3), chess.WHITE), "pv": line * 8}
    found = search_detail(MateEngine(), chess.Board(), 8)
    assert found["mate"] == -3 and found["cp"] == -997 and len(found["pv"]) == 5
    assert found["best_uci"] == found["pv"][0]


def test_checkmate_position_is_mate_zero_and_names_the_loser():
    fools = _pgn("1. f3 e5 2. g4 Qh4# 0-1", tc="180")
    conn = connect(":memory:")
    _add(conn, fools, tc="180")
    analyze_pending(conn, MaterialEngine, "me", workers=1)
    last = gameview.get_game(conn, "111")["moves"][-1]
    assert last["san"] == "Qh4#"
    assert last["eval_after"] == {"cp": None, "mate": 0, "mated": "white"}


def test_only_flagged_adds_context_and_merges_overlaps():
    conn = connect(":memory:")
    _add(conn, _pgn(RUY))
    # White loses material at index 4 (ply 5) and again at index 6 (ply 7).
    cps = [20, 20, 20, 20, 20, -300, -300, -700, -700, -700, -700]
    _seed_analysis(conn, _pgn(RUY), cps)
    out = gameview.get_game(conn, "111", only_flagged=True)["moves"]
    assert [m["ply"] for m in out] == [4, 5, 6, 7, 8]     # no duplicates where contexts overlap
    assert [m["ply"] for m in out if m.get("context")] == [4, 6, 8]
    assert all(m["class"] in ("blunder", "mistake", "inaccuracy") for m in out if "context" not in m)
    ranged = gameview.get_game(conn, "111", only_flagged=True, ply_from=6, ply_to=7)["moves"]
    assert [m["ply"] for m in ranged] == [6, 7]
    assert [m["ply"] for m in gameview.get_game(conn, "111", ply_from=9)["moves"]] == [9, 10]


def test_new_analyses_store_mate_and_pv_for_every_ply():
    conn = _analysed_scholar()
    rows = conn.execute("SELECT d.ply, d.pv, p.best_uci FROM position_detail d "
                        "JOIN positions p ON p.url = d.url AND p.ply = d.ply ORDER BY d.ply").fetchall()
    assert len(rows) == 7                                  # six moves + the final position
    assert all(r["pv"].split()[0] == r["best_uci"] for r in rows if r["best_uci"])
    assert conn.execute("SELECT complete FROM game_detail").fetchone()[0] == 1


class RestrictedEngine(MaterialEngine):
    def analyse(self, board, limit, root_moves=None):
        out = super().analyse(board, limit)
        if root_moves:
            out["pv"] = list(root_moves)
        return out


def test_backfill_adds_detail_without_touching_stored_evaluations():
    conn = _analysed_scholar()
    before = [tuple(r) for r in conn.execute("SELECT * FROM positions ORDER BY ply")]
    conn.execute("DELETE FROM position_detail")            # an analysis made before details existed
    conn.execute("DELETE FROM game_detail")
    conn.commit()
    assert gameview.get_game(conn, "111")["meta"]["mate_data"] == "missing"

    res = backfill_details(conn, RestrictedEngine, "me", workers=1)
    assert res == {"backfilled_now": 1, "backfill_pending": 0}
    assert [tuple(r) for r in conn.execute("SELECT * FROM positions ORDER BY ply")] == before
    assert conn.execute("SELECT complete FROM game_detail").fetchone()[0] == 0
    out = gameview.get_game(conn, "111")
    assert out["meta"]["mate_data"] == "partial"
    blunder = next(m for m in out["moves"] if m["san"] == "Qxf7+")
    assert blunder["class"] == "blunder" and blunder["best_pv_san"]
    assert backfill_details(conn, RestrictedEngine, "me", workers=1)["backfilled_now"] == 0


def _listed(conn, url, color, time_class, result, opening, end_time, white="me", black="op"):
    pgn = _pgn(RUY, white=white, black=black, eco=opening)
    raw = {"url": url, "pgn": pgn, "end_time": end_time, "time_class": time_class, "time_control": "180",
           "rules": "chess", "rated": True,
           "white": {"username": white, "rating": 800, "result": result if color == "white" else "x"},
           "black": {"username": black, "rating": 900, "result": result if color == "black" else "x"}}
    insert_games(conn, "me", [game_row(raw, "me")])


def test_list_games_filters_and_orders_newest_first():
    conn = connect(":memory:")
    _listed(conn, "https://x/game/live/1", "white", "blitz", "win", "Sicilian-Defense-Najdorf-Variation", 100)
    _listed(conn, "https://x/game/live/2", "black", "rapid", "resigned", "Italian-Game", 300, white="op", black="me")
    _listed(conn, "https://x/game/live/3", "white", "blitz", "agreed", "Sicilian-Defense", 200)
    ids = lambda **kw: [g["game_id"] for g in gameview.list_games(conn, "me", **kw)["games"]]
    assert ids() == ["2", "3", "1"]
    assert ids(n=1) == ["2"]
    assert ids(time_class="blitz") == ["3", "1"]
    assert ids(color="black") == ["2"]
    assert ids(result="draw") == ["3"] and ids(result="loss") == ["2"] and ids(result="win") == ["1"]
    assert ids(opening="SICILIAN") == ["3", "1"]
    assert ids(opening="najdorf") == ["1"]
    g = gameview.list_games(conn, "me", n=1)["games"][0]
    assert (g["opponent"], g["opponent_rating"], g["my_rating"], g["color"]) == ("op", 800, 900, "black")
    assert (g["result"], g["moves_count"], g["analyzed"], g["eco"]) == ("loss", 5, False, "C60")
    assert g["end_time"] == "1970-01-01T00:05:00+00:00" and g["termination"] == "me won by resignation"
    with pytest.raises(ValueError):
        gameview.list_games(conn, "me", color="red")
    with pytest.raises(ValueError):
        gameview.list_games(conn, "me", result="won")


def test_analyse_game_rows_carry_mate_and_pv():
    positions, _ = analyse_game(MaterialEngine(), SCHOLAR, fast_depth=4, deep_depth=8)
    assert all(p["mate"] is None for p in positions)
    assert positions[0]["pv"] == [positions[0]["best_uci"]]
