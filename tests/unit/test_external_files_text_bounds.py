"""`nora.infra.external_files.text` and the adapter's body read stay bounded on
crafted input: every count is charged while it is read, cumulatively, on the
bytes and characters actually held."""

from __future__ import annotations

import gzip
import io
import struct
import time
import zipfile

import pytest

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, ExternalFileError, recognise
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.external_files.text import (
    docx_text,
    pdf_text,
    plain_text,
    pptx_text,
    sheets_text,
    xlsx_text,
)
from nora.infra.google.auth import GoogleCredentials
from nora.obs.metrics import Metrics
from nora.testing.google_drive import GDOC, DriveFile, FakeDriveServer

S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _zip(members: dict[str, bytes | str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


def _refused(call, reason: str) -> None:
    with pytest.raises(ExternalFileError) as raised:
        call()
    assert (raised.value.status, raised.value.reason) == ("refused", reason)


def _workbook(sheets: dict[str, str]) -> bytes:
    names = list(sheets)
    book = "".join(
        f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Target="worksheets/s{i}.xml"/>'
        for i in range(1, len(names) + 1)
    )
    members: dict[str, bytes | str] = {
        "xl/workbook.xml": f"<workbook {S} {R}><sheets>{book}</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": f"{RELS}{rels}</Relationships>",
    }
    for i, n in enumerate(names, 1):
        members[f"xl/worksheets/s{i}.xml"] = (
            f"<worksheet {S}><sheetData>{sheets[n]}</sheetData></worksheet>"
        )
    return _zip(members)


# ---- XML: a DTD is refused by the parser itself, however it is spelled ----

_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
    ']><w:document ' + W + '><w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>'
)


@pytest.mark.parametrize("raw", [
    _LAUGHS.encode(),
    # UTF-16 with a BOM: a byte scan for "<!DOCTYPE" sees no such bytes.
    _LAUGHS.encode("utf-16"),
    # An external entity, and a parameter entity.
    ('<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><w:document ' + W
     + '><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>').encode(),
    ('<!DOCTYPE d [<!ENTITY % p SYSTEM "http://example.invalid/x">%p;]><w:document '
     + W + "/>").encode(),
])
def test_any_dtd_in_a_part_is_refused_by_the_parser(raw):
    _refused(lambda: docx_text(_zip({"word/document.xml": raw}), 10_000_000), NOT_TEXT)


def test_a_dtd_in_a_nested_part_is_refused_too():
    data = _workbook({"Tab": '<row r="1"><c r="A1"><v>1</v></c></row>'})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    members["xl/worksheets/s1.xml"] = _LAUGHS.encode("utf-16")
    _refused(lambda: xlsx_text(_zip(members), 10_000_000), NOT_TEXT)


def test_the_predefined_entities_still_read():
    raw = (f"<w:document {W}><w:body><w:p><w:r><w:t>A &amp; B &lt;3</w:t></w:r></w:p>"
           "</w:body></w:document>")
    assert docx_text(_zip({"word/document.xml": raw}), 10_000) == "A & B <3"


# ---- bytes: one budget across every member, on bytes actually inflated ----

def test_a_zip_bomb_member_stops_at_the_cap():
    bomb = _zip({"word/document.xml": b"<w:document " + W.encode() + b">" + b" " * 30_000_000})
    assert len(bomb) < 100_000
    _refused(lambda: docx_text(bomb, 1_000_000), TOO_LARGE)


def test_many_small_parts_summing_past_the_cap_are_refused():
    """Each sheet is far under the cap; together they are over it. A cap
    per member reads all twenty."""
    row = '<row r="1"><c r="A1" t="inlineStr"><is><t>' + "x" * 50_000 + "</t></is></c></row>"
    data = _workbook({f"T{i}": row for i in range(20)})
    _refused(lambda: xlsx_text(data, 500_000), TOO_LARGE)
    assert xlsx_text(data, 5_000_000).count("## T") == 20


def test_a_member_lying_about_its_size_is_not_trusted():
    """The central directory says 10 bytes; the member inflates to far more.
    It is read to its declared size and fails its check, never past the cap."""
    data = bytearray(_zip({"word/document.xml": b"<x>" + b" " * 2_000_000 + b"</x>"}))
    cd = data.rindex(b"PK\x01\x02")
    data[cd + 24:cd + 28] = struct.pack("<I", 10)
    with pytest.raises(ExternalFileError) as raised:
        docx_text(bytes(data), 100_000)
    assert raised.value.status == "refused"


# ---- characters: charged before they are kept, padding included ----

def test_a_cell_at_the_last_column_costs_its_padding_before_it_exists():
    rows = "".join(f'<row r="{i}"><c r="XFD{i}"><v>1</v></c></row>' for i in range(1, 200))
    data = _workbook({"Wide": rows})
    _refused(lambda: xlsx_text(data, 10_000_000, max_chars=100_000), TOO_LARGE)
    # The row never closes: only a charge at the CELL (before the padding is
    # allocated) can refuse it as too large; a charge at the row's end never
    # comes, and the broken XML reads as not text.
    open_row = _workbook({"Wide": '<row r="1"><c r="XFD1"><v>1</v></c>'})
    _refused(lambda: xlsx_text(open_row, 10_000_000, max_chars=10_000), TOO_LARGE)


def test_empty_rows_are_charged_for_the_lines_they_make():
    """A row's `| ... |` is charged: 1.5M empty rows (13.9 KB zipped) must not
    make 7.5M chars past a 2M cap."""
    rows = '<row r="1"/>' * 1_500_000
    data = _workbook({"Empty": rows})
    assert len(data) < 100_000
    _refused(lambda: xlsx_text(data, 100_000_000), TOO_LARGE)


def test_a_full_row_is_charged_once_not_twice():
    rows = "".join(
        f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>abcd</t></is></c></row>'
        for i in range(1, 1001)
    )
    text = xlsx_text(_workbook({"T": rows}), 10_000_000, max_chars=12_000)
    assert text.count("| abcd |") == 1000  # 9 chars a line + the header


def test_out_of_memory_in_a_parser_is_not_called_not_text(monkeypatch):
    """The parsers' catch-all must not turn a MemoryError into `not_text`
    ("a picture, a video, an archive"). It propagates, and the parse child
    answers it as too large."""
    import pypdf

    from nora.infra.external_files import text as text_mod

    def oom(*_a, **_k):
        raise MemoryError

    monkeypatch.setattr(text_mod.zipfile, "ZipFile", oom)
    with pytest.raises(MemoryError):
        docx_text(b"PK", 1000)
    monkeypatch.setattr(pypdf, "PdfReader", oom)
    with pytest.raises(MemoryError):
        pdf_text(b"%PDF-1.4", 1000)


def test_the_parse_child_answers_out_of_memory_as_too_large(monkeypatch, capsys):
    import json as _json
    import sys as _sys

    from nora.infra.external_files import parse

    def oom(*_a, **_k):
        raise MemoryError

    header = _json.dumps({"kind": "pdf", "max_bytes": 10, "max_chars": 10, "titles": [],
                          "memory_bytes": 1 << 30, "cpu_seconds": 30}).encode()
    monkeypatch.setattr(_sys, "stdin", type("S", (), {"buffer": io.BytesIO(header + b"\n%PDF")})())
    monkeypatch.setattr(parse, "_limit", lambda *_a: None)
    monkeypatch.setattr(parse, "_parse", oom)
    parse.main()
    assert _json.loads(capsys.readouterr().out) == {"status": "refused", "reason": TOO_LARGE}


def test_a_column_past_excel_is_refused():
    data = _workbook({"Bad": '<row r="1"><c r="ZZZZZZZZ1"><v>1</v></c></row>'})
    _refused(lambda: xlsx_text(data, 10_000_000), NOT_TEXT)


def test_plain_text_and_pdf_text_are_bounded_too():
    _refused(lambda: plain_text(b"x" * 1000, max_chars=100), TOO_LARGE)
    from tests.unit.test_google_drive_source import _pdf

    _refused(lambda: pdf_text(_pdf("Lunch set 88"), max_chars=10), TOO_LARGE)


# ---- ZIP methods, nesting, one Sheets budget ----

@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_a_bzip2_or_lzma_member_is_refused_before_it_is_opened(method):
    """`zipfile` inflates these whole on a read, past any chunked budget (a
    697-byte bzip2 docx peaks at 1.12 GB). Office never writes them."""
    xml = (
        f"<w:document {W}><w:body><w:p><w:r><w:t>" + "A" * 5_000_000
        + "</w:t></w:r></w:p></w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as z:
        z.writestr("word/document.xml", xml)
    _refused(lambda: docx_text(out.getvalue(), 10_000_000), NOT_TEXT)


def _nested_docx(n: int) -> bytes:
    body = (
        "<w:tbl><w:tr><w:tc>" * n + "<w:p><w:r><w:t>deep</w:t></w:r></w:p>"
        + "</w:tc></w:tr></w:tbl>" * n
    )
    return _zip({"word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>"})


def test_nesting_past_the_depth_cap_is_refused():
    """Tables in tables 20,000 deep: refused at the start tag that passes the
    cap, before any of it is walked."""
    started = time.monotonic()
    _refused(lambda: docx_text(_nested_docx(20_000), 10_000_000), NOT_TEXT)
    assert time.monotonic() - started < 1


def test_nesting_inside_the_cap_still_reads():
    assert "deep" in docx_text(_nested_docx(15), 10_000_000)


def test_slides_nested_past_the_cap_are_refused():
    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    data = _zip({
        "ppt/presentation.xml": f'<p:presentation {p} {R}><p:sldIdLst><p:sldId r:id="rId1"/>'
                                "</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": f'{RELS}<Relationship Id="rId1" Target="slides/s1.xml"/>'
                                           "</Relationships>",
        "ppt/slides/s1.xml": f"<p:sld {p} {a}>" + "<a:p>" * 16_000 + "</a:p>" * 16_000 + "</p:sld>",
    })
    _refused(lambda: pptx_text(data, 10_000_000), NOT_TEXT)


def test_the_sheets_budget_is_one_for_every_tab_together():
    """Each tab is far under the cap; together they are over it. A budget per
    tab returned N times the cap."""
    tab = [["x" * 50_000]]
    _refused(lambda: sheets_text([(f"T{i}", tab) for i in range(20)], 500_000), TOO_LARGE)
    assert sheets_text([(f"T{i}", tab) for i in range(20)], 5_000_000).count("## T") == 20


# ---- the HTTP body: counted raw, inflated bounded ----

ID = "1GzipBomb0000000000000000"


def _drive(data: bytes) -> tuple[FakeDriveServer, GoogleDriveSource]:
    server = FakeDriveServer(
        files={ID: DriveFile("Plan", GDOC, exports={"text/markdown": data})}, gzip_bodies=True,
    )
    source = GoogleDriveSource(
        credentials=GoogleCredentials(
            client_id="cid", client_secret="cs",  # noqa: S106 - a test value
            refresh_token=server.refresh_token, metrics=Metrics(), transport=server.transport,
        ),
        transport=server.transport, max_bytes=1_000_000,
    )
    return server, source


async def test_a_gzip_body_is_inflated_and_read():
    _server, source = _drive(b"# Plan\n\nhello")
    ref = recognise(f"https://docs.google.com/document/d/{ID}/edit")
    assert (await source.read(ref=ref, part=1)).text == "# Plan\n\nhello"


async def test_a_gzip_bomb_body_stops_at_the_cap():
    data = b"z" * 50_000_000
    assert len(gzip.compress(data)) < 200_000
    _server, source = _drive(data)
    ref = recognise(f"https://docs.google.com/document/d/{ID}/edit")
    with pytest.raises(ExternalFileError) as raised:
        await source.read(ref=ref, part=1)
    assert (raised.value.status, raised.value.reason) == ("refused", TOO_LARGE)


async def test_every_request_asks_for_an_uncompressed_body():
    server, source = _drive(b"x")
    await source.read(ref=recognise(f"https://docs.google.com/document/d/{ID}/edit"), part=1)
    api = [r for r in server.requests if r.url.host != "oauth2.googleapis.com"]
    assert api and all(r.headers["Accept-Encoding"] == "identity" for r in api)
