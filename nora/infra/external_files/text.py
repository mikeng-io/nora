"""A shared file's bytes as text, and text as numbered parts.

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
its own). Images, charts, comments and formatting are dropped.

**Every count is bounded while it is being read, never after.**
The file is somebody else's and may be crafted:

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
- *Shape.* Each part is walked ONCE, as
  expat's events stream past (no tree, no `iter()` inside `iter()`, which
  is quadratic in nesting); an element deeper than `_MAX_DEPTH` refuses the
  file. A ZIP member compressed with anything but stored or deflate (bzip2,
  LZMA: inflated whole by `zipfile`, past the chunked budget) is refused
  before it is opened. A spreadsheet's tabs share ONE text budget.
- *Time.* Not bounded here: these functions run only inside the killable
  child of `parse`, under its wall-clock deadline and memory cap.

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
from xml.parsers import expat

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, ExternalFileError

log = logging.getLogger("nora.external_files")

__all__ = [
    "MAX_PDF_PAGES", "MAX_TEXT_CHARS", "docx_text", "pages", "pdf_text",
    "plain_text", "pptx_text", "sheets_text", "xlsx_text",
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


def sheets_text(
    tabs: list[tuple[str, list[object]]], max_chars: int = MAX_TEXT_CHARS,
) -> str:
    """Every tab of a spreadsheet, in order, each as its name and a pipe table,
    one line per row. ONE budget for the whole read (a
    budget per tab would let N tabs return N times the cap). Empty trailing rows are
    dropped; an empty tab says so."""
    budget = _Budget(max_chars)
    out = []
    for title, rows in tabs:
        lines = [f"## {title}"]
        budget.take(len(lines[0]) + 2)
        body: list[str] = []
        for row in rows:
            cells = row if isinstance(row, list) else []
            line = "| " + " | ".join(_cell(v) for v in cells) + " |"
            budget.take(len(line) + 1)
            body.append(line)
        while body and not body[-1].replace("|", "").strip():
            body.pop()
        lines += body or ["(empty)"]
        out.append("\n".join(lines))
    return "\n\n".join(out)


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
    except (ExternalFileError, MemoryError):
        raise
    except Exception as exc:
        log.info("a PDF could not be read as text: %s", type(exc).__name__)
        raise ExternalFileError("refused", NOT_TEXT) from exc
    return "\n\n".join(out)


# --- Office Open XML ---------------------------------------------------------
#
# ONE streaming pass per part, no tree (walking a tree with
# `iter()` inside `iter()` is quadratic in the nesting). Each reader is a small state machine fed
# expat's events; text is charged to the char budget as it arrives, and an element nested
# deeper than `_MAX_DEPTH` refuses the file.

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships}"
#: Real Office files nest a few dozen deep at most.
_MAX_DEPTH = 64
#: The only ZIP methods read. bzip2 and LZMA members are decompressed by
#: `zipfile` whole on each read, past any chunked budget, and Office never writes
#: them.
_ZIP_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class _DtdError(Exception):
    """A document type, entity declaration or external entity was met."""


def _refuse(*_args: object) -> None:
    raise _DtdError


class _Reader:
    """What a streaming reader implements. Tags arrive as `uri}local`."""

    def start(self, tag: str, attrs: dict[str, str]) -> None: ...

    def end(self, tag: str) -> None: ...

    def data(self, text: str) -> None: ...


class _Package:
    """An OOXML package. Every member read draws on ONE byte budget, charged
    with the bytes actually decompressed, chunk by chunk; every piece of text
    kept draws on one char budget (`chars`)."""

    def __init__(self, data: bytes, max_bytes: int, max_chars: int) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._bytes = _Budget(max_bytes)
        self.chars = _Budget(max_chars)

    def stream(self, name: str, reader: _Reader) -> bool:
        """Feed member `name` to `reader`; False when there is no such member."""
        try:
            info = self._zip.getinfo(name.lstrip("/"))
        except KeyError:
            return False
        if info.compress_type not in _ZIP_METHODS:
            raise ExternalFileError("refused", NOT_TEXT)
        depth = 0

        def start(tag: str, attrs: dict[str, str]) -> None:
            nonlocal depth
            depth += 1
            if depth > _MAX_DEPTH:
                raise ExternalFileError("refused", NOT_TEXT)
            reader.start(tag, attrs)

        def end(tag: str) -> None:
            nonlocal depth
            depth -= 1
            reader.end(tag)

        parser = expat.ParserCreate(namespace_separator="}")
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        parser.StartDoctypeDeclHandler = _refuse
        parser.EntityDeclHandler = _refuse
        parser.UnparsedEntityDeclHandler = _refuse
        parser.ExternalEntityRefHandler = _refuse  # type: ignore[assignment]
        parser.SkippedEntityHandler = _refuse
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = reader.data
        try:
            with self._zip.open(info) as member:
                while chunk := member.read(_CHUNK):
                    self._bytes.take(len(chunk))
                    parser.Parse(chunk, False)
                parser.Parse(b"", True)
        except _DtdError:
            raise ExternalFileError("refused", NOT_TEXT) from None
        return True

    def targets(self, rels: str, base: str) -> dict[str, str]:
        """Relationship id -> member name, from a `.rels` part."""
        reader = _Rels(base)
        self.stream(rels, reader)
        return reader.out


class _Rels(_Reader):
    def __init__(self, base: str) -> None:
        self.base = base
        self.out: dict[str, str] = {}

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_REL}Relationship":
            target = attrs.get("Target", "")
            self.out[attrs.get("Id", "")] = (
                target.lstrip("/") if target.startswith("/") else f"{self.base}/{target}"
            )


class _Text(_Reader):
    """Collects the character data inside `text_tag` elements, charged."""

    def __init__(self, chars: _Budget, text_tag: str) -> None:
        self.chars = chars
        self.text_tag = text_tag
        self.inside = 0
        self.buf: list[str] = []

    def data(self, text: str) -> None:
        if self.inside:
            self.chars.take(len(text))
            self.buf.append(text)

    def take(self) -> str:
        out = "".join(self.buf)
        self.buf = []
        return out


def _office(read: Callable[[_Package], str]) -> Callable[..., str]:
    def run(data: bytes, max_bytes: int, max_chars: int = MAX_TEXT_CHARS) -> str:
        try:
            return read(_Package(data, max_bytes, max_chars))
        except (ExternalFileError, MemoryError):
            # MemoryError is not "not text": it reaches the parse child,
            # which answers it as too large.
            raise
        except Exception as exc:
            log.info("an Office file could not be read as text: %s", type(exc).__name__)
            raise ExternalFileError("refused", NOT_TEXT) from exc
    return run


class _Docx(_Text):
    """Paragraphs as lines; a table row as `| a | b |`. A nested table's
    paragraphs join the cell they sit in, flattened."""

    def __init__(self, chars: _Budget) -> None:
        super().__init__(chars, f"{_W}t")
        self.lines: list[str] = []
        self.cells: list[list[str]] = []   # one row per open table row
        self.cell: list[list[str]] = []    # paragraphs of the open cell, per row

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == self.text_tag:
            self.inside += 1
        elif tag in (f"{_W}tab",):
            self.data_always("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            self.data_always("\n")
        elif tag == f"{_W}tr":
            self.cells.append([])
        elif tag == f"{_W}tc":
            self.cell.append([])

    def data_always(self, text: str) -> None:
        self.chars.take(len(text))
        self.buf.append(text)

    def end(self, tag: str) -> None:
        if tag == self.text_tag:
            self.inside = max(0, self.inside - 1)
        elif tag == f"{_W}p":
            text = self.take()
            if self.cell:
                self.cell[-1].append(text)
            else:
                self.lines.append(text)
        elif tag == f"{_W}tc" and self.cell:
            done = _cell(" ".join(self.cell.pop()))
            if self.cells:
                self.cells[-1].append(done)
        elif tag == f"{_W}tr" and self.cells:
            line = "| " + " | ".join(self.cells.pop()) + " |"
            self.chars.take(len(line) + 1)
            if self.cell:
                self.cell[-1].append(line)
            else:
                self.lines.append(line)


def _docx(pkg: _Package) -> str:
    reader = _Docx(pkg.chars)
    if not pkg.stream("word/document.xml", reader):
        raise ExternalFileError("refused", NOT_TEXT)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(reader.lines))


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


class _Shared(_Text):
    def __init__(self, chars: _Budget) -> None:
        super().__init__(chars, f"{_S}t")
        self.strings: list[str] = []

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == self.text_tag:
            self.inside += 1

    def end(self, tag: str) -> None:
        if tag == self.text_tag:
            self.inside = max(0, self.inside - 1)
        elif tag == f"{_S}si":
            self.strings.append(self.take())


class _Book(_Reader):
    def __init__(self) -> None:
        self.sheets: list[tuple[str, str]] = []

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_S}sheet":
            self.sheets.append((attrs.get("name", "Sheet"), attrs.get(f"{_R}id", "")))


class _Sheet(_Reader):
    """One worksheet: each `<c>` becomes a cell at its column, its padding
    charged before it is allocated; each row's line is charged in full when it
    is made, less what its cells already paid (otherwise a row's
    `| ... |` would go uncharged, and millions of empty rows would add up)."""

    def __init__(self, chars: _Budget, shared: list[str]) -> None:
        self.chars = chars
        self.shared = shared
        self.lines: list[str] = []
        self.row: list[str] = []
        self.kind = ""
        self.col = 0
        self.inside = False
        self.buf: list[str] = []
        self.paid = 0  # chars this row's cells have already charged

    def _take(self, n: int) -> None:
        self.chars.take(n)
        self.paid += n

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_S}row":
            self.row = []
            self.paid = 0
        elif tag == f"{_S}c":
            self.kind = attrs.get("t", "")
            self.col = _column(attrs.get("r", ""))
            self.buf = []
        elif tag in (f"{_S}v", f"{_S}t"):
            self.inside = True

    def data(self, text: str) -> None:
        if self.inside:
            self._take(len(text))
            self.buf.append(text)

    def end(self, tag: str) -> None:
        if tag in (f"{_S}v", f"{_S}t"):
            self.inside = False
        elif tag == f"{_S}c":
            v = "".join(self.buf)
            if self.kind == "s":
                value = self.shared[int(v)] if v.isdigit() and int(v) < len(self.shared) else ""
            elif self.kind == "b":
                value = "TRUE" if v == "1" else "FALSE"
            else:
                value = v
            gap = self.col - len(self.row)
            # Charged BEFORE the padding exists: each empty cell is " | ".
            # Inline text was charged as it streamed; a shared string is
            # charged at every use, since each use repeats it in the output.
            shared_cost = len(value) if self.kind in ("s", "b") else 0
            self._take(3 * max(gap, 0) + shared_cost + 3)
            if gap > 0:
                self.row += [""] * gap
            self.row.append(_cell(value))
        elif tag == f"{_S}row":
            line = "| " + " | ".join(self.row) + " |"
            self.chars.take(max(len(line) + 1 - self.paid, 0))
            self.lines.append(line)
            self.row = []
            self.paid = 0


def _xlsx(pkg: _Package) -> str:
    book = _Book()
    if not pkg.stream("xl/workbook.xml", book):
        raise ExternalFileError("refused", NOT_TEXT)
    targets = pkg.targets("xl/_rels/workbook.xml.rels", "xl")
    shared = _Shared(pkg.chars)
    pkg.stream("xl/sharedStrings.xml", shared)
    tabs = []
    for name, rel in book.sheets:
        sheet = _Sheet(pkg.chars, shared.strings)
        member = targets.get(rel)
        if member:
            pkg.stream(member, sheet)
        lines = sheet.lines
        while lines and not lines[-1].replace("|", "").strip():
            lines.pop()
        pkg.chars.take(len(name) + 4)
        tabs.append("\n".join([f"## {name}", *(lines or ["(empty)"])]))
    return "\n\n".join(tabs)


class _Deck(_Reader):
    def __init__(self) -> None:
        self.slides: list[str] = []

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_P}sldId":
            self.slides.append(attrs.get(f"{_R}id", ""))


class _Slide(_Text):
    def __init__(self, chars: _Budget) -> None:
        super().__init__(chars, f"{_A}t")
        self.lines: list[str] = []

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == self.text_tag:
            self.inside += 1

    def end(self, tag: str) -> None:
        if tag == self.text_tag:
            self.inside = max(0, self.inside - 1)
        elif tag == f"{_A}p":
            line = self.take()
            if line.strip():
                self.lines.append(line)


def _pptx(pkg: _Package) -> str:
    deck = _Deck()
    if not pkg.stream("ppt/presentation.xml", deck):
        raise ExternalFileError("refused", NOT_TEXT)
    targets = pkg.targets("ppt/_rels/presentation.xml.rels", "ppt")
    slides = []
    for n, rel in enumerate(deck.slides, start=1):
        slide = _Slide(pkg.chars)
        member = targets.get(rel)
        if member:
            pkg.stream(member, slide)
        text = "\n".join(slide.lines)
        pkg.chars.take(16)
        slides.append(f"## Slide {n}\n{text or '(no text on this slide)'}")
    return "\n\n".join(slides)


docx_text = _office(_docx)
xlsx_text = _office(_xlsx)
pptx_text = _office(_pptx)
