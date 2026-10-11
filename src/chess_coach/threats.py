"""The "threat lens": what the opponent could do to the player in a position.

Given the position after the player's move (opponent to move), list the player's pieces the
opponent can win material from, the checks available to the opponent, and any mate in one.
This is the deterministic version of Heisman's 'imagine passing' check: it names the concrete
things a player should have looked for before moving."""

from __future__ import annotations

import chess

from .opportunities import _exchange_loss as net_gain_by_capturing


def lens(fen: str) -> dict:
    """Threats against the side that just moved. `fen` has the opponent to move."""
    board = chess.Board(fen)
    victim_color = not board.turn
    hanging = []
    for square, piece in board.piece_map().items():
        if piece.color != victim_color or piece.piece_type == chess.KING:
            continue
        gain = net_gain_by_capturing(board, square)
        if gain >= 1:
            hanging.append({"square": chess.square_name(square), "piece": piece.symbol().upper(), "gain": gain})
    hanging.sort(key=lambda h: -h["gain"])

    checks, mates = [], []
    for move in list(board.legal_moves):
        if board.gives_check(move):
            san = board.san(move)
            checks.append(san)
            board.push(move)
            if board.is_checkmate():
                mates.append(san)
            board.pop()
    return {
        "hanging": hanging,
        "checks": checks[:6],
        "mate_in_one": mates,
        "verdict": "mate" if mates else "material" if hanging else "check" if checks else "clear",
    }
