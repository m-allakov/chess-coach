"""Convert an EPUB into one Markdown file plus the images it uses (standard library only).

    python tools/epub_to_markdown.py <book.epub> --out books_work/<slug>

Reading order follows the EPUB spine. Headings, paragraphs, lists, bold and italic are kept;
every image is extracted and referenced as ![](images/<name>) so chess diagrams stay
available for later processing. Output goes to a private working folder (git-ignored).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "o": "http://www.idpf.org/2007/opf"}
BLOCK = {"p", "div", "li", "blockquote", "tr", "section", "article", "table", "ul", "ol"}


class Converter(HTMLParser):
    def __init__(self, image_names: dict[str, str], base: str):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.image_names = image_names
        self.base = base
        self.skip = 0

    def text(self) -> str:
        s = "".join(self.out)
        s = re.sub(r"[ \t]+\n", "\n", s)
        return re.sub(r"\n{3,}", "\n\n", s).strip()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in BLOCK:
            self.out.append("\n\n" + ("- " if tag == "li" else ""))
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "img":
            src = posixpath.normpath(posixpath.join(self.base, a.get("src", "")))
            name = self.image_names.get(src)
            if name:
                self.out.append(f"\n\n![{a.get('alt', '')}](images/{name})\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) or tag in BLOCK:
            self.out.append("\n\n")
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def spine_files(z: zipfile.ZipFile) -> tuple[list[str], str]:
    root = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = root.find(".//c:rootfile", NS).attrib["full-path"]
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest = {i.attrib["id"]: i.attrib["href"] for i in opf.findall(".//o:manifest/o:item", NS)}
    order = [manifest[r.attrib["idref"]] for r in opf.findall(".//o:spine/o:itemref", NS) if r.attrib["idref"] in manifest]
    return [posixpath.normpath(posixpath.join(base, h)) for h in order], base


def convert(epub: Path, out: Path) -> dict:
    (out / "images").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub) as z:
        files, _ = spine_files(z)
        names = set(z.namelist())
        image_names: dict[str, str] = {}
        for n in sorted(names):
            if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                flat = re.sub(r"[^A-Za-z0-9._-]+", "_", n)
                image_names[n] = flat
                (out / "images" / flat).write_bytes(z.read(n))
        parts = []
        for f in files:
            if f not in names:
                continue
            conv = Converter(image_names, posixpath.dirname(f))
            conv.feed(z.read(f).decode("utf-8", "replace"))
            body = conv.text()
            if body:
                parts.append(f"<!-- {f} -->\n\n{body}")
    (out / "book.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return {"sections": len(parts), "images": len(image_names), "chars": sum(len(p) for p in parts)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("epub")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    print(convert(Path(args.epub), Path(args.out)))


if __name__ == "__main__":
    main()
