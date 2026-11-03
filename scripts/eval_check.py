"""Numeric check of the eval answers (docs/eval/DESIGN.md): is every number in an answer one the
run's own tools returned?

For each run in private/eval/runs/ the numbers of the answer are sorted into four classes:

  excluded     not a claim: list numbering ("1." at the start of a line), ordinals (1st, 9th),
               "per 100", a time-control label (10+0, "5-minute blitz", "30+ minute games"), a rating
               band name ("1,100+", "1000-1200 band"), a game-in-session bucket ("games 1-3",
               "9+", like an ordinal) and any number that is in the question itself.
  direct       a number the run's tools returned, as written or as a rounding of one (0.445 ->
               44.5, 44, 45; 88.6 -> 89). A share between 0 and 1 is also read as a percentage and
               an engine score "cp" (centipawns) may be said in pawns (-390 -> 3.9); the sign is not
               a claim. A move number ("23. Nf3", "move 23") is accepted when a tool returned the
               ply it belongs to (45 or 46).
  derived      not returned, but computed from what was:
                 - "N%" equals a/b (rounded to the places written) for two directly supported
                   numbers, a <= b, b >= 10, of the same sentence or the one before it ("158 of
                   those 399 games (40%)");
                 - "about/roughly/around/approximately/nearly/~ N" is within 2% of a number a tool
                   returned.
  unsupported  everything else: the candidates for real hallucinations.

Both derived rules are lenient: with several numbers in a sentence, or a long tool output, a wrong
number can match by coincidence. numeric_check_derived.csv lists what each derived number was
matched to, so the leniency can be audited.

The principle is the one in chess_coach.commentary.faithfulness; its regex is too coarse for chat
answers (it reads the 4 of "Rh4" and the 800 of "2,800"), so this script has its own, stricter number
extraction.

Writes to private/eval/: numeric_check.csv (per run), unsupported_review.csv (every unsupported
number with its sentence), numeric_check_derived.csv (audit of the derived ones) and
numeric_check_top_unsupported.csv (the 15 most frequent unsupported numbers).

Usage: python scripts/eval_check.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "private" / "eval"
RUNS = EVAL / "runs"
OUT = EVAL / "numeric_check.csv"
OUT_TOP = EVAL / "numeric_check_top_unsupported.csv"
OUT_REVIEW = EVAL / "unsupported_review.csv"
OUT_DERIVED = EVAL / "numeric_check_derived.csv"

NEAR = Decimal("0.02")                    # "about N" is read within 2% of a tool number

_NAME = re.compile(r"^(sonnet|haiku)_q(\d\d)_r(\d)\.json$")
# A number, not glued to a letter or digit before it (Rh4, f7, x2); thousands separators allowed.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_LIST_MARK = re.compile(r"^\s*(?:[-*>]\s*)*\**\d{1,2}[.)]\**\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*(\"])")
# "23. Nf3", "23...Nf3", "23. O-O", "move 23", "moves 23", "ply 45" (the number itself is group 1).
_MOVE_CONTEXT = re.compile(
    r"(?:(?<![A-Za-z0-9_.])(\d{1,3})\s*\.{1,3}\s*(?:[KQRBN]?[a-h]?x?[a-h][1-8]|O-O)|\b(?:moves?|ply)\s+(\d{1,3}))")

# Non-claims, found around a number.
_ORDINAL = re.compile(r"(?:st|nd|rd|th)\b")                       # right after the number
_PER = re.compile(r"\bper\s*$", re.I)                             # right before it
_TIME_CONTROL = re.compile(
    r"(?<![\d.,])\d{1,3}\+\d{1,3}(?![\d.])"                        # 10+0, 3+2 (a "|" is a table cell)
    r"|(?<![\d.,])\d{1,3}\+?\s*[-–]?\s*min(?:ute)?s?\b[-\s]*(?:blitz|rapid|bullet|games?|chess|time controls?|format)?"
    r"(?=\s*(?:blitz|rapid|bullet|games?|chess|time controls?|format)\b)"       # 5-minute blitz
    r"|(?<![\d.,])\d{1,3}\+\s*min(?:ute)?s?\b",                    # 30+ minute games
    re.I)
_BAND_WORD = r"(?:bands?|brackets?|ranges?|rated|rating|elo|players?|peers?)"
_RANGE = r"(?<![\d.,])(?:\d{1,3},\d{3}|\d{3,4})\s*[-–]\s*(?:\d{1,3},\d{3}|\d{3,4})"
_RATING_BAND = re.compile(
    r"(?<![\d.,])(?:\d{1,3},\d{3}|\d{3,4})\+(?![\d.])"             # 1,100+
    rf"|{_RANGE}(?=\s*{_BAND_WORD}\b)"                             # 1000-1200 band
    rf"|\b(?:rated|rating|elo)\s+{_RANGE}",                        # rated 1000-1200
    re.I)
# Game-in-session buckets ("games 1-3", "game 9+", "3+ losses in a row"): positions, like ordinals.
_BUCKET = re.compile(
    r"\bgames?\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\+?"
    r"|(?<![\d.,])\d{1,2}\+(?![\d.%])",
    re.I)
# "about 800" and the far end of "about 800-870" / "roughly 15 to 20".
_APPROX = re.compile(
    r"\b(?:about|roughly|around|approximately|nearly)\s*[-−+]?\s*$"
    r"|~\s*$"
    r"|\b(?:about|roughly|around|approximately|nearly)\s*[-−+]?[\d.,]+%?\s*(?:[-–]|to)\s*$",
    re.I)


@dataclass
class Facts:
    """What one run's tools returned."""
    accepted: set[str] = field(default_factory=set)       # spellings a number may fairly have
    whole: set[int] = field(default_factory=set)          # whole numbers (plies)
    values: set[Decimal] = field(default_factory=set)     # the numbers themselves (and as percent / pawns)

    def update(self, other: "Facts") -> None:
        self.accepted |= other.accepted
        self.whole |= other.whole
        self.values |= other.values


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


