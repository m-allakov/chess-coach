"""Validate principle files and (optionally) check them against the private source text.

    python tools/validate_principles.py docs/pedagogy/principles/*.jsonl
    python tools/validate_principles.py docs/pedagogy/principles/x.jsonl --against books_work/<slug>/book.md

With --against, any claim that shares a run of 8 or more consecutive words with the book is
reported: claims must be in the project's own words. Exit status is non-zero on any problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chess_coach import principles  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--against", help="private Markdown/text of the source book")
    ap.add_argument("--run", type=int, default=8, help="shared-word-run length that counts as copying")
    args = ap.parse_args()

    sources = json.loads((ROOT / "docs/pedagogy/sources.json").read_text(encoding="utf-8"))
    books = {b["key"] for b in sources["books"]}
    text = Path(args.against).read_text(encoding="utf-8") if args.against else None
    bad = 0
    for f in args.files:
        path = Path(f)
        problems = principles.validate_file(path, books)
        if text:
            for rec in principles.load(path):
                run = principles.shared_run(rec["claim"], text, args.run)
                if run:
                    problems.setdefault(rec["id"], []).append(f"copies {args.run}+ words from the source: '{run}'")
        count = len(principles.load(path))
        print(f"{path.name}: {count} records, {len(problems)} with problems")
        for rid, errs in problems.items():
            bad += 1
            for e in errs:
                print(f"  {rid}: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
