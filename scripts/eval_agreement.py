"""How well does the LLM judge (scripts/eval_judge.py) agree with my hand labels? (issue #1)

Inputs, all in private/eval/:
  judge/<run>.json   the judge's labels, one file per run
  hand_sample.csv    my labels for the 20 sampled runs: counts of contradicted / misframed claims,
                     advice_relevant (0/1), trap_pass (question 10)
  q10_trap.md        my trap scoring of the ten question 10 runs (v1 and v2) and the
                     contradicted claims I found while scoring them

The comparison is per run, not per claim (the judge and I word and split claims differently).
For every run that has hand labels and a judgement:

  (a) any claim contradicted or misframed: yes/no          hand (contradicted + misframed) > 0
                                                           against the judge's count > 0
  (b) number of contradicted + misframed claims            agree when the two counts differ by at
                                                           most one
  (c) advice_relevant                                      the same 0/1. A judge "null" (no advice
                                                           or no weekly focus to compare with)
                                                           against a hand 0/1 is a disagreement.
  (d) trap_pass (question 10)                              the judge's trap_pass against my v2 score
                                                           (the judge applies rule v2); my v1 score is
                                                           shown for reference. All ten question 10
                                                           runs count, not only the sampled ones.

Trust threshold 85%, computed on (a) and (c) pooled (every run x metric is one comparison). (b) and
(d) are reported next to it but are not in that figure. Unverifiable claims are not compared.

Every disagreement is listed, per run, with the judge's evidence. A last section checks whether the
judge found each Q10 claim I wrote down as contradicted; it is a recall check, not in the percentages.

The script can run on a part of the runs: runs without hand labels or without a judgement are
listed under "Not compared". Prints the report and saves it to private/eval/judge_agreement.md.

Usage: python scripts/eval_agreement.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "private" / "eval"
JUDGE = EVAL / "judge"
HAND = EVAL / "hand_sample.csv"
TRAP = EVAL / "q10_trap.md"
REPORT = EVAL / "judge_agreement.md"
THRESHOLD = 0.85
METRICS = {
    "a": "any contradicted or misframed claim (yes/no)",
    "b": "count of contradicted + misframed, within 1",
    "c": "advice_relevant",
    "d": "trap_pass (v2, question 10)",
}

_TRAP_LINE = re.compile(r"^\s*((?:haiku|sonnet)_q10_r\d)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.*)$")
_Q10_CLAIM = re.compile(r'^\s*[-*]\s*((?:haiku|sonnet)_q10_r\d)\s*:\s*"(.+?)"\s*(?:\((.*)\))?\s*$')


def to_bool(text: str) -> bool | None:
    t = (text or "").strip().lower()
    if t in ("pass", "1", "true", "yes", "y"):
        return True
    if t in ("fail", "0", "false", "no", "n"):
        return False
    return None


def to_int(text: str) -> int | None:
    t = (text or "").strip()
    return int(t) if re.fullmatch(r"\d+", t) else None


def load_judge() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(JUDGE.glob("*.json"))}


def load_hand() -> dict[str, dict]:
    if not HAND.exists():
        return {}
    with HAND.open(encoding="utf-8", newline="") as f:
        return {r["run"]: r for r in csv.DictReader(f)}


def load_trap() -> tuple[dict[str, dict], list[tuple[str, str, str]]]:
    """(run -> {v1, v2, reason}, [(run, quoted claim, note)])"""
    scores, claims = {}, []
    if not TRAP.exists():
        return scores, claims
    for line in TRAP.read_text(encoding="utf-8").splitlines():
        m = _TRAP_LINE.match(line)
        if m:
            scores[m.group(1)] = {"v1": to_bool(m.group(2)), "v2": to_bool(m.group(3)), "reason": m.group(4)}
            continue
        m = _Q10_CLAIM.match(line)
        if m:
            claims.append((m.group(1), m.group(2), m.group(3) or ""))
    return scores, claims


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:[.,][0-9]+)*", text.lower()))


def flagged(judged: dict) -> list[dict]:
    """The claims the judge called contradicted or misframed."""
    return [c for c in judged["judgement"]["claims"] if c["label"] in ("contradicted", "misframed")]


def show_flagged(judged: dict) -> list[str]:
    cs = flagged(judged)
    if not cs:
        return ["      judge flagged no claim"]
    return [f'      judge {c["label"]}: "{c["claim"]}" | evidence: {c["evidence"]} | {c.get("reason", "")}'
            for c in cs]


def pct(a: int, n: int) -> str:
    return f"{100 * a / n:.0f}%" if n else "-"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    judge = load_judge()
    hand = load_hand()
    trap, q10_claims = load_trap()
    if not judge:
        sys.exit(f"no judgements in {JUDGE}: run scripts/eval_judge.py first")

    tally = {k: [0, 0] for k in METRICS}               # metric -> [agree, compared]
    miss = {"judge missed": 0, "judge added": 0}      # (a): hand yes / judge no, and the reverse
    problems: dict[str, list[str]] = defaultdict(list)
    skipped: list[str] = []

    def record(metric: str, ok: bool) -> None:
        tally[metric][0] += ok
        tally[metric][1] += 1

    for run, row in sorted(hand.items()):
        c, m = to_int(row.get("contradicted", "")), to_int(row.get("misframed", ""))
        adv = to_int(row.get("advice_relevant", ""))
        if c is None and m is None and adv is None:
            skipped.append(f"{run}: no hand labels yet")
            continue
        if run not in judge:
            skipped.append(f"{run}: has hand labels, not judged yet")
            continue
        j = judge[run]
        if c is not None and m is not None:
            hand_n = c + m
            judge_n = j["counts"]["contradicted"] + j["counts"]["misframed"]
            ok_a = (hand_n > 0) == (judge_n > 0)
            record("a", ok_a)
            if not ok_a:
                miss["judge missed" if hand_n > 0 else "judge added"] += 1
                problems[run].append(f"(a) hand contradicted+misframed {hand_n}, judge {judge_n}")
                problems[run] += show_flagged(j)
            ok_b = abs(hand_n - judge_n) <= 1
            record("b", ok_b)
            if not ok_b:
                problems[run].append(f"(b) hand contradicted+misframed {hand_n}, judge {judge_n} (more than 1 apart)")
                if ok_a:
                    problems[run] += show_flagged(j)
        else:
            skipped.append(f"{run}: contradicted/misframed incomplete in hand_sample.csv (a, b skipped)")
        if adv in (0, 1):
            judged_value = j["judgement"]["advice_relevant"]
            ok_c = judged_value == adv
            record("c", ok_c)
            if not ok_c:
                problems[run].append(f"(c) advice_relevant hand {adv}, judge {judged_value}: "
                                     f"{j['judgement']['advice_reason']}")

    for run in sorted(trap):
        if run not in judge:
            skipped.append(f"{run}: question 10 run not judged yet")
            continue
        mine, theirs = trap[run]["v2"], judge[run]["judgement"]["trap_pass"]
        ok_d = mine == theirs
        record("d", ok_d)
        if not ok_d:
            problems[run].append(
                f"(d) trap_pass hand v2 {'pass' if mine else 'fail'} (v1 {'pass' if trap[run]['v1'] else 'fail'}; "
                f"{trap[run]['reason']}), judge {'pass' if theirs else 'fail'}: "
                f"{judge[run]['judgement']['trap_reason']}")

    out = ["# Judge vs hand labels (run level)", "",
           f"{len(judge)} runs judged.", "",
           "| metric | agree | compared | % |", "|---|---|---|---|"]
    for k, text in METRICS.items():
        a, n = tally[k]
        out.append(f"| ({k}) {text} | {a} | {n} | {pct(a, n)} |")
    won = tally["a"][0] + tally["c"][0]
    done = tally["a"][1] + tally["c"][1]
    out.append("")
    if done:
        overall = won / done
        verdict = "**reached**" if overall >= THRESHOLD else "**not reached**"
        out.append(f"Overall, (a) and (c) pooled: {won}/{done} = {100 * overall:.1f}%. "
                   f"Trust threshold {100 * THRESHOLD:.0f}%: {verdict}.")
        if tally["a"][1]:
            out.append(f"In (a), the judge missed {miss['judge missed']} run(s) in which I found a problem "
                       f"and flagged {miss['judge added']} run(s) in which I found none.")
    else:
        out.append("Nothing to compare yet: fill in hand_sample.csv and judge those runs.")

    out += ["", f"## Disagreements ({len(problems)} runs)", ""]
    for run in sorted(problems):
        out.append(f"- **{run}**")
        for line in problems[run]:
            out.append(line if line.startswith("      ") else f"    {line}")
    if not problems:
        out.append("None.")

    if q10_claims:
        out += ["", "## Q10 contradicted claims I found: did the judge find them?", ""]
        for run, quote, note in q10_claims:
            if run not in judge:
                out.append(f"- {run}: not judged yet")
                continue
            target = words(quote)
            best, best_score = None, 0.0
            for cl in judge[run]["judgement"]["claims"]:
                score = len(target & words(cl["claim"])) / len(target) if target else 0.0
                if score > best_score:
                    best, best_score = cl, score
            if best and best_score >= 0.6:
                verdict = "found" if best["label"] == "contradicted" else f"found, labelled {best['label']}"
                out.append(f'- {run}: "{quote}": **{verdict}** (judge: "{best["claim"]}")')
            else:
                out.append(f'- {run}: "{quote}": **not found** among the judge\'s claims')

    if skipped:
        out += ["", "## Not compared", ""] + [f"- {s}" for s in skipped]

    report = "\n".join(out) + "\n"
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"(saved to {REPORT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
