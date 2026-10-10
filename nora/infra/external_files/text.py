"""A shared file's bytes as text, and text as numbered parts
(design/external-files/00-design.md §2).

Vendor-neutral: any adapter that downloads a PDF, a Word, Excel or PowerPoint
file reads it here, and pages what it read with `pages`. Nothing is kept: each
function is a pure function of its input (the never-cached rule,
`tests/architecture/test_external_files_are_never_cached.py`, reads this
module too).

**The Office formats are read with the standard library** (`zipfile` and
`pyexpat`), not a parser per format: what is wanted is the words in order
(paragraphs, table rows, cells, slide text), a few element names per format,
and three dependencies for that would be three supply chains for one read.
PDF has no such shortcut, so it is `pypdf` (pure Python, no dependencies of
its own). Images, charts, comments and formatting are dropped (§9).

**Every count is bounded while it is being read, never after** (security
review of PR S2, 2026-10-10). The file is somebody else's and may be crafted:

- *Bytes in.* One `_Budget` of `max_bytes` covers EVERY ZIP member a read
  opens, together: members are read in chunks and the budget is charged with
  the bytes actually decompressed (a member's declared size is never
  trusted), so a zip bomb or a thousand small parts stop at the cap.
- *Text out.* A second budget, `max_chars`, is charged with every piece of
  text before it is kept, padding included: a spreadsheet cell placed at
  column 16,000 costs its 16,000 empty cells before one is allocated, and a
  column past Excel's last (XFD) is refused outright. A PDF is cut off at
  `MAX_PDF_PAGES` pages and at the same text budget.
- *XML.* Parsed with `pyexpat` directly, whose handlers refuse ANY document
  type declaration, entity declaration or external entity reference as the
  parser meets it (so the guard and the parser cannot disagree about
  encodings or spellings, as a byte pre-scan could). OOXML never declares
  one; the five predefined entities still read.

A breach of either budget is `ExternalFileError("refused", TOO_LARGE)`; any
other failure to parse is `("refused", NOT_TEXT)`: a file that cannot be read
as text is the user's answer, not an outage.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Callable
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, ExternalFileError

log = logging.getLogger("nora.external_files")

__all__ = [
    "MAX_PDF_PAGES", "MAX_TEXT_CHARS", "docx_text", "pages", "pdf_text",
    "plain_text", "pptx_text", "table_text", "xlsx_text",
]

#: The most text one read keeps, in characters, whatever the format. A part
#: is 12,000 (`google_drive.PART_CHARS`), so this is about 160 parts.
MAX_TEXT_CHARS = 2_000_000
#: The most pages of one PDF read as text.
MAX_PDF_PAGES = 2_000
#: Excel's last column (XFD); a cell reference past it is not a real sheet.
_MAX_COLUMNS = 16_384
#: Bytes read from a ZIP member per chunk.
_CHUNK = 64 * 1024


class _Budget:
    """A count that may not pass `limit`: `take(n)` charges `n` BEFORE the
    caller keeps or allocates it, and refuses once the total would exceed."""

    def __init__(self, limit: int) -> None:
        self.left = limit

    def take(self, n: int) -> None:
        if n > self.left:
            raise ExternalFileError("refused", TOO_LARGE)
        self.left -= n


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


def plain_text(data: bytes, max_chars: int = MAX_TEXT_CHARS) -> str:
    """A text file's bytes, as UTF-8 (a BOM dropped, a bad byte replaced)."""
    text = data.decode("utf-8-sig", errors="replace")
    _Budget(max_chars).take(len(text))
    return text


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def table_text(
    title: str, rows: list[list[object]], max_chars: int = MAX_TEXT_CHARS,
) -> str:
    """One tab of a spreadsheet as its name and a pipe table, one line per
    row. Empty trailing rows are dropped; an empty tab says so."""
    budget = _Budget(max_chars)
    lines = [f"## {title}"]
    budget.take(len(lines[0]))
    body: list[str] = []
    for row in rows:
        line = "| " + " | ".join(_cell(v) for v in row) + " |"
        budget.take(len(line) + 1)
        body.append(line)
    while body and not body[-1].replace("|", "").strip():
        body.pop()
    lines += body or ["(empty)"]
    return "\n".join(lines)