def output_facts(output) -> Facts:
    """Every number in one tool output."""
    facts = Facts()

    def add_value(d: Decimal) -> None:
        facts.accepted |= variants(d)
        d = abs(d)
        facts.values.add(d)
        if d <= 1:
            facts.values.add(d * 100)

    def add(token: str) -> None:
        d = to_decimal(token)
        if d is not None:
            add_value(d)
            if d == d.to_integral_value():
                facts.whole.add(int(d))

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
                    add_value(Decimal(repr(x)) / 100)    # centipawns, said in pawns
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
    return facts


def _spans(pattern: re.Pattern, text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)]


def exclusion(sentence: str, m: re.Match, label_spans: list[tuple[int, int]],
              bucket_spans: list[tuple[int, int]]) -> str | None:
    """Why this number is not a claim, or None."""
    if _ORDINAL.match(sentence, m.end()):
        return "ordinal"
    if _PER.search(sentence[:m.start()]) and m.group() in ("100", "1,000"):
        return "per 100"
    if any(a <= m.start() < b for a, b in label_spans):
        return "time control / rating band"
    if any(a <= m.start() < b for a, b in bucket_spans):
        return "session bucket"
    return None


def answer_sentences(answer: str, ignore: set[str]) -> tuple[list[list[dict]], int]:
    """The answer as sentences, each a list of its numbers; plus how many list numbers were dropped."""
    sentences, list_marks = [], 0
    for line in answer.splitlines():
        lm = _LIST_MARK.match(line)
        if lm:
            list_marks += 1
            line = line[lm.end():]
        for sentence in _SENTENCE.split(line):
            move_nums = {m.group(1) or m.group(2) for m in _MOVE_CONTEXT.finditer(sentence)}
            label_spans = _spans(_TIME_CONTROL, sentence) + _spans(_RATING_BAND, sentence)
            bucket_spans = _spans(_BUCKET, sentence)
            nums = []
            for m in _NUMBER.finditer(sentence):
                d = to_decimal(m.group())
                if d is None:
                    continue
                after = sentence[m.end():]
                reason = "from the question" if canon(d) in ignore else exclusion(sentence, m, label_spans, bucket_spans)
                nums.append({"raw": m.group(), "number": canon(d), "value": d,
                             "sentence": sentence.strip(), "move": m.group() in move_nums,
                             "percent": bool(re.match(r"\s*(?:%|percent\b)", after)),
                             "approx": bool(_APPROX.search(sentence[:m.start()])),
                             "excluded": reason})
            if nums:
                sentences.append(nums)
    return sentences, list_marks


def percent_from(p: Decimal, candidates: list[Decimal]) -> str | None:
    """'a/b' when p (a percentage, as written) is a/b rounded to the places written."""
    places = min(max(-p.as_tuple().exponent, 0), 2)
    q = Decimal(1).scaleb(-places)
    for a in candidates:
        for b in candidates:
            if 0 < a <= b and b >= 10:       # a ratio of single digits (2/5) is mostly a coincidence
                r = a * 100 / b
                if p in (r.quantize(q, rounding=ROUND_HALF_UP), r.quantize(q, rounding=ROUND_HALF_EVEN)):
                    return f"{canon(a)}/{canon(b)}"
    return None


def near_value(n: Decimal, values: set[Decimal]) -> Decimal | None:
    """A tool number within 2% of n, or None."""
    if n == 0:
        return None
    hits = [v for v in values if abs(v - n) <= NEAR * n]
    return min(hits, key=lambda v: abs(v - n)) if hits else None


