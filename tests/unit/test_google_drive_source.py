"""`GoogleDriveSource` against `FakeDriveServer` (an `httpx.MockTransport`, no
network): every kind, paging, the refusals, the token and the resource key."""

from __future__ import annotations

import io
import logging
import zipfile

import pytest

from nora.domain.external_files import (
    DELETED,
    NO_DOWNLOAD,
    NO_PART,
    NOT_SHARED,
    NOT_TEXT,
    TOO_LARGE,
    ExternalFileError,
    ExternalFileRef,
    recognise,
)
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.external_files.text import pages
from nora.infra.google.auth import GoogleCredentials
from nora.obs.metrics import Metrics
from nora.testing.google_drive import GDOC, GSHEET, GSLIDES, DriveFile, FakeDriveServer

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SECRET = "client-secret-xyz"  # noqa: S105 - a test value


def _ref(file_id: str, key: str | None = None) -> ExternalFileRef:
    url = f"https://drive.google.com/file/d/{file_id}/view"
    if key:
        url += f"?resourcekey={key}"
    ref = recognise(url)
    assert ref is not None
    return ref


def _credentials(server: FakeDriveServer, metrics: Metrics | None = None) -> GoogleCredentials:
    return GoogleCredentials(
        client_id="cid", client_secret=SECRET, refresh_token=server.refresh_token,
        metrics=metrics or Metrics(), transport=server.transport,
    )


def _source(server: FakeDriveServer, metrics: Metrics | None = None, **kw) -> GoogleDriveSource:
    return GoogleDriveSource(
        credentials=_credentials(server, metrics), transport=server.transport, **kw,
    )


