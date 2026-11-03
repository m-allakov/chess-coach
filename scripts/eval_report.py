"""Summarise the eval runs (docs/eval/DESIGN.md): what the 100 runs cost and how they behaved.

Reads private/eval/runs/<model>_q<NN>_r<N>.json and private/eval/snapshot.txt, writes
private/eval/summary.md. Also draws the hand-check sample (private/eval/hand_sample.csv) once;
an existing sample is never overwritten, because I fill in its labels by hand.

Usage: python scripts/eval_report.py [--seed 20261103]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "private" / "eval"
RUNS = EVAL / "runs"
SNAPSHOT = EVAL / "snapshot.txt"
SUMMARY = EVAL / "summary.md"
HAND_SAMPLE = EVAL / "hand_sample.csv"

WRITE_TOOLS = {"sync_games", "analyze_games", "tag_motifs", "backfill_game_details"}
_NAME = re.compile(r"^(sonnet|haiku)_q(\d\d)_r(\d)\.json$")


def short(tool: str) -> str:
    return tool.rsplit("__", 1)[-1]


def load_runs() -> list[dict]:
    runs = []
    for path in sorted(RUNS.glob("*.json")):
        m = _NAME.match(path.name)
        if not m:
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        d["_name"], d["_q"], d["_r"] = path.stem, int(m.group(2)), int(m.group(3))
        runs.append(d)
    return runs


def snapshot_hash() -> str:
    for line in SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if line.startswith("sha256: "):
            return line.split(": ", 1)[1].strip()
    sys.exit(f"no sha256 in {SNAPSHOT}")


def med(xs: list[float], nd: int = 1) -> float | None:
    return round(st.median(xs), nd) if xs else None


def money(x: float | None) -> str:
    return "-" if x is None else f"${x:.4f}"


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def per_model(runs: list[dict], sha: str) -> str:
    rows = []
    for model in sorted({r["model"] for r in runs}):
        rs = [r for r in runs if r["model"] == model]
        calls = [c for r in rs for c in r["tool_calls"]]
        costs = [r["total_cost_usd"] or 0 for r in rs]
        rows.append([
            model, len(rs),
            sum(r.get("snapshot_sha256") == sha for r in rs),
            sum(bool(c.get("is_error")) or c.get("output") is None for c in calls),
            sum(short(c["name"]) in WRITE_TOOLS for c in calls),
            sum(not r["tool_calls"] for r in rs),
            money(sum(costs)), money(st.median(costs)),
            med([r["wall_time_s"] for r in rs]),
            med([r["usage"]["output_tokens"] for r in rs], 0),
            med([len(r["tool_calls"]) for r in rs], 1),
        ])
    return table(["model", "runs", "snapshot hash OK", "tool errors", "write-tool calls (must be 0)",
                  "runs with 0 tool calls", "total cost", "median cost", "median wall time (s)",
                  "median output tokens", "median tool calls"], rows)


def per_question(runs: list[dict]) -> str:
    by = defaultdict(list)
    for r in runs:
        by[(r["_q"], r["model"])].append(r)
    rows = []
    for (q, model), rs in sorted(by.items()):
        sets = Counter(tuple(sorted({short(c["name"]) for c in r["tool_calls"]})) for r in rs)
        (top, n), = sets.most_common(1)
        rows.append([f"Q{q:02d}", model, len(rs),
                     (", ".join(top) or "(none)") + f"  ({n}/{len(rs)} runs)",
                     money(st.median([r["total_cost_usd"] or 0 for r in rs])),
                     med([r["wall_time_s"] for r in rs])])
    return table(["question", "model", "runs", "most common tool set", "median cost", "median wall time (s)"], rows)


def draw_hand_sample(runs: list[dict], seed: int) -> None:
    """Two runs per question, one per model, drawn at random with a fixed seed."""
    if HAND_SAMPLE.exists():
        print(f"{HAND_SAMPLE.name} exists; left as it is")
        return
    rng = random.Random(seed)
    by = defaultdict(list)
    for r in runs:
        by[(r["_q"], r["model"])].append(r)
    fields = ["run", "model", "question", "answer_path", "claims_checked", "contradicted", "misframed",
              "unverifiable", "advice_relevant", "trap_pass", "notes", "sample_seed"]
    rows = []
    for q in range(1, 11):
        for model in ("haiku", "sonnet"):
            r = rng.choice(sorted(by[(q, model)], key=lambda x: x["_r"]))
            rows.append({"run": r["_name"], "model": model, "question": q,
                         "answer_path": f"private/eval/runs/{r['_name']}.json", "sample_seed": seed})
    with HAND_SAMPLE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {HAND_SAMPLE.name}: {len(rows)} runs (seed {seed})")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261103, help="seed of the hand-check sample")
    args = ap.parse_args()
    runs = load_runs()
    sha = snapshot_hash()
    text = "\n\n".join([
        "# Eval run summary",
        f"{len(runs)} runs. Snapshot SHA-256: `{sha}`",
        "## Per model", per_model(runs, sha),
        "## Per question and model", per_question(runs),
    ]) + "\n"
    SUMMARY.write_text(text, encoding="utf-8")
    print(f"wrote {SUMMARY}")
    draw_hand_sample(runs, args.seed)


if __name__ == "__main__":
    main()
