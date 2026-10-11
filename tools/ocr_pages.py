"""Turn scanned book pages into Markdown with a local OCR engine, and measure OCR accuracy.

Runs in its own environment (RapidOCR + PyMuPDF), separate from the project's dependencies:
    python -m venv <tools-dir>/venv && <tools-dir>/venv/Scripts/pip install rapidocr-onnxruntime pymupdf pillow numpy
    <tools-dir>/venv/Scripts/python tools/ocr_pages.py eval  <book.pdf> --pages 40-49
    <tools-dir>/venv/Scripts/python tools/ocr_pages.py run   <book.pdf> --out books_work/<slug>

`eval` compares OCR with the PDF's own text layer, so it only makes sense on books that have a
clean one; it is the ground truth that tells us whether OCR can be trusted on the scans.
`run` writes one Markdown file per page (resumable) plus a combined book.md. Spread pages (two book
pages per PDF page) are split into left and right halves so reading order stays correct.
Chess diagrams are not recognised; text that OCR finds inside them is low-confidence and dropped.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

MIN_CONFIDENCE = 0.6      # below this a fragment is usually diagram noise
DPI = 200

_engine = RapidOCR()


def render(page: "fitz.Page") -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def halves(img: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """A spread (clearly wider than tall) becomes a left and a right page."""
    h, w = img.shape
    if w > 1.15 * h:
        mid = w // 2
        return [("L", img[:, : mid + 10]), ("R", img[:, mid - 10:])]
    return [("", img)]


_BOARD_LABEL = re.compile(r"^(?:[1-8]|[a-h]{1,8})$")


def build_text(items: list[dict]) -> str:
    """Lines and paragraphs for one column, top to bottom."""
    if not items:
        return ""
    items = sorted(items, key=lambda i: (i["y"], i["x"]))
    lines, cur = [], [items[0]]
    for it in items[1:]:
        if abs(it["y"] - cur[-1]["y"]) < 0.6 * max(it["h"], cur[-1]["h"]):
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
    lines.append(cur)
    out, prev_y, med_h = [], None, float(np.median([i["h"] for i in items]))
    for ln in lines:
        ln.sort(key=lambda i: i["x"])
        y = sum(i["y"] for i in ln) / len(ln)
        if prev_y is not None and y - prev_y > 1.9 * med_h:
            out.append("")
        out.append(" ".join(i["t"] for i in ln))
        prev_y = y
    return "\n".join(out)


def split_columns(items: list[dict]) -> list[list[dict]]:
    """Two text columns when a vertical gap near the middle is crossed by almost no box."""
    if len(items) < 20:
        return [items]
    width = max(i["x2"] for i in items)
    best, best_cross = None, None
    for k in range(35, 66):
        cut = width * k / 100
        cross = sum(1 for i in items if i["x"] < cut < i["x2"])
        if best_cross is None or cross < best_cross:
            best, best_cross = cut, cross
    left = [i for i in items if (i["x"] + i["x2"]) / 2 < best]
    right = [i for i in items if (i["x"] + i["x2"]) / 2 >= best]
    if best_cross <= 0.03 * len(items) and len(left) >= 0.25 * len(items) and len(right) >= 0.25 * len(items):
        return [left, right]
    return [items]


def ocr_to_text(img: np.ndarray) -> str:
    """Recognise one page and rebuild columns, lines and paragraphs from the word boxes."""
    result, _ = _engine(img)
    if not result:
        return ""
    items = []
    for box, text, score in result:
        text = text.strip()
        if float(score) < MIN_CONFIDENCE or not text or _BOARD_LABEL.match(text):
            continue                       # diagram noise: low confidence, or board coordinates
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({"t": text, "x": min(xs), "x2": max(xs), "y": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys)})
    columns = split_columns(items)
    return "\n\n".join(t for t in (build_text(c) for c in columns) if t)


def page_markdown(page: "fitz.Page") -> str:
    parts = []
    for side, img in halves(render(page)):
        text = ocr_to_text(img)
        if text.strip():
            parts.append((f"<!-- {side} -->\n" if side else "") + text)
    return "\n\n".join(parts)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00ad", "").replace("-\n", "")).strip()


def cmd_eval(args) -> None:
    doc = fitz.open(args.book)
    lo, hi = (int(x) for x in args.pages.split("-"))
    sims, recalls, secs = [], [], []
    for n in range(lo, hi + 1):
        page = doc[n - 1]
        truth = norm(page.get_text("text"))
        if len(truth) < 300:
            continue
        t0 = time.perf_counter()
        got = norm(page_markdown(page).replace("<!--", " ").replace("-->", " "))
        secs.append(time.perf_counter() - t0)
        # Compare words, not characters: punctuation and layout order should not count as errors.
        tw = re.findall(r"[a-z0-9']+", truth.lower())
        gw = re.findall(r"[a-z0-9']+", got.lower())
        sims.append(difflib.SequenceMatcher(None, tw, gw, autojunk=False).ratio())
        want, have = Counter(tw), Counter(gw)
        recalls.append(sum(min(c, have[w]) for w, c in want.items()) / max(1, sum(want.values())))
        print(f"  page {n}: similarity {sims[-1]:.3f}, word recall {recalls[-1]:.3f}, {secs[-1]:.1f}s", flush=True)
    print(f"mean similarity {np.mean(sims):.3f} | mean word recall {np.mean(recalls):.3f} | {np.mean(secs):.1f}s/page "
          f"over {len(sims)} pages")


def cmd_run(args) -> None:
    out = Path(args.out)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    doc = fitz.open(args.book)
    start, end = args.start or 1, args.end or doc.page_count
    t0 = time.perf_counter()
    for n in range(start, end + 1):
        target = out / "pages" / f"p{n:04d}.md"
        if target.exists():
            continue
        target.write_text(page_markdown(doc[n - 1]), encoding="utf-8")
        if n % 10 == 0:
            print(f"page {n}/{end}  ({time.perf_counter() - t0:.0f}s)", flush=True)
    book = out / "book.md"
    with book.open("w", encoding="utf-8") as f:
        for n in range(1, doc.page_count + 1):
            p = out / "pages" / f"p{n:04d}.md"
            if p.exists():
                f.write(f"\n\n<!-- pdf page {n} -->\n\n{p.read_text(encoding='utf-8')}")
    print("done:", book)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval")
    e.add_argument("book")
    e.add_argument("--pages", default="40-49")
    r = sub.add_parser("run")
    r.add_argument("book")
    r.add_argument("--out", required=True)
    r.add_argument("--start", type=int)
    r.add_argument("--end", type=int)
    args = ap.parse_args()
    {"eval": cmd_eval, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main()