def pdf_text(data: bytes, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Each page's text under `## Page n`. An encrypted or unparseable PDF is
    `refused` (`NOT_TEXT`); more than `MAX_PDF_PAGES` pages or `max_chars`
    of text is `refused` (`TOO_LARGE`). pypdf bounds each decompressed
    stream itself (`pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH`)."""
    from pypdf import PdfReader  # deferred: only an open of a PDF pays the import

    budget = _Budget(max_chars)
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExternalFileError("refused", NOT_TEXT)
        out = []
        for n, page in enumerate(reader.pages, start=1):
            if n > MAX_PDF_PAGES:
                raise ExternalFileError("refused", TOO_LARGE)
            words = (page.extract_text() or "").strip() or "(no text on this page)"
            budget.take(len(words) + 16)
            out.append(f"## Page {n}\n{words}")
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


class _DtdError(Exception):
    """A document type, entity declaration or external entity was met."""


def _refuse(*_args: object) -> None:
    raise _DtdError


def _name(raw: str) -> str:
    """`uri}local` (expat's namespace form) as ElementTree's `{uri}local`."""
    return "{" + raw if "}" in raw else raw


class _Package:
    """An OOXML package. Every member read draws on ONE byte budget, charged
    with the bytes actually decompressed, chunk by chunk; every piece of text
    kept draws on one char budget (`chars`)."""

    def __init__(self, data: bytes, max_bytes: int, max_chars: int) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._bytes = _Budget(max_bytes)
        self.chars = _Budget(max_chars)

    def xml(self, name: str) -> Element | None:
        try:
            member = self._zip.open(name.lstrip("/"))
        except KeyError:
            return None
        builder = TreeBuilder()
        parser = expat.ParserCreate(namespace_separator="}")
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        parser.StartDoctypeDeclHandler = _refuse
        parser.EntityDeclHandler = _refuse
        parser.UnparsedEntityDeclHandler = _refuse
        parser.ExternalEntityRefHandler = _refuse  # type: ignore[assignment]
        parser.SkippedEntityHandler = _refuse
        parser.StartElementHandler = lambda tag, attrs: builder.start(
            _name(tag), {_name(k): v for k, v in attrs.items()},
        )
        parser.EndElementHandler = lambda tag: builder.end(_name(tag))
        parser.CharacterDataHandler = builder.data
        try:
            with member:
                while chunk := member.read(_CHUNK):
                    self._bytes.take(len(chunk))
                    parser.Parse(chunk, False)
                parser.Parse(b"", True)
        except _DtdError:
            raise ExternalFileError("refused", NOT_TEXT) from None
        return builder.close()

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


def _office(read: Callable[[_Package], str]) -> Callable[..., str]:
    def run(data: bytes, max_bytes: int, max_chars: int = MAX_TEXT_CHARS) -> str:
        try:
            return read(_Package(data, max_bytes, max_chars))
        except ExternalFileError:
            raise
        except Exception as exc:
            log.info("an Office file could not be read as text: %s", type(exc).__name__)
            raise ExternalFileError("refused", NOT_TEXT) from exc
    return run


def _w_text(p: Element) -> str:
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
            line = _w_text(el)
        elif el.tag == f"{_W}tbl":
            rows = []
            for tr in el.iter(f"{_W}tr"):
                cells = [
                    _cell(" ".join(_w_text(p) for p in tc.iter(f"{_W}p")))
                    for tc in tr.findall(f"{_W}tc")
                ]
                rows.append("| " + " | ".join(cells) + " |")
            line = "\n".join(rows)
        else:
            continue
        pkg.chars.take(len(line) + 1)
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


_COLUMN = re.compile(r"[A-Z]{1,3}")


def _column(ref: str) -> int:
    """A cell reference's 0-based column; `ExternalFileError` past XFD."""
    letters = _COLUMN.match(ref or "")
    n = 0
    for ch in letters.group(0) if letters else "":
        n = n * 26 + ord(ch) - 64
    if n > _MAX_COLUMNS:
        raise ExternalFileError("refused", NOT_TEXT)
    return max(n - 1, 0)


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
        lines = [f"## {sheet.get('name', 'Sheet')}"]
        pkg.chars.take(len(lines[0]) + 2)
        for row in root.iter(f"{_S}row") if root is not None else ():
            cells: list[str] = []
            for c in row.findall(f"{_S}c"):
                kind = c.get("t", "")
                v = c.findtext(f"{_S}v") or ""
                if kind == "s" and v.isdigit() and int(v) < len(shared):
                    value = shared[int(v)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{_S}t"))
                elif kind == "b":
                    value = "TRUE" if v == "1" else "FALSE"
                else:
                    value = v
                gap = _column(c.get("r", "")) - len(cells)
                # Charged BEFORE the padding exists: each empty cell is " | ".
                pkg.chars.take(3 * max(gap, 0) + len(value) + 3)
                if gap > 0:
                    cells += [""] * gap
                cells.append(_cell(value))
            lines.append("| " + " | ".join(cells) + " |")
        while len(lines) > 1 and not lines[-1].replace("|", "").strip():
            lines.pop()
        if len(lines) == 1:
            lines.append("(empty)")
        tabs.append("\n".join(lines))
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
        slide = f"## Slide {n}\n{text or '(no text on this slide)'}"
        pkg.chars.take(len(slide) + 2)
        slides.append(slide)
    return "\n\n".join(slides)


docx_text = _office(_docx)
xlsx_text = _office(_xlsx)
pptx_text = _office(_pptx)
