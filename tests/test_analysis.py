import chess
import chess.engine

from chess_coach.analysis import analyse_game, analyze_pending, move_losses, phase_of, status
from chess_coach.chesscom import game_row
from chess_coach.db import connect, insert_games

_VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 300, chess.ROOK: 500, chess.QUEEN: 900}


class MaterialEngine:
    """Fake engine: the side to move greedily picks the move that maximises its
    material balance (one ply of lookahead, like a very weak engine).

    Records the depth of every request so tests can check which pass ran.
    """

    def __init__(self):
        self.depths = []

    @staticmethod
    def _white_material(board):
        return sum(_VALUES.get(p.piece_type, 0) * (1 if p.color else -1)
                   for p in board.piece_map().values())

    def analyse(self, board, limit):
        self.depths.append(limit.depth)
        sign = 1 if board.turn == chess.WHITE else -1
        best_move, best = None, None
        for move in board.legal_moves:
            board.push(move)
            value = sign * self._white_material(board)
            board.pop()
            if best is None or value > best:
                best_move, best = move, value
        return {"score": chess.engine.PovScore(chess.engine.Cp(best), board.turn),
                "pv": [best_move]}

    def quit(self):
        pass


# 1.e4 e5 2.Qh5 Nc6 3.Qxf7+?? hangs nothing; 3...Kxf7 wins the queen for a pawn.
BLUNDER_PGN = '[Event "t"]\n\n1. e4 e5 2. Qh5 Nc6 3. Qxf7+ Kxf7 0-1'


def test_move_losses_are_from_the_movers_point_of_view():
    pos = [
        {"cp": 0, "turn": "white"},
        {"cp": -300, "turn": "black"},   # white's move dropped 300
        {"cp": -200, "turn": "white"},   # black's move gave back 100
        {"cp": -200, "turn": "black"},
    ]
    assert move_losses(pos) == [300, 100, 0]


def test_blunder_is_flagged_and_only_costly_moves_are_searched_deeper():
    engine = MaterialEngine()
    positions, refined = analyse_game(engine, BLUNDER_PGN, fast_depth=4, deep_depth=8, refine_loss=80)
    losses = move_losses(positions)
    assert losses[4] >= 800          # 3.Qxf7+?? loses the queen for a pawn
    # The greedy fake also sees 3...Nc6 (leaving f7 loose) as a 100cp slip, so the
    # re-searched positions are plies 3, 4 and 5 - and nothing else.
    assert refined == 3
    assert engine.depths.count(8) == 3
    assert [p["depth"] for p in positions] == [4, 4, 4, 8, 8, 8, 4]


def test_phase_detection():
    assert phase_of(chess.Board()) == "opening"
    endgame = chess.Board("8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 40")
    assert phase_of(endgame) == "endgame"
    middle = chess.Board("r1bq1rk1/ppp2ppp/2n2n2/3pp3/1bPP4/2N1PN2/PP3PPP/R1BQKB1R w KQ - 0 8")
    middle.fullmove_number = 15
    assert phase_of(middle) == "middlegame"


def test_analyze_pending_goes_newest_first_and_is_resumable():
    conn = connect(":memory:")
    for n, t in enumerate((100, 300, 200)):
        raw = {
            "url": f"https://x/{n}", "pgn": BLUNDER_PGN, "end_time": t, "time_class": "blitz",
            "time_control": "300", "rules": "chess", "rated": True,
            "white": {"username": "me", "rating": 800, "result": "win"},
            "black": {"username": "op", "rating": 800, "result": "lose"},
        }
        insert_games(conn, "me", [game_row(raw, "me")])

    first = analyze_pending(conn, MaterialEngine, "me", limit=2, workers=1)
    assert first["games_analyzed"] == 2 and first["games_pending"] == 1
    done = {r[0] for r in conn.execute("SELECT url FROM game_analysis")}
    assert done == {"https://x/1", "https://x/2"}   # end_time 300 and 200, newest first

    second = analyze_pending(conn, MaterialEngine, "me", limit=5, workers=1)
    assert second["analyzed_now"] == 1 and second["games_pending"] == 0
    assert status(conn, "me")["games_analyzed"] == 3
