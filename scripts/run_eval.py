"""Run the coaching-answer eval (docs/eval/DESIGN.md): every question, on every model, several times.

Each run starts a fresh headless Claude Code process (`claude -p`) that can only use the
chess-coach MCP tools, and saves the answer, every tool call with its full output, tokens and
wall time to private/eval/runs/<model>_q<NN>_r<N>.json. A run whose file exists is skipped, so
the script can be started again after a usage limit. A run that fails is never saved.

Every run reads a frozen, read-only copy of the database (private/eval/games_eval.db, described
in private/eval/snapshot.txt, wired in through private/eval/mcp_eval.json), and the tools that
write (sync_games, analyze_games, tag_motifs, backfill_game_details) are disabled. The SHA-256 of
the snapshot is checked before the first run and after every run; if it differs, the script stops
and does not save that run.

Usage:
  python scripts/run_eval.py --username <chess.com name>                   # all 100 runs
  python scripts/run_eval.py --username <name> --questions 1 --models haiku --runs 1
  python scripts/run_eval.py --dry-run                                     # list the runs
  python scripts/run_eval.py --summary private/eval/runs/haiku_q01_r1.json

The username is not in the repository (it names the author's account); it comes from
--username or the CHESS_COACH_EVAL_USER environment variable.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "eval" / "DESIGN.md"
GAMES = ROOT / "private" / "eval" / "games.csv"
RUNS = ROOT / "private" / "eval" / "runs"
SNAPSHOT = ROOT / "private" / "eval" / "snapshot.txt"
MCP_EVAL = ROOT / "private" / "eval" / "mcp_eval.json"

# MCP tools that change the database; every run has them switched off.
WRITE_TOOLS = tuple(f"mcp__chess-coach__{t}" for t in
                    ("sync_games", "analyze_games", "tag_motifs", "backfill_game_details"))

MODELS = ("sonnet", "haiku")
N_RUNS = 5
TIMEOUT_S = 900
MAX_CONSECUTIVE_FAILURES = 3

# Matched against the result text and stderr of a failed run.
_NOT_LOGGED_IN = re.compile(r"not logged in|please run /login|invalid api key|authentication", re.I)
_USAGE_LIMIT = re.compile(r"usage limit|spend limit|limit reached|rate.?limit|quota|resets? (at|in)|too many requests|\b429\b", re.I)


# ---- questions ---------------------------------------------------------------------------
def load_questions() -> dict[int, str]:
    """The ten questions from the table in DESIGN.md, with the two game placeholders filled
    from games.csv (the most recent not-won game with that cause)."""
    causes: dict[str, str] = {}
    if GAMES.exists():
        with GAMES.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                causes.setdefault(row["code_cause"], row["game_id"])   # file is newest first
    section = DESIGN.read_text(encoding="utf-8").split("## Question set", 1)[1].split("\n## ", 1)[0]
    questions: dict[int, str] = {}
    for line in section.splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split(" | ")]
        if len(parts) >= 2 and parts[0].isdigit():
            questions[int(parts[0])] = parts[1]

    def fill(m: re.Match) -> str:
        if m.group(1) not in causes:
            sys.exit(f"{GAMES} has no game with cause = {m.group(1)}")
        return causes[m.group(1)]
    questions = {n: re.sub(r"<a not-won game with cause = (\w+)>", fill, q) for n, q in questions.items()}
    if sorted(questions) != list(range(1, 11)):
        sys.exit(f"expected 10 questions in {DESIGN}, found {sorted(questions)}")
    return questions


# ---- the frozen database -----------------------------------------------------------------
class SnapshotChanged(Exception):
    pass


def snapshot_info() -> dict[str, str]:
    if not SNAPSHOT.exists():
        sys.exit(f"{SNAPSHOT} not found: create the database snapshot first")
    info = dict(line.split(": ", 1) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines() if ": " in line)
    info["path"] = str(ROOT / info["file"])
    return info


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_snapshot(info: dict[str, str]) -> None:
    now = sha256_of(info["path"])
    if now != info["sha256"]:
        raise SnapshotChanged(f"{info['path']} has SHA-256 {now}, expected {info['sha256']}")


def check_mcp_points_at_snapshot(mcp_config: Path, info: dict[str, str]) -> None:
    """The config must read the frozen copy, never the normal database."""
    cfg = json.loads(mcp_config.read_text(encoding="utf-8"))
    db = cfg["mcpServers"]["chess-coach"].get("env", {}).get("CHESS_COACH_DB")
    if not db or os.path.normcase(os.path.abspath(db)) != os.path.normcase(os.path.abspath(info["path"])):
        sys.exit(f"{mcp_config}: CHESS_COACH_DB is {db!r}, expected the snapshot {info['path']}")


# ---- the claude process ------------------------------------------------------------------
def find_claude(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    # The copy bundled with the Claude desktop app.
    local = os.environ.get("LOCALAPPDATA", "")
    bundled = sorted(glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe")))
    if bundled:
        return bundled[-1]
    sys.exit("claude not found: put it on PATH or pass --claude")


def child_env() -> dict[str, str]:
    """The environment of a run: no variables of a Claude Code session this script may be
    running inside of, so the child starts as a normal, separate Claude Code."""
    drop = ("CLAUDE_", "CLAUDECODE", "ANTHROPIC_BASE_URL")
    return {k: v for k, v in os.environ.items() if not k.startswith(drop)}


def build_command(claude: str, prompt: str, model: str, effort: str, mcp_config: Path) -> list[str]:
    cmd = [claude, "-p", prompt, "--model", model,
           "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(mcp_config), "--strict-mcp-config",
           "--tools", "", "--allowedTools", "mcp__chess-coach__*",
           "--disallowedTools", ",".join(WRITE_TOOLS),
           "--setting-sources", "", "--no-session-persistence"]
    if effort != "default":
        cmd += ["--effort", effort]
    return cmd


def parse_stream(text: str) -> dict:
    """The events of `--output-format stream-json` -> init info, tool calls, final result."""
    init, result, calls, order = {}, None, {}, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            init = ev
        elif kind == "result":
            result = ev
        elif kind in ("assistant", "user"):
            content = ev.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    calls[block["id"]] = {"id": block["id"], "name": block["name"],
                                          "input": block.get("input"), "output": None, "is_error": None}
                    order.append(block["id"])
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    calls[block["tool_use_id"]]["output"] = block.get("content")
                    calls[block["tool_use_id"]]["is_error"] = block.get("is_error", False)
    return {"init": init, "result": result, "tool_calls": [calls[i] for i in order]}


class RunFailed(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def run_once(claude: str, model: str, effort: str, n: int, question: str, username: str,
             mcp_config: Path) -> dict:
    prompt = f"My chess.com username is {username}. {question}"
    cmd = build_command(claude, prompt, model, effort, mcp_config)
    with tempfile.TemporaryDirectory(prefix="chess_eval_") as cwd:   # no CLAUDE.md or memory to leak in
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=child_env(), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise RunFailed("timeout", f"no answer within {TIMEOUT_S} s")
        wall = time.monotonic() - start
    parsed = parse_stream(proc.stdout)
    res = parsed["result"]
    text = (res or {}).get("result") or ""
    blob = f"{text}\n{proc.stderr}"
    if res is None or res.get("is_error") or proc.returncode != 0:
        if _NOT_LOGGED_IN.search(blob):
            raise RunFailed("not_logged_in", text.strip() or proc.stderr.strip()[:300])
        if _USAGE_LIMIT.search(blob):
            raise RunFailed("usage_limit", text.strip() or proc.stderr.strip()[:300])
        if effort != "default" and re.search(r"effort", proc.stderr, re.I):
            raise RunFailed("effort_unsupported", proc.stderr.strip()[:300])
        raise RunFailed("error", (text.strip() or proc.stderr.strip() or f"exit {proc.returncode}")[:300])
    init = parsed["init"]
    return {
        "question_no": None, "question": question, "prompt": prompt.replace(username, "<username>"),
        "model": model, "model_id": init.get("model"), "run": n,
        "effort_requested": effort,
        "claude_code_version": init.get("claude_code_version"),
        "tools_available": init.get("tools"), "tools_disabled": list(WRITE_TOOLS),
        "answer": text,
        "tool_calls": parsed["tool_calls"],
        "num_turns": res.get("num_turns"),
        "usage": res.get("usage"), "model_usage": res.get("modelUsage"),
        "total_cost_usd": res.get("total_cost_usd"),
        "wall_time_s": round(wall, 2), "duration_ms": res.get("duration_ms"),
        "duration_api_ms": res.get("duration_api_ms"),
        "session_id": res.get("session_id"),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


# ---- files and summaries -----------------------------------------------------------------
def run_path(model: str, q: int, n: int) -> Path:
    return RUNS / f"{model}_q{q:02d}_r{n}.json"


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def summarize(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    u = d.get("usage") or {}
    lines = [f"{path.name}",
             f"  model: {d['model']} ({d.get('model_id')})   effort requested: {d.get('effort_requested')}   "
             f"claude code {d.get('claude_code_version')}",
             f"  database snapshot sha256: {d.get('snapshot_sha256')}",
             f"  write tools available: {[t for t in (d.get('tools_available') or []) if t.split('__')[-1] in {w.split('__')[-1] for w in WRITE_TOOLS}] or 'none'}",
             f"  question: {d['question']}",
             f"  wall time: {d['wall_time_s']} s (api {round((d.get('duration_api_ms') or 0) / 1000, 1)} s)   "
             f"turns: {d.get('num_turns')}   cost: ${d.get('total_cost_usd')}",
             f"  tokens: input {u.get('input_tokens')}, output {u.get('output_tokens')}, "
             f"cache read {u.get('cache_read_input_tokens')}, cache write {u.get('cache_creation_input_tokens')}, "
             f"thinking {(u.get('output_tokens_details') or {}).get('thinking_tokens')}",
             f"  tool calls ({len(d['tool_calls'])}):"]
    for c in d["tool_calls"]:
        out = c["output"]
        size = len(json.dumps(out, ensure_ascii=False)) if out is not None else 0
        status = "NO OUTPUT" if out is None else ("error" if c.get("is_error") else f"{size} chars")
        lines.append(f"    - {c['name']} {json.dumps(c['input'], ensure_ascii=False)} -> {status}")
    lines.append("  answer:")
    lines += [f"    {ln}" for ln in d["answer"].splitlines()]
    return "\n".join(lines)


# ---- main --------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", default=os.environ.get("CHESS_COACH_EVAL_USER"))
    ap.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    ap.add_argument("--questions", nargs="+", type=int, default=list(range(1, 11)))
    ap.add_argument("--runs", nargs="+", type=int, default=list(range(1, N_RUNS + 1)))
    ap.add_argument("--effort", default="medium", help="same for both models; 'default' omits the flag")
    ap.add_argument("--mcp-config", type=Path, default=MCP_EVAL)
    ap.add_argument("--claude", help="path to the claude executable")
    ap.add_argument("--dry-run", action="store_true", help="list the runs and stop")
    ap.add_argument("--summary", type=Path, help="print the summary of a saved run and stop")
    args = ap.parse_args()

    if args.summary:
        print(summarize(args.summary))
        return 0

    questions = load_questions()
    # Runs of the same repetition are neighbours, so a stop part-way leaves a balanced sample.
    plan = [(m, q, n) for n in args.runs for q in args.questions for m in args.models]
    total = len(MODELS) * 10 * N_RUNS
    todo = [r for r in plan if not run_path(*r).exists()]
    done_all = sum(run_path(m, q, n).exists() for m in MODELS for q in range(1, 11) for n in range(1, N_RUNS + 1))
    print(f"{len(plan)} runs selected, {len(plan) - len(todo)} already saved, {len(todo)} to do "
          f"({done_all}/{total} of the full set saved)")
    if args.dry_run:
        for m, q, n in todo:
            print(f"  {run_path(m, q, n).name}: {questions[q]}")
        return 0
    if not todo:
        return 0
    if not args.username:
        sys.exit("pass --username or set CHESS_COACH_EVAL_USER")
    if not args.mcp_config.exists():
        sys.exit(f"MCP config not found: {args.mcp_config}")
    claude = find_claude(args.claude)
    snap = snapshot_info()
    check_mcp_points_at_snapshot(args.mcp_config, snap)
    try:
        check_snapshot(snap)
    except SnapshotChanged as e:
        sys.exit(f"Snapshot changed before the first run: {e}")

    failures = 0
    for i, (m, q, n) in enumerate(todo, 1):
        path = run_path(m, q, n)
        print(f"[{i}/{len(todo)}] {path.name} ...", flush=True)
        try:
            data = run_once(claude, m, args.effort, n, questions[q], args.username, args.mcp_config)
        except RunFailed as e:
            if e.kind == "usage_limit":
                done_all = sum(run_path(a, b, c).exists() for a in MODELS for b in range(1, 11) for c in range(1, N_RUNS + 1))
                print(f"Usage limit reached ({e.detail}). Stopped cleanly: {done_all}/{total} runs saved. "
                      f"Start the script again later to resume.")
                return 0
            if e.kind in ("not_logged_in", "effort_unsupported"):
                print(f"Stopped: {e}")
                if e.kind == "not_logged_in":
                    print("Run `claude` in a terminal and /login, then start this script again.")
                else:
                    print("Re-run with --effort default for both models and record that in DESIGN.md.")
                return 1
            failures += 1
            print(f"  failed ({e}); not saved")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"Stopped after {failures} failures in a row.")
                return 1
            continue
        try:
            check_snapshot(snap)
        except SnapshotChanged as e:
            print(f"Stopped: the database snapshot changed during {path.name}; that run is not saved.\n  {e}")
            return 1
        failures = 0
        data["question_no"] = q
        data["snapshot_sha256"] = snap["sha256"]
        save(path, data)
        print(f"  saved: {data['wall_time_s']} s, {len(data['tool_calls'])} tool calls")
    done_all = sum(run_path(a, b, c).exists() for a in MODELS for b in range(1, 11) for c in range(1, N_RUNS + 1))
    print(f"Done: {done_all}/{total} runs saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
