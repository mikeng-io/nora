"""A shared file's bytes as text, and text as numbered parts
(design/external-files/00-design.md §2).

Vendor-neutral: any adapter that downloads a PDF, a Word, Excel or PowerPoint
file reads it here, and pages what it read with `pages`. Nothing is kept: each
function is a pure function of its input (the never-cached rule,
`tests/architecture/test_external_files_are_never_cached.py`, reads this
module too).

**The Office formats are read with the standard library**, `zipfile` and
`ElementTree`, not a parser per format: what is wanted is the words in order
(paragraphs, table rows, cells, slide text), which is a few element names per
format, and three dependencies for that would be three supply chains for one
read. PDF has no such shortcut, so it is `pypdf` (pure Python, no dependencies
of its own). Images, charts, comments and formatting are dropped (§9).

**Bounded.** A ZIP member is read at most `max_bytes` uncompressed (a small
archive can expand without limit), and every failure to parse is
`ExternalFileError("refused", NOT_TEXT)`: a file that cannot be read as text
is the user's answer, not an outage.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Callable
from xml.etree import ElementTree as ET

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, ExternalFileError

log = logging.getLogger("nora.external_files")

__all__ = [
    "docx_text", "pages", "pdf_text", "plain_text", "pptx_text", "table_text",
    "xlsx_text",
]


def pages(text: str, part_chars: int) -> list[str]:
    """`text` cut into parts of at most `part_chars` characters, at a blank
    line when one falls in the second half of the part, else at a line
    break, else mid-line. Never empty: no text is one empty part."""
    text = text.strip()
    out: list[str] = []
    while len(text) > part_chars:
        cut = text.rfind("\n\n", 0, part_chars + 1)
        if cut < part_chars // 2:
            cut = text.rfind("\n", 0, part_chars + 1)
        if cut <= 0:
            cut = part_chars
        out.append(text[:cut].rstrip("\n"))
        text = text[cut:].lstrip("\n")
    out.append(text)
    return out


def plain_text(data: bytes) -> str:
    """A text file's bytes, as UTF-8 (a BOM dropped, a bad byte replaced)."""
    return data.decode("utf-8-sig", errors="replace")


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def table_text(title: str, rows: list[list[object]]) -> str:
    """One tab of a spreadsheet as its name and a pipe table, one line per
    row. Empty trailing rows are dropped; an empty tab says so."""
    lines = [f"## {title}"]
    body = [[_cell(v) for v in row] for row in rows]
    while body and not any(body[-1]):
        body.pop()
    if not body:
        lines.append("(empty)")
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def pdf_text(data: bytes) -> str:
    """Each page's text under `## Page n`. An encrypted or unparseable PDF is
    `refused` (`NOT_TEXT`); a scanned PDF has pages with no text, said so."""
    from pypdf import PdfReader  # deferred: only an open of a PDF pays the import

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExternalFileError("refused", NOT_TEXT)
        out = []
        for n, page in enumerate(reader.pages, start=1):
            words = (page.extract_text() or "").strip()
            out.append(f"## Page {n}\n{words or '(no text on this page)'}")
    except ExternalFileError:
        raise
    except Exception as exc:
        log.info("a PDF could not be read as text: %s", type(exc).__name__)
        raise ExternalFileError("refused", NOT_TEXT) from exc
    return "\n\n".join(out)


# --- Office Open XML ---------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class _Package:
    """An OOXML package: its members by name, each read at most `max_bytes`
    uncompressed."""

    def __init__(self, data: bytes, max_bytes: int) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._max = max_bytes

    def xml(self, name: str) -> ET.Element | None:
        try:
            with self._zip.open(name.lstrip("/")) as member:
                raw = member.read(self._max + 1)
        except KeyError:
            return None
        if len(raw) > self._max:
            raise ExternalFileError("refused", TOO_LARGE)
        if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw:
            # OOXML never declares one; a part that does is not an Office
            # file, and refusing it closes entity expansion without a
            # second XML library.
            raise ExternalFileError("refused", NOT_TEXT)
        return ET.fromstring(raw)  # noqa: S314 - no DTD reaches here (above)

    def targets(self, rels: str, base: str) -> dict[str, str]:
        """Relationship id -> member name, from a `.rels` part."""
        root = self.xml(rels)
        out: dict[str, str] = {}
        for rel in root.iter(f"{_REL}Relationship") if root is not None else ():
            target = rel.get("Target", "")
            out[rel.get("Id", "")] = (
                target.lstrip("/") if target.startswith("/") else f"{base}/{target}"
            )
        return out