def _zip(members: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return out.getvalue()


_RELS = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx() -> bytes:
    return _zip({"word/document.xml": (
        f"<w:document {_W}><w:body>"
        "<w:p><w:r><w:t>Trip plan</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Day</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Place</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:body></w:document>"
    )})


def _xlsx() -> bytes:
    s = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return _zip({
        "xl/workbook.xml": f'<workbook {s} {r}><sheets>'
                           '<sheet name="Budget" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'{_RELS}<Relationship Id="rId1" '
                                      'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f"<sst {s}><si><t>Hotel</t></si></sst>",
        "xl/worksheets/sheet1.xml": f'<worksheet {s}><sheetData><row r="1">'
                                    '<c r="A1" t="s"><v>0</v></c><c r="C1"><v>1200</v></c>'
                                    "</row></sheetData></worksheet>",
    })


def _pptx() -> bytes:
    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return _zip({
        "ppt/presentation.xml": f'<p:presentation {p} {r}><p:sldIdLst>'
                                '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/>'
                                "</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": f'{_RELS}<Relationship Id="rId2" '
                                           'Target="slides/slide1.xml"/><Relationship '
                                           'Id="rId3" Target="slides/slide2.xml"/>'
                                           "</Relationships>",
        "ppt/slides/slide1.xml": f"<p:sld {p} {a}><a:p><a:r><a:t>Welcome</a:t></a:r></a:p></p:sld>",
        "ppt/slides/slide2.xml": f"<p:sld {p} {a}><a:p><a:r><a:t>Agenda</a:t></a:r></a:p></p:sld>",
    })


def _pdf(text: str) -> bytes:
    """A one-page PDF with `text` in Helvetica, offsets computed."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)


IDS = {k: f"1{k.capitalize():0<24}" for k in (
    "doc", "sheet", "slides", "pdf", "docx", "xlsx", "pptx", "image", "unshared",
    "trashed", "nodownload", "huge", "keyed", "keyedsheet",
)}


def _server() -> FakeDriveServer:
    return FakeDriveServer(files={
        IDS["doc"]: DriveFile(
            "Plan", GDOC, exports={"text/markdown": b"# Plan\n\nDay one", DOCX: b"PK"},
        ),
        IDS["sheet"]: DriveFile("Budget", GSHEET, tabs={
            "Costs": [["Item", "HKD"], ["Hotel", "1,200"]], "It's later": [["note"]],
        }),
        IDS["slides"]: DriveFile("Deck", GSLIDES, exports={"text/plain": b"Welcome\n\nAgenda"}),
        IDS["pdf"]: DriveFile("Menu.pdf", "application/pdf", data=_pdf("Lunch set 88")),
        IDS["docx"]: DriveFile("Trip.docx", DOCX, data=_docx()),
        IDS["xlsx"]: DriveFile("Budget.xlsx", XLSX, data=_xlsx()),
        IDS["pptx"]: DriveFile("Talk.pptx", PPTX, data=_pptx()),
        IDS["image"]: DriveFile("photo.png", "image/png", data=b"\x89PNG"),
        IDS["unshared"]: DriveFile("Secret", GDOC, shared=False),
        IDS["trashed"]: DriveFile("Old", GDOC, trashed=True),
        IDS["nodownload"]: DriveFile("Locked.pdf", "application/pdf", data=b"%PDF",
                                     download_disabled=True),
        IDS["huge"]: DriveFile("Huge", GDOC, export_too_large=True),
        IDS["keyed"]: DriveFile("Old link", GDOC, resource_key="0-abc",
                                exports={"text/markdown": b"keyed text"}),
        IDS["keyedsheet"]: DriveFile("Old sheet", GSHEET, resource_key="0-def",
                                     tabs={"Costs": [["Hotel", "1,200"]]},
                                     exports={XLSX: _xlsx()}),
    })


async def test_describe_names_the_file_in_the_house_vocabulary():
    server = _server()
    got = await _source(server).describe(ref=_ref(IDS["doc"]))
    assert (got.name, got.kind, got.owner) == ("Plan", "document", "Jordan")
    assert got.modified_ms == 1_791_532_800_000  # 2026-10-09T08:00:00Z
    sheet = await _source(server).describe(ref=_ref(IDS["image"]))
    assert sheet.kind == "other"


@pytest.mark.parametrize(("key", "kind", "want"), [
    ("doc", "document", "# Plan\n\nDay one"),
    ("slides", "presentation", "Welcome\n\nAgenda"),
    ("sheet", "spreadsheet",
     "## Costs\n| Item | HKD |\n| Hotel | 1,200 |\n\n## It's later\n| note |"),
    ("docx", "document", "Trip plan\n| Day | Place |"),
    ("xlsx", "spreadsheet", "## Budget\n| Hotel |  | 1200 |"),
    ("pptx", "presentation", "## Slide 1\nWelcome\n\n## Slide 2\nAgenda"),
])
async def test_every_kind_reads_as_text(key, kind, want):
    source = _source(_server())
    ref = _ref(IDS[key])
    assert (await source.describe(ref=ref)).kind == kind
    part = await source.read(ref=ref, part=1)
    assert (part.text, part.part, part.parts) == (want, 1, 1)


async def test_a_pdf_reads_page_by_page():
    part = await _source(_server()).read(ref=_ref(IDS["pdf"]), part=1)
    assert part.text.startswith("## Page 1\n") and "Lunch set 88" in part.text


async def test_a_kind_that_is_not_text_is_refused():
    with pytest.raises(ExternalFileError) as raised:
        await _source(_server()).read(ref=_ref(IDS["image"]), part=1)
    assert (raised.value.status, raised.value.reason) == ("refused", NOT_TEXT)


async def test_a_long_file_is_paged_and_says_part_i_of_n():
    server = _server()
    text = "\n\n".join(f"paragraph {n} " + "x" * 40 for n in range(10))
    server.files[IDS["doc"]].exports["text/markdown"] = text.encode()
    source = _source(server, part_chars=120)
    first = await source.read(ref=_ref(IDS["doc"]), part=1)
    assert first.parts == len(pages(text, 120)) > 1 and first.part == 1
    last = await source.read(ref=_ref(IDS["doc"]), part=first.parts)
    assert last.text.endswith("paragraph 9 " + "x" * 40)
    with pytest.raises(ExternalFileError) as raised:
        await source.read(ref=_ref(IDS["doc"]), part=first.parts + 1)
    assert (raised.value.status, raised.value.reason) == ("nothing_found", NO_PART)


def test_pages_never_loses_text_and_never_exceeds_the_cap():
    text = "\n".join(f"line {n} {'y' * (n % 37)}" for n in range(400))
    parts = pages(text, 500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    assert pages("", 10) == [""]


@pytest.mark.parametrize(("key", "status", "reason"), [
    ("unshared", "refused", NOT_SHARED),
    ("trashed", "nothing_found", DELETED),
    ("nodownload", "refused", NO_DOWNLOAD),
    ("huge", "refused", TOO_LARGE),
])
async def test_each_refusal_is_its_status_and_reason(key, status, reason):
    with pytest.raises(ExternalFileError) as raised:
        await _source(_server()).read(ref=_ref(IDS[key]), part=1)
    assert (raised.value.status, raised.value.reason) == (status, reason)


async def test_an_unknown_id_is_the_same_404_as_not_shared():
    """Drive answers both with a bare 404; the note asks for a share."""
    with pytest.raises(ExternalFileError) as raised:
        await _source(_server()).describe(ref=_ref("1NoSuchFile0000000000000000"))
    assert (raised.value.status, raised.value.reason) == ("refused", NOT_SHARED)


async def test_a_download_larger_than_the_cap_is_refused_before_it_is_fetched():
    server = _server()
    with pytest.raises(ExternalFileError) as raised:
        await _source(server, max_bytes=100).read(ref=_ref(IDS["pdf"]), part=1)
    assert (raised.value.status, raised.value.reason) == ("refused", TOO_LARGE)
    assert not [r for r in server.requests if r.url.params.get("alt") == "media"]


async def test_a_body_larger_than_the_cap_is_refused_while_streaming():
    server = _server()
    server.files[IDS["doc"]].exports["text/markdown"] = b"z" * 5000
    with pytest.raises(ExternalFileError) as raised:
        await _source(server, max_bytes=1000).read(ref=_ref(IDS["doc"]), part=1)
    assert (raised.value.status, raised.value.reason) == ("refused", TOO_LARGE)


async def test_the_resource_key_rides_in_its_header():
    server = _server()
    part = await _source(server).read(ref=_ref(IDS["keyed"], "0-abc"), part=1)
    assert part.text == "keyed text"
    assert all(
        r.headers["X-Goog-Drive-Resource-Keys"] == f"{IDS['keyed']}/0-abc"
        for r in server.requests if r.url.host != "oauth2.googleapis.com"
    )
    with pytest.raises(ExternalFileError) as raised:
        await _source(server).read(ref=_ref(IDS["keyed"]), part=1)
    assert raised.value.reason == NOT_SHARED


async def test_every_drive_call_supports_all_drives():
    server = _server()
    source = _source(server)
    for key in ("doc", "pdf"):
        await source.read(ref=_ref(IDS[key]), part=1)
    await source.fetch_bytes(ref=_ref(IDS["doc"]))
    drive = [r for r in server.requests if r.url.host == "www.googleapis.com"]
    assert len(drive) == 6
    assert all(r.url.params.get("supportsAllDrives") == "true" for r in drive)


async def test_nothing_is_cached_two_opens_are_two_fetches():
    """Every open fetches fresh, metadata too."""
    server = _server()
    source = _source(server)
    for _ in range(2):
        await source.describe(ref=_ref(IDS["doc"]))
        await source.read(ref=_ref(IDS["doc"]), part=1)
    drive = [r for r in server.requests if r.url.host == "www.googleapis.com"]
    exports = [r for r in drive if r.url.path.endswith("/export")]
    assert len(drive) == 6 and len(exports) == 2
    server.files[IDS["doc"]].exports["text/markdown"] = b"changed"
    assert (await source.read(ref=_ref(IDS["doc"]), part=1)).text == "changed"


async def test_fetch_bytes_exports_a_native_file_and_downloads_a_stored_one():
    source = _source(_server())
    doc = await source.fetch_bytes(ref=_ref(IDS["doc"]))
    assert (doc.data, doc.media_type) == (b"PK", DOCX)
    pdf = await source.fetch_bytes(ref=_ref(IDS["pdf"]))
    assert pdf.data.startswith(b"%PDF") and pdf.media_type == "application/pdf"


async def test_one_token_refresh_serves_many_calls_and_a_401_refreshes_once():
    server = _server()
    source = _source(server)
    await source.read(ref=_ref(IDS["doc"]), part=1)
    await source.read(ref=_ref(IDS["slides"]), part=1)
    tokens = [r for r in server.requests if r.url.host == "oauth2.googleapis.com"]
    # One read of the grant, one Drive token minted for drive.readonly alone.
    assert len(tokens) == 2
    assert server.token_scopes_asked() == [None, "https://www.googleapis.com/auth/drive.readonly"]
    assert b"client_secret=" + SECRET.encode() in tokens[1].content
    server.expire_tokens()
    assert (await source.read(ref=_ref(IDS["doc"]), part=1)).text.startswith("# Plan")
    assert len([r for r in server.requests if r.url.host == "oauth2.googleapis.com"]) == 3


async def test_invalid_grant_is_unavailable_counted_and_logged_without_the_secret(caplog):
    server = _server()
    server.revoked = True
    metrics = Metrics()
    with caplog.at_level(logging.INFO), pytest.raises(ExternalFileError) as raised:
        await _source(server, metrics).describe(ref=_ref(IDS["doc"]))
    assert (raised.value.status, raised.value.reason) == ("unavailable", "invalid_grant")
    assert metrics.get("nora_google_invalid_grant_total") == 1
    assert "google_auth.py" in caplog.text
    assert SECRET not in caplog.text and server.refresh_token not in caplog.text
    assert IDS["doc"] not in caplog.text


@pytest.mark.parametrize("trouble", ["down", "rate_limited"])
async def test_an_unreachable_or_throttled_provider_is_unavailable(trouble):
    server = _server()
    setattr(server, trouble, True)
    with pytest.raises(ExternalFileError) as raised:
        await _source(server).read(ref=_ref(IDS["doc"]), part=1)
    assert raised.value.status == "unavailable"


def test_a_package_declaring_a_dtd_is_refused():
    from nora.infra.external_files.text import docx_text

    bomb = _zip({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'})
    with pytest.raises(ExternalFileError) as raised:
        docx_text(bomb, 10_000)
    assert raised.value.reason == NOT_TEXT


# ---- the Sheets API -----------------------------------------

async def test_a_disabled_sheets_api_is_unavailable_and_logged_never_not_shared(caplog):
    """A 403 PERMISSION_DENIED whose detail says SERVICE_DISABLED is the
    operator's to fix: «I couldn't check», not «not shared with me»."""
    server = _server()
    server.sheets_disabled = True
    with caplog.at_level(logging.ERROR, logger="nora.external_files"), \
            pytest.raises(ExternalFileError) as raised:
        await _source(server).read(ref=_ref(IDS["sheet"]), part=1)
    assert (raised.value.status, raised.value.reason) == ("unavailable", "api_disabled")
    assert any("not enabled" in r.getMessage() for r in caplog.records)


async def test_a_drive_api_not_configured_is_unavailable_too():
    from nora.infra.external_files.google_drive import _failure

    body = b'{"error": {"code": 403, "errors": [{"reason": "accessNotConfigured"}]}}'
    assert _failure(403, body).status == "unavailable"


async def test_a_sheet_the_sheets_api_refuses_is_read_from_its_export():
    """Its link's resource key reaches Drive only: Drive answers for the file,
    the Sheets API does not, and the same workbook is read as an xlsx export."""
    server = _server()
    ref = recognise(f"https://docs.google.com/spreadsheets/d/{IDS['keyedsheet']}/edit?resourcekey=0-def")
    assert ref is not None
    got = await _source(server).read(ref=ref, part=1)
    assert "## Budget" in got.text and "Hotel" in got.text
    exports = [r for r in server.requests if r.url.path.endswith("/export")]
    assert exports and exports[0].url.params["mimeType"] == XLSX


async def test_a_compressed_body_cut_off_mid_stream_is_not_read_as_the_file():
    """A gzip body that ends before its stream does must not be
    returned as if it were the whole file."""
    import gzip

    import httpx

    from nora.infra.external_files.google_drive import _bounded_body

    whole = gzip.compress(b"x" * 50_000)
    resp = httpx.Response(200, headers={"Content-Encoding": "gzip"},
                          stream=httpx.ByteStream(whole[: len(whole) // 2]))
    with pytest.raises(ExternalFileError):
        await _bounded_body(resp, 1_000_000)
    complete = httpx.Response(200, headers={"Content-Encoding": "gzip"},
                              stream=httpx.ByteStream(whole))
    assert await _bounded_body(complete, 1_000_000) == b"x" * 50_000


async def test_a_native_kind_with_no_export_is_refused_not_asserted(monkeypatch):
    """Only a Sheet has no export, and a Sheet is read before this branch. A
    Doc is patched to have none so the branch runs: the refusal must be an
    ExternalFileError, not a bare assert (stripped under `python -O`, after
    which a None export would be sent to Google)."""
    from nora.infra.external_files import google_drive

    kind, _export, office = google_drive._NATIVE[google_drive._GDOC]
    monkeypatch.setitem(google_drive._NATIVE, google_drive._GDOC, (kind, None, office))
    with pytest.raises(ExternalFileError) as err:
        await _source(_server()).read(ref=_ref(IDS["doc"]), part=1)
    assert err.value.status == "refused"
    assert err.value.reason == NOT_TEXT