def check_run(d: dict) -> dict:
    facts = Facts()
    for call in d["tool_calls"]:
        facts.update(output_facts(call.get("output")))
    ignore = {canon(x) for x in (to_decimal(m.group()) for m in _NUMBER.finditer(d["question"])) if x is not None}
    sentences, list_marks = answer_sentences(d["answer"], ignore)

    for sent in sentences:                                   # pass 1: excluded and direct
        for n in sent:
            if n["excluded"]:
                n["status"] = "excluded"
                continue
            ok = n["number"] in facts.accepted
            if not ok and n["move"] and n["number"].isdigit():
                k = int(n["number"])
                ok = k in facts.whole or (2 * k - 1) in facts.whole or (2 * k) in facts.whole
            n["status"] = "direct" if ok else "pending"

    for i, sent in enumerate(sentences):                     # pass 2: derived, from the direct ones nearby
        nearby = [n for s in sentences[max(i - 1, 0):i + 1] for n in s
                  if n["status"] == "direct" and not n["percent"]]
        candidates = [n["value"] for n in nearby]
        for n in sent:
            if n["status"] != "pending":
                continue
            via = None
            if n["percent"]:
                ratio = percent_from(n["value"], candidates)
                via = f"{ratio} (x100)" if ratio else None
            if via is None and n["approx"]:
                v = near_value(n["value"], facts.values)
                via = f"within 2% of tool value {canon(v)}" if v is not None else None
            n["status"], n["via"] = ("derived", via) if via else ("unsupported", None)
    return {"numbers": [n for s in sentences for n in s], "list_marks": list_marks}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows, review, derived_rows = [], [], []
    totals = defaultdict(Counter)                            # model -> status -> count
    excluded_why = defaultdict(Counter)                      # model -> reason -> count
    unsupported = defaultdict(list)                          # number -> [(model, sentence)]
    for path in sorted(RUNS.glob("*.json")):
        m = _NAME.match(path.name)
        if not m:
            continue
        model, question = m.group(1), int(m.group(2))
        d = json.loads(path.read_text(encoding="utf-8"))
        res = check_run(d)
        nums = res["numbers"]
        count = Counter(n["status"] for n in nums)
        count["excluded"] += res["list_marks"]
        excluded_why[model]["list number"] += res["list_marks"]
        totals[model].update(count)
        for n in nums:
            if n["status"] == "excluded":
                excluded_why[model][n["excluded"]] += 1
            elif n["status"] == "derived":
                derived_rows.append({"run": path.stem, "model": model, "number": n["raw"],
                                     "matched_as": n["via"], "sentence": n["sentence"]})
            elif n["status"] == "unsupported":
                review.append({"run": path.stem, "model": model, "question": question,
                               "number": n["raw"], "sentence": n["sentence"]})
                unsupported[n["number"]].append((model, n["sentence"]))
        rows.append({"run": path.stem, "model": model, "question": question,
                     "n_numbers": len(nums) + res["list_marks"], "n_excluded": count["excluded"],
                     "n_direct": count["direct"], "n_derived": count["derived"],
                     "n_unsupported": count["unsupported"],
                     "unsupported_numbers": "; ".join(r["number"] for r in review if r["run"] == path.stem)})

    def write(path: Path, fields: list[str], data: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)

    write(OUT, ["run", "model", "question", "n_numbers", "n_excluded", "n_direct", "n_derived",
                "n_unsupported", "unsupported_numbers"], rows)
    write(OUT_REVIEW, ["run", "model", "question", "number", "sentence"], review)
    write(OUT_DERIVED, ["run", "model", "number", "matched_as", "sentence"], derived_rows)
    print(f"wrote {OUT.name} ({len(rows)} runs), {OUT_REVIEW.name} ({len(review)} numbers), "
          f"{OUT_DERIVED.name} ({len(derived_rows)} numbers)\n")

    print("numbers in the answers, per model")
    print(f"  {'model':7s} {'all':>6s} {'excluded':>9s} {'direct':>7s} {'derived':>8s} {'unsupported':>12s}"
          f"   unsupported / checked")
    for model, c in sorted(totals.items()):
        allnums = sum(c.values())
        checked = allnums - c["excluded"]
        print(f"  {model:7s} {allnums:6d} {c['excluded']:9d} {c['direct']:7d} {c['derived']:8d} "
              f"{c['unsupported']:12d}   {c['unsupported']:4d} / {checked:4d} = {100 * c['unsupported'] / checked:.1f}%")
    print("\nexcluded, by reason")
    for model, why in sorted(excluded_why.items()):
        print(f"  {model:7s} " + ", ".join(f"{k} {v}" for k, v in why.most_common()))

    top = sorted(unsupported.items(), key=lambda kv: -len(kv[1]))[:15]
    with OUT_TOP.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "count", "haiku", "sonnet", "example_sentence"])
        for number, hits in top:
            by_model = Counter(mdl for mdl, _ in hits)
            w.writerow([number, len(hits), by_model["haiku"], by_model["sonnet"], hits[0][1]])


if __name__ == "__main__":
    main()
