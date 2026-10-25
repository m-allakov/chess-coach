"""Why a costly move was costly: rule-based tags read off the engine's stored best lines.

Every rule is plain geometry and material counting with python-chess on the position before
the move, the move played, the engine's best line from there (`pv_before`) and its best line
after the move, i.e. the opponent's best answer (`pv_after`). Nothing here starts an engine; the
one rule that needs data the analysis never stored (`ignored_threat`) takes a ready-made
null-move line as input.

Material: P=1, N=3, B=3, R=5, Q=9. A line's "swing" is what a side wins minus what it loses in
captures along it. The hanging and capture rules look at a short window (a capture and its
recapture); the tactical rules look along the whole stored line, which is at most 5 plies, so a
line that ends on a capture may overstate what the capturing side finally keeps.

A move can carry several tags. Positional and strategic errors match none of them and are
reported as `untagged` by the caller. Change a rule => bump MOTIF_VERSION so moves are re-tagged."""

from __future__ import annotations

import chess

from .mates import MISSED_MATE_MAX

MOTIF_VERSION = 1

TAGS = ("moved_piece_hangs", "left_piece_hanging", "missed_capture", "missed_check", "missed_mate",
        "allowed_fork", "allowed_discovered_attack", "allowed_pin_or_skewer", "ignored_threat")
UNTAGGED = "untagged"

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
KING_WORTH = 100          # a king is worth more than anything when ranking pins and skewers

MIN_NET = 2               # pawns a line must win or lose to count as a real gain or loss
HANG_PLIES = 2            # capture + recapture
CAPTURE_PLIES = 3         # best capture, the reply, the follow-up
TACTIC_PLIES = 5          # the whole stored line (at most 5 plies) after the move
PIECE_MIN = 3             # forks, pins and skewers are about minor pieces and up
EQUIVALENT_SLACK = 1      # a threat that still wins within this much of the original is not met


def _worth(piece: chess.Piece) -> int:
    return KING_WORTH if piece.piece_type == chess.KING else VALUE[piece.piece_type]


def _captured(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return VALUE[chess.PAWN]
    taken = board.piece_at(move.to_square)
    gain = VALUE[taken.piece_type] if taken else 0
    return gain + (VALUE[move.promotion] - 1 if move.promotion else 0)


def _legal(board: chess.Board, uci: str | None) -> chess.Move | None:
    if not uci:
        return None
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    return move if move in board.legal_moves else None


def swing(board: chess.Board, ucis: list[str], plies: int, side: chess.Color) -> int:
    """Material `side` wins minus loses playing the first `plies` moves of `ucis` from `board`.
    Stops at the first move that is not legal, so a stale or truncated line cannot mislead."""
    board, total = board.copy(stack=False), 0
    for uci in ucis[:plies]:
        move = _legal(board, uci)
        if move is None:
            break
        gain = _captured(board, move)
        total += gain if board.turn == side else -gain
        board.push(move)
    return total


# --- x-ray lines: pins and skewers -------------------------------------------------------------

_DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))
_STRAIGHT = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _ray(square: int, df: int, dr: int):
    f, r = chess.square_file(square) + df, chess.square_rank(square) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        yield chess.square(f, r)
        f, r = f + df, r + dr


def _lines(board: chess.Board, attacker: chess.Color) -> set[tuple[int, int, int]]:
    """(slider, front piece, piece behind it) where the attacker's slider looks through one of the
    other side's pieces at another, both worth a minor piece or more (a king counts as most), and
    the two are not worth the same: a pin when the front one is worth less, a skewer when more."""
    found = set()
    for piece_type, directions in ((chess.BISHOP, _DIAGONAL), (chess.ROOK, _STRAIGHT),
                                   (chess.QUEEN, _DIAGONAL + _STRAIGHT)):
        for slider in board.pieces(piece_type, attacker):
            for df, dr in directions:
                hits = []
                for sq in _ray(slider, df, dr):
                    piece = board.piece_at(sq)
                    if piece:
                        hits.append((sq, piece))
                        if len(hits) == 2:
                            break
                if len(hits) < 2:
                    continue
                (front, fp), (behind, bp) = hits
                if fp.color == attacker or bp.color == attacker:
                    continue
                fw, bw = _worth(fp), _worth(bp)
                if fw >= PIECE_MIN and bw >= PIECE_MIN and fw != bw:
                    found.add((slider, front, behind))
    return found


# --- the tags ----------------------------------------------------------------------------------

