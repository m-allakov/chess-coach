"""Coaching principles distilled from reference literature, in the project's own words.

A principle is one JSON object per line (JSONL) with a fixed schema (see docs/pedagogy/SCHEMA.md).
This module validates records, loads them, and checks that a claim does not copy a source text.
The source books are private and are never read by this module unless a path is given."""

from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = {"definition", "principle", "heuristic", "diagnostic", "taxonomy", "process_step",
         "exercise_type", "misconception", "curriculum_level"}
EVIDENCE = {"author_claim", "worked_example", "empirical", "consensus"}
STATUS = {"candidate", "supported_by_data", "refuted_by_data", "untestable"}
CONFIDENCE = {"low", "medium", "high"}
# Where a principle is used in the application. Extending this list is a deliberate act.
HOOKS = {
    "report.blunder_causes", "report.thrown_wins", "report.resilience", "report.clock",
    "drill.threat_check", "drill.threat_then_prevention", "drill.opponent_just_erred",
    "explain.move", "coach.habit", "coach.level_assessment", "eval.principle_test",
}
CAUSES = {"time", "thrown_win", "opening", "blunder", "gradual"}   # keep in step with coach.CAUSES
MAX_ADVICE_WORDS = 40
KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
MAX_CLAIM_WORDS = 60
MAX_QUOTE_WORDS = 12
ID_PATTERN = re.compile(r"^[a-z]{2,6}-\d{3}$")
REQUIRED = ("id", "claim", "kind", "source", "evidence", "status", "confidence")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def validate_record(rec: dict, known_books: set[str] | None = None) -> list[str]:
    """Return a list of problems with one record (empty when it is valid)."""
    errs: list[str] = []
    for key in REQUIRED:
        if key not in rec:
            errs.append(f"missing field: {key}")
    if errs:
        return errs
    if not isinstance(rec["id"], str) or not ID_PATTERN.match(rec["id"]):
        errs.append("id must look like 'abc-001'")
    claim = rec["claim"]
    if not isinstance(claim, str) or not claim.strip():
        errs.append("claim must be a non-empty string")
    elif len(words(claim)) > MAX_CLAIM_WORDS:
        errs.append(f"claim is longer than {MAX_CLAIM_WORDS} words: one idea per record")
    for key, allowed in (("kind", KINDS), ("evidence", EVIDENCE), ("status", STATUS), ("confidence", CONFIDENCE)):
        if rec[key] not in allowed:
            errs.append(f"{key} must be one of {sorted(allowed)}")
    src = rec["source"]
    if not isinstance(src, dict) or not src.get("book") or not src.get("location"):
        errs.append("source needs 'book' and 'location'")
    elif known_books is not None and src["book"] not in known_books:
        errs.append(f"unknown source book: {src['book']}")
    for hook in rec.get("app_hooks", []):
        if hook not in HOOKS:
            errs.append(f"unknown app hook: {hook}")
    for cause in rec.get("relevant_to", []):
        if cause not in CAUSES:
            errs.append(f"unknown cause in relevant_to: {cause}")
    advice = rec.get("advice")
    if advice is not None and (not isinstance(advice, str) or not advice.strip() or len(words(advice)) > MAX_ADVICE_WORDS):
        errs.append(f"advice must be a short sentence of at most {MAX_ADVICE_WORDS} words")
    if rec.get("relevant_to") and not advice:
        errs.append("a principle that is relevant_to a cause needs user-facing advice")
    prio = rec.get("priority")
    if prio is not None and not (isinstance(prio, int) and 1 <= prio <= 9):
        errs.append("priority must be an integer from 1 (most important) to 9")
    pred = rec.get("testable_prediction")
    if rec["status"] == "untestable" and pred:
        errs.append("an untestable principle must not carry a testable_prediction")
    if pred is not None and not isinstance(pred, str):
        errs.append("testable_prediction must be a string or null")
    quote = rec.get("quote")
    if quote and len(words(quote)) > MAX_QUOTE_WORDS:
        errs.append(f"quote is longer than {MAX_QUOTE_WORDS} words: paraphrase instead")
    aud = rec.get("audience")
    if aud is not None:
        lo, hi = aud.get("min_rating"), aud.get("max_rating")
        if not all(isinstance(v, int) or v is None for v in (lo, hi)) or (lo is not None and hi is not None and lo > hi):
            errs.append("audience min_rating/max_rating must be integers with min <= max")
    return errs


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_file(path: Path, known_books: set[str] | None = None) -> dict[str, list[str]]:
    """Problems per record id (or per line for unparseable lines); also catches duplicate ids and dangling references."""
    problems: dict[str, list[str]] = {}
    records, seen = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            problems[f"line {n}"] = ["not valid JSON"]
            continue
        records.append(rec)
        errs = validate_record(rec, known_books)
        if rec.get("id") in seen:
            errs.append("duplicate id")
        seen.add(rec.get("id"))
        if errs:
            problems[str(rec.get("id", f"line {n}"))] = errs
    ids = {r.get("id") for r in records}
    for rec in records:
        for other in rec.get("conflicts_with", []):
            if other not in ids:
                problems.setdefault(rec["id"], []).append(f"conflicts_with refers to unknown id {other}")
    return problems


def shared_run(claim: str, source_text: str, n: int = 8) -> str | None:
    """The first run of `n` consecutive words that a claim shares with a source text, if any.
    A claim in the project's own words should share no long runs with the book it summarises."""
    cw = words(claim)
    if len(cw) < n:
        return None
    sw = words(source_text)
    grams = {" ".join(sw[i: i + n]) for i in range(len(sw) - n + 1)}
    for i in range(len(cw) - n + 1):
        run = " ".join(cw[i: i + n])
        if run in grams:
            return run
    return None


def sources() -> dict[str, dict]:
    """Bibliographic records by book key."""
    data = json.loads((KNOWLEDGE_DIR / "sources.json").read_text(encoding="utf-8"))
    return {b["key"]: b for b in data["books"]}


def load_all() -> list[dict]:
    """Every shipped principle, in file and line order."""
    out: list[dict] = []
    for f in sorted((KNOWLEDGE_DIR / "principles").glob("*.jsonl")):
        out.extend(load(f))
    return out
