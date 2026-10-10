"""`nora.infra.external_files.text` and the adapter's body read stay bounded on
crafted input: every count is charged while it is read, cumulatively, on the
bytes and characters actually held (security review of PR S2, 2026-10-10)."""

from __future__ import annotations

import gzip
import io
import struct
import zipfile

import pytest

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, ExternalFileError, recognise
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.external_files.text import docx_text, pdf_text, plain_text, xlsx_text
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


def test_a_column_past_excel_is_refused():
    data = _workbook({"Bad": '<row r="1"><c r="ZZZZZZZZ1"><v>1</v></c></row>'})
    _refused(lambda: xlsx_text(data, 10_000_000), NOT_TEXT)


def test_plain_text_and_pdf_text_are_bounded_too():
    _refused(lambda: plain_text(b"x" * 1000, max_chars=100), TOO_LARGE)
    from tests.unit.test_google_drive_source import _pdf

    _refused(lambda: pdf_text(_pdf("Lunch set 88"), max_chars=10), TOO_LARGE)


# ---- the HTTP body: counted raw, inflated bounded ----

ID = "1GzipBomb0000000000000000"


def _drive(data: bytes) -> tuple[FakeDriveServer, GoogleDriveSource]:
    server = FakeDriveServer(
        files={ID: DriveFile("Plan", GDOC, exports={"text/markdown": data})}, gzip_bodies=True,
    )
    source = GoogleDriveSource(
        client_id="cid", client_secret="cs",  # noqa: S106 - a test value
        refresh_token=server.refresh_token, metrics=Metrics(), transport=server.transport,
        max_bytes=1_000_000,
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