def _office(read: Callable[[_Package], str]) -> Callable[[bytes, int], str]:
    def run(data: bytes, max_bytes: int) -> str:
        try:
            return read(_Package(data, max_bytes))
        except ExternalFileError:
            raise
        except Exception as exc:
            log.info("an Office file could not be read as text: %s", type(exc).__name__)
            raise ExternalFileError("refused", NOT_TEXT) from exc
    return run


def _w_text(p: ET.Element) -> str:
    out = []
    for node in p.iter():
        if node.tag == f"{_W}t" and node.text:
            out.append(node.text)
        elif node.tag == f"{_W}tab":
            out.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _docx(pkg: _Package) -> str:
    root = pkg.xml("word/document.xml")
    body = root.find(f"{_W}body") if root is not None else None
    if body is None:
        raise ExternalFileError("refused", NOT_TEXT)
    lines = []
    for el in body:
        if el.tag == f"{_W}p":
            lines.append(_w_text(el))
        elif el.tag == f"{_W}tbl":
            for tr in el.iter(f"{_W}tr"):
                cells = [
                    _cell(" ".join(_w_text(p) for p in tc.iter(f"{_W}p")))
                    for tc in tr.findall(f"{_W}tc")
                ]
                lines.append("| " + " | ".join(cells) + " |")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


_COLUMN = re.compile(r"[A-Z]+")


def _column(ref: str) -> int:
    letters = _COLUMN.match(ref or "")
    n = 0
    for ch in letters.group(0) if letters else "":
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(pkg: _Package) -> str:
    book = pkg.xml("xl/workbook.xml")
    if book is None:
        raise ExternalFileError("refused", NOT_TEXT)
    targets = pkg.targets("xl/_rels/workbook.xml.rels", "xl")
    shared_root = pkg.xml("xl/sharedStrings.xml")
    shared = [
        "".join(t.text or "" for t in si.iter(f"{_S}t"))
        for si in (shared_root.findall(f"{_S}si") if shared_root is not None else ())
    ]
    tabs = []
    for sheet in book.iter(f"{_S}sheet"):
        member = targets.get(sheet.get(f"{_R}id", ""))
        root = pkg.xml(member) if member else None
        rows: list[list[object]] = []
        for row in root.iter(f"{_S}row") if root is not None else ():
            values: list[object] = []
            for c in row.findall(f"{_S}c"):
                col = _column(c.get("r", ""))
                kind = c.get("t", "")
                v = c.findtext(f"{_S}v") or ""
                if kind == "s" and v.isdigit() and int(v) < len(shared):
                    value: object = shared[int(v)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{_S}t"))
                elif kind == "b":
                    value = "TRUE" if v == "1" else "FALSE"
                else:
                    value = v
                if col > len(values):
                    values += [""] * (col - len(values))
                values.append(value)
            rows.append(values)
        tabs.append(table_text(sheet.get("name", "Sheet"), rows))
    return "\n\n".join(tabs)


def _pptx(pkg: _Package) -> str:
    deck = pkg.xml("ppt/presentation.xml")
    if deck is None:
        raise ExternalFileError("refused", NOT_TEXT)
    targets = pkg.targets("ppt/_rels/presentation.xml.rels", "ppt")
    slides = []
    for n, sld in enumerate(deck.iter(f"{_P}sldId"), start=1):
        member = targets.get(sld.get(f"{_R}id", ""))
        root = pkg.xml(member) if member else None
        lines = [
            "".join(t.text or "" for t in p.iter(f"{_A}t"))
            for p in (root.iter(f"{_A}p") if root is not None else ())
        ]
        text = "\n".join(line for line in lines if line.strip())
        slides.append(f"## Slide {n}\n{text or '(no text on this slide)'}")
    return "\n\n".join(slides)


docx_text = _office(_docx)
xlsx_text = _office(_xlsx)
pptx_text = _office(_pptx)