def tag_move(before: chess.Board, played: chess.Move, best_uci: str | None, pv_before: list[str],
             pv_after: list[str], threat_pv: list[str] | None, missed_mate: int | None) -> dict[str, dict]:
    """The motifs of one costly move, as {tag: details} in the order of TAGS (empty if none apply).

    before     the position before the move, `played` the move made from it
    best_uci   the engine's best move there; pv_before its best line from `before`
    pv_after   the engine's best line after the move (the opponent's best answer first)
    threat_pv  the opponent's best line from `before` had the mover passed, or None when no such
               line exists (the mover is in check) or none was computed
    missed_mate  moves to the mate the mover had and let go (None if there was none)"""
    mover = before.turn
    after = before.copy(stack=False)
    after.push(played)
    tags: dict[str, dict] = {}

    def lost(plies: int) -> int:        # what the mover loses along the move and the best answer
        return -swing(before, [played.uci()] + pv_after, plies, mover)

    reply = _legal(after, pv_after[0]) if pv_after else None
    if reply and after.is_capture(reply):
        if lost(1 + HANG_PLIES) >= MIN_NET:
            tags["moved_piece_hangs" if reply.to_square == played.to_square else "left_piece_hanging"] = {}

    best = _legal(before, best_uci)
    if best and best != played:
        line = pv_before if pv_before and pv_before[0] == best.uci() else [best.uci()]
        if before.is_capture(best):
            gain = swing(before, line, CAPTURE_PLIES, mover)
            got = swing(before, [played.uci()] + pv_after, CAPTURE_PLIES, mover)
            if gain >= MIN_NET and got <= gain - MIN_NET:
                tags["missed_capture"] = {}
        if before.gives_check(best) and not before.gives_check(played):
            tags["missed_check"] = {}

    if missed_mate and missed_mate <= MISSED_MATE_MAX:
        tags["missed_mate"] = {"n": missed_mate}

    if reply and lost(1 + TACTIC_PLIES) >= MIN_NET:
        replied = after.copy(stack=False)
        replied.push(reply)
        fork = _fork(after, replied, reply, mover)
        if fork is not None:
            tags["allowed_fork"] = {"with_check": fork}
        if _discovered(after, replied, reply, mover):
            tags["allowed_discovered_attack"] = {}
        if _lines(replied, not mover) - _lines(before, not mover):
            tags["allowed_pin_or_skewer"] = {}

    if threat_pv and not before.is_check():
        threatened = before.copy(stack=False)
        threatened.push(chess.Move.null())
        threat = swing(threatened, threat_pv, TACTIC_PLIES, not mover)
        if threat >= MIN_NET and lost(1 + TACTIC_PLIES) >= max(MIN_NET, threat - EQUIVALENT_SLACK):
            tags["ignored_threat"] = {}
    return {tag: tags[tag] for tag in TAGS if tag in tags}


def _fork(after: chess.Board, replied: chess.Board, reply: chess.Move, mover: chess.Color) -> bool | None:
    """None if the opponent's reply forks nothing; else whether the fork is also a check.
    A fork attacks two of the mover's pieces worth a minor piece or more, or the king and one such
    piece. A reply that itself takes a minor piece or more is a capture, not a fork."""
    if _captured(after, reply) >= PIECE_MIN:
        return None
    attacked = replied.attacks(reply.to_square)
    pieces = [sq for sq in attacked if (p := replied.piece_at(sq)) and p.color == mover
              and p.piece_type != chess.KING and VALUE[p.piece_type] >= PIECE_MIN]
    check = replied.king(mover) in attacked
    if len(pieces) >= 2 or (check and pieces):
        return check
    return None


def _discovered(after: chess.Board, replied: chess.Board, reply: chess.Move, mover: chess.Color) -> bool:
    """The opponent's reply moves a piece off the line of one of its own bishops, rooks or queens
    and that slider now attacks the mover's king or a piece worth a minor piece or more."""
    for kind in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        for slider in replied.pieces(kind, not mover):
            if slider == reply.to_square:
                continue
            for target in replied.attacks(slider) - after.attacks(slider):
                piece = replied.piece_at(target)
                if (piece and piece.color == mover and _worth(piece) >= PIECE_MIN
                        and reply.from_square in chess.SquareSet(chess.between(slider, target))):
                    return True
    return False


def in_scope(kind: str, missed_mate: int | None) -> bool:
    """Which moves get tagged: mistakes, blunders, and mates of MISSED_MATE_MAX moves or fewer that
    were let go. Inaccuracies are not tagged."""
    return kind in ("mistake", "blunder") or bool(missed_mate and missed_mate <= MISSED_MATE_MAX)


def san_of(fen: str, uci: str | None) -> str | None:
    """SAN of a stored UCI move in the position `fen`, or None if it is missing or illegal there."""
    board = chess.Board(fen)
    move = _legal(board, uci)
    return board.san(move) if move else None
