"""Numeric check of the eval answers (docs/eval/DESIGN.md): is every number in an answer one the
run's own tools returned?

For each run in private/eval/runs/ the numbers of the answer (integers, decimals, percents) are
looked up among the numbers of that run's tool outputs. A number counts as supported when it
appears there as written or as a rounding of one (0.445 -> 44.5, 44, 45; 88.6 -> 89); a share
between 0 and 1 is also read as a percentage, and an engine score "cp" (centipawns) may be said
in pawns (-390 -> 3.9). The principle is the one in
chess_coach.commentary.faithfulness; its regex is too coarse for chat answers (it reads the 4 of
"Rh4" and the 800 of "2,800"), so this script has its own, stricter number extraction.

Left out of the answer's numbers: list numbering ("1." at the start of a line), digits that are
part of a move or square ("Rh4", "f7") and any number that is in the question itself (the game
id of questions 8 and 9). A number written as a move number ("23. Nf3", "move 23") is also
accepted when a tool returned the ply it belongs to (45 or 46).

A number built from several (a sum, a difference, a ratio) is NOT found, so expect false
positives; the most frequent unsupported numbers are printed with their sentence to judge them.

Writes private/eval/numeric_check.csv and private/eval/numeric_check_top_unsupported.csv.

Usage: python scripts/eval_check.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "private" / "eval"
RUNS = EVAL / "runs"
OUT = EVAL / "numeric_check.csv"
OUT_TOP = EVAL / "numeric_check_top_unsupported.csv"

_NAME = re.compile(r"^(sonnet|haiku)_q(\d\d)_r(\d)\.json$")
# A number, not glued to a letter or digit before it (Rh4, f7, x2); thousands separators allowed.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_LIST_MARK = re.compile(r"^\s*(?:[-*>]\s*)*\**\d{1,2}[.)]\**\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*(\"])")
# "23. Nf3", "23...Nf3", "23. O-O", "move 23", "moves 23", "ply 45" (the number itself is group 1).
_MOVE_CONTEXT = re.compile(
    r"(?:(?<![A-Za-z0-9_.])(\d{1,3})\s*\.{1,3}\s*(?:[KQRBN]?[a-h]?x?[a-h][1-8]|O-O)|\b(?:moves?|ply)\s+(\d{1,3}))")


def canon(d: Decimal) -> str:
    """'44.0' and '44' read the same."""
    s = format(d.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def variants(d: Decimal) -> set[str]:
    """The ways a tool number can fairly be written in an answer."""
    d = abs(d)                       # the sign is not a claim ("-3.9" is said "3.9")
    xs = [d] + ([d * 100] if d <= 1 else [])
    out = set()
    for x in xs:
        out.add(canon(x))
        for places in ("0.1", "0.01"):
            out.add(canon(x.quantize(Decimal(places), rounding=ROUND_HALF_UP)))
        out.add(canon(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        out.add(canon(x.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)))
    return out


def output_numbers(output) -> tuple[set[str], set[int]]:
    """Every number in one tool output, as accepted spellings, plus its whole numbers (plies)."""
    accepted: set[str] = set()
    whole: set[int] = set()

    def add(token: str) -> None:
        d = to_decimal(token)
        if d is not None:
            accepted.update(variants(d))
            if d == d.to_integral_value():
                whole.add(int(d))

    def walk(node) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            add(repr(node))
        elif isinstance(node, str):
            if node.count("/") >= 7:     # a FEN: board rows, not facts
                return
            for m in _NUMBER.finditer(node):
                add(m.group())
        elif isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for key, x in node.items():
                if key == "cp" and isinstance(x, (int, float)) and not isinstance(x, bool):
                    accepted.update(variants(Decimal(repr(x)) / 100))    # centipawns, said in pawns
                walk(x)

    blocks = output if isinstance(output, list) else [output]
    for b in blocks:
        text = b.get("text") if isinstance(b, dict) else b
        if not isinstance(text, str):
            walk(b)
            continue
        try:
            walk(json.loads(text))
        except json.JSONDecodeError:
            walk(text)
    return accepted, whole


def answer_numbers(answer: str, ignore: set[str]) -> list[dict]:
    """Numbers of an answer with their sentence; list numbering and the question's numbers are left out."""
    found = []
    for line in answer.splitlines():
        line = _LIST_MARK.sub("", line)
        for sentence in _SENTENCE.split(line):
            move_nums = {m.group(1) or m.group(2) for m in _MOVE_CONTEXT.finditer(sentence)}
            for m in _NUMBER.finditer(sentence):
                d = to_decimal(m.group())
                if d is None or canon(d) in ignore:
                    continue
                found.append({"raw": m.group(), "number": canon(d), "sentence": sentence.strip(),
                              "move": m.group() in move_nums})
    return found


def check_run(d: dict) -> dict:
    accepted: set[str] = set()
    whole: set[int] = set()
    for call in d["tool_calls"]:
        a, w = output_numbers(call.get("output"))
        accepted |= a
        whole |= w
    ignore = {canon(x) for x in (to_decimal(m.group()) for m in _NUMBER.finditer(d["question"])) if x is not None}
    nums = answer_numbers(d["answer"], ignore)
    for n in nums:
        ok = n["number"] in accepted
        if not ok and n["move"] and n["number"].isdigit():
            k = int(n["number"])
            ok = k in whole or (2 * k - 1) in whole or (2 * k) in whole
        n["supported"] = ok
    return {"numbers": nums}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows, totals = [], defaultdict(lambda: [0, 0])
    unsupported = defaultdict(list)           # number -> [(model, sentence)]
    for path in sorted(RUNS.glob("*.json")):
        m = _NAME.match(path.name)
        if not m:
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        nums = check_run(d)["numbers"]
        bad = [n for n in nums if not n["supported"]]
        rows.append({"run": path.stem, "model": m.group(1), "question": int(m.group(2)),
                     "n_numbers": len(nums), "n_supported": len(nums) - len(bad),
                     "unsupported_numbers": "; ".join(n["raw"] for n in bad)})
        totals[m.group(1)][0] += len(bad)
        totals[m.group(1)][1] += len(nums)
        for n in bad:
            unsupported[n["number"]].append((m.group(1), n["sentence"]))
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run", "model", "question", "n_numbers", "n_supported",
                                          "unsupported_numbers"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} runs)\n")

    print("unsupported numbers / all numbers, per model")
    for model, (bad, allnums) in sorted(totals.items()):
        print(f"  {model:7s} {bad:4d} / {allnums:4d} = {100 * bad / allnums:.1f}%")

    top = sorted(unsupported.items(), key=lambda kv: -len(kv[1]))[:15]
    with OUT_TOP.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "count", "haiku", "sonnet", "example_sentence"])
        for number, hits in top:
            by_model = Counter(mdl for mdl, _ in hits)
            w.writerow([number, len(hits), by_model["haiku"], by_model["sonnet"], hits[0][1]])
    print(f"\nthe 15 most frequent unsupported numbers (also in {OUT_TOP.name})")
    for number, hits in top:
        by_model = Counter(mdl for mdl, _ in hits)
        print(f"\n  {number}  x{len(hits)}  (haiku {by_model['haiku']}, sonnet {by_model['sonnet']})")
        print(f"    {hits[0][1][:230]}")


if __name__ == "__main__":
    main()
