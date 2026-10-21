"""`computer_import` copies a file somebody shared a LINK to into her
computer's `inbox/`.

Built through `build_tool_specs`, as production builds it, over the REAL
Google Drive adapter driven by `FakeDriveServer` (no network) wherever the
answer depends on what the provider says, and over the fake source where only
the tool's own logic is under test. The computer side is the contracted
fakes (`FakeSandboxRuntime`, `FakeComputerEventStore`).

What is pinned, each against the property and not a proxy for it: a link
handle's code, a pasted link, and a link pasted into `code` each copy the
file fresh; a native file lands as its office export; the suffix follows the
media type, and the owner's file name only for `application/octet-stream`;
neither the answer, the audit rows nor the log carry the file id or the URL;
both byte caps refuse and write nothing; the computer off is the off answer
with no fetch; the Drive fetch runs BEFORE the lease, so a slow provider
never holds the room's computer; two copies are two fetches; a code a sent
file and a link handle share is `ambiguous`, never a guess; and `file_fetch`
stays a read (the copy is `computer_import`'s).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
import typing

import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import _COMPUTER_BUSY_LIVE, _COMPUTER_OFF, NATIVE_TOOLS, build_tool_specs
from nora.domain.computer import ComputerToolSettings, computer_unit_scope
from nora.domain.computer_leases import ComputerLeases
from nora.domain.external_files import file_refs_in
from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.google.auth import GoogleCredentials
from nora.obs.metrics import Metrics
from nora.testing import FakeBlobStore, FakeComputerEventStore, FakeSandboxRuntime
from nora.testing.external_files import FakeExternalFilesSource, FakeFile
from nora.testing.fakes import FakeRecordStore
from nora.testing.google_drive import GDOC, GSHEET, GSLIDES, DriveFile, FakeDriveServer

GROUP = "g1"
DOC = "1SynthDocLink000000000000"
SHEET = "1SynthSheetLink00000000000"
SLIDES = "1SynthSlidesLink0000000000"
PDF = "1SynthPdfLink0000000000000"
BLOB = "1SynthBlobLink000000000000"
BARE = "1SynthBareLink000000000000"
UNSHARED = "1SynthNotShared0000000000"
DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit?usp=sharing"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0"
SLIDES_URL = f"https://docs.google.com/presentation/d/{SLIDES}/edit"
UNSHARED_URL = f"https://drive.google.com/file/d/{UNSHARED}/view"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_NOW = int(time.time() * 1000)


def _code(file_id: str) -> str:
    return hashlib.sha256(f"google_drive:{file_id}".encode()).hexdigest()[:6]


def _file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def _server() -> FakeDriveServer:
    return FakeDriveServer(files={
        DOC: DriveFile("Trip plan", GDOC, exports={DOCX: b"PK-docx-trip"}),
        SHEET: DriveFile("Budget", GSHEET, exports={XLSX: b"PK-xlsx-budget"}),
        SLIDES: DriveFile("Pitch", GSLIDES, exports={PPTX: b"PK-pptx-pitch"}),
        PDF: DriveFile("invoice.sh", "application/pdf", data=b"%PDF-1.7 invoice"),
        BLOB: DriveFile("dump.sqlite", "application/octet-stream", data=b"SQLite format 3"),
        BARE: DriveFile("dump", "application/octet-stream", data=b"raw bytes"),
        UNSHARED: DriveFile("Secret", GDOC, shared=False),
    })


def _drive(server: FakeDriveServer, **kwargs) -> GoogleDriveSource:
    return GoogleDriveSource(
        credentials=GoogleCredentials(
            client_id="cid", client_secret="cs",  # noqa: S106 - a test value
            refresh_token=server.refresh_token, metrics=Metrics(), transport=server.transport,
        ),
        transport=server.transport, **kwargs,
    )


class _Computer:
    """The computer's side: the VM, its audit and its leases."""

    def __init__(self, *, on: bool = True, import_max_bytes: int = 25 * 1024 * 1024,
                 leases: ComputerLeases | None = None) -> None:
        self.runtime = FakeSandboxRuntime() if on else None
        self.events = FakeComputerEventStore()
        self.leases = leases
        self.settings = ComputerToolSettings(import_max_bytes=import_max_bytes)

    def files(self) -> dict[str, bytes]:
        if self.runtime is None:
            return {}
        return self.runtime.files_by_scope.get(computer_unit_scope(GROUP), {})

    def rows(self) -> list:
        return [e for e in self.events.events if e.tool == "computer_import"]


async def _store(*posts: tuple[str, str], blobs: tuple[MediaBlob, ...] = ()) -> FakeRecordStore:
    store = FakeRecordStore()
    for n, (conv, body) in enumerate(posts):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id=conv, conversation_type="group",
            sender_id="+85200000000", sender_name="A", timestamp=_NOW - 60_000 + n, body=body,
            raw={}, external_files=file_refs_in(body), media_blobs=blobs,
        ))
    return store


def _tools(source, computer: _Computer, store=None, blobs=None) -> dict:
    room = Room(platform="signal", conversation_id=GROUP, roster_source=None,
                persons=_NoPersons())
    specs = build_tool_specs(
        unit_for=lambda platform, cid: cid,
        store=store or FakeRecordStore(), conversation_id=GROUP, conversation_type="group",
        persons=_NoPersons(), honcho=None, deliverer=None, metrics=Metrics(),
        room=room, transit_source=None, blob_store=blobs or FakeBlobStore(),
        computer=computer.runtime, computer_events=computer.events,
        computer_settings=computer.settings, computer_leases=computer.leases,
        external_files=source,
    )
    return {s.name: s.call for s in specs}


def _import(source, computer: _Computer, store=None):
    return _tools(source, computer, store)["computer_import"]


def _exports(server: FakeDriveServer, file_id: str) -> int:
    return sum(1 for r in server.requests if r.url.path.endswith(f"/{file_id}/export"))


def _no_id_or_url(text: str, *file_ids: str) -> None:
    assert "http" not in text.lower() and "google" not in text.lower(), text
    for file_id in file_ids:
        assert file_id not in text, text


# ---- the copy --------------------------------------------------------------


@pytest.mark.parametrize(("file_id", "url", "kind", "data", "suffix"), [
    (DOC, DOC_URL, "document", b"PK-docx-trip", ".docx"),
    (SHEET, SHEET_URL, "spreadsheet", b"PK-xlsx-budget", ".xlsx"),
    (SLIDES, SLIDES_URL, "presentation", b"PK-pptx-pitch", ".pptx"),
])
async def test_a_link_handles_code_copies_the_office_export(file_id, url, kind, data, suffix):
    computer = _Computer()
    store = await _store((GROUP, f"here {url}"))
    marker = f"[Google Doc link {_code(file_id)}]"
    out = await _import(_drive(_server()), computer, store)(code=marker)
    assert out["status"] == "ok", out
    path = f"inbox/{hashlib.sha256(data).hexdigest()[:12]}{suffix}"
    assert (out["path"], out["size"], out["kind"]) == (path, len(data), kind)
    assert computer.files() == {path: data}
    assert out["modified"] == "2026-10-09T16:00+08:00" and "snapshot" in out["note"]
    started, finished = computer.rows()
    assert (started.phase, finished.phase, finished.status) == ("started", "finished", "ok")
    assert started.request == {"code": _code(file_id)}


#: A link as a model may paste it into `code`: with its scheme, with none,
#: with an upper-case one (the last two must not reach the audit row raw).
SCHEMELESS = DOC_URL.removeprefix("https://")
UPPER = "HTTPS://" + SCHEMELESS


@pytest.mark.parametrize("args", [
    {"link": DOC_URL}, {"code": DOC_URL}, {"code": SCHEMELESS}, {"code": UPPER},
])
async def test_a_pasted_link_copies_the_file_and_the_record_names_only_its_handle(args):
    """A link in `link`, or pasted into `code` in any spelling, is a link: the
    audit row and the answer name the handle, never the URL or the id (R-18)."""
    computer = _Computer()
    out = await _import(_drive(_server()), computer)(**args)
    assert out["status"] == "ok" and out["kind"] == "document"
    _no_id_or_url(json.dumps(out), DOC)
    for row in computer.rows():
        _no_id_or_url(json.dumps([row.request, row.result]), DOC)
    assert computer.rows()[0].request == {"code": "", "link": _code(DOC)}


async def test_neither_the_answer_the_rows_nor_the_log_carry_the_id_or_url(caplog):
    caplog.set_level(logging.DEBUG)
    computer = _Computer()
    store = await _store((GROUP, f"{DOC_URL} and {UNSHARED_URL}"))
    tool = _import(_drive(_server()), computer, store)
    answers = [
        await tool(code=_code(DOC)), await tool(link=DOC_URL),
        await tool(code=SCHEMELESS), await tool(code=UPPER),
        await tool(code=_code(UNSHARED)), await tool(link=UNSHARED_URL),
        # A bare id is no link and no code: whatever the model put in `code`
        # never reaches the row raw, only its hex.
        await tool(code=DOC),
    ]
    assert [a["status"] for a in answers] == ["ok"] * 4 + ["refused"] * 2 + ["nothing_found"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # The positive control: the provider's requests WERE logged, redacted.
    assert "/drive/v3/files/<file>" in logged, logged
    for secret in (DOC, UNSHARED, DOC_URL, UNSHARED_URL, SCHEMELESS):
        assert secret not in logged, logged
    _no_id_or_url(json.dumps(answers), DOC, UNSHARED)
    for row in computer.rows():
        _no_id_or_url(json.dumps([row.request, row.result]), DOC, UNSHARED)


@pytest.mark.parametrize(("file_id", "suffix"), [
    (PDF, ".pdf"),        # the type wins over an owner's name that says .sh
    (BLOB, ".sqlite"),    # octet-stream says nothing: the name's suffix
    (BARE, ""),           # octet-stream and no suffix: none
])
async def test_the_suffix_follows_the_media_type_and_the_name_only_for_octet_stream(
        file_id, suffix):
    computer = _Computer()
    out = await _import(_drive(_server()), computer)(link=_file_url(file_id))
    assert out["status"] == "ok", out
    assert out["path"].removeprefix("inbox/")[12:] == suffix
    assert set(computer.files()) == {out["path"]}


async def test_an_unshared_file_is_refused_with_how_to_share_it_and_nothing_is_written():
    computer = _Computer()
    out = await _import(_drive(_server()), computer)(link=UNSHARED_URL)
    assert out["status"] == "refused" and "nora@67.zone" in out["note"]
    assert computer.files() == {}
    assert [r.phase for r in computer.rows()] == ["started", "finished"]


async def test_over_the_computers_import_cap_is_refused_and_writes_nothing():
    computer = _Computer(import_max_bytes=5)
    out = await _import(_drive(_server()), computer)(link=DOC_URL)
    assert out["status"] == "refused" and "too large" in out["note"]
    assert computer.files() == {} and computer.rows()[-1].status == "refused"


async def test_over_the_sources_byte_cap_is_refused_and_writes_nothing():
    computer = _Computer()
    out = await _import(_drive(_server(), max_bytes=5), computer)(link=DOC_URL)
    assert out["status"] == "refused" and "too large to copy" in out["note"]
    assert computer.files() == {} and computer.rows()[-1].status == "refused"


async def test_with_the_computer_off_it_is_the_off_answer_and_fetches_nothing():
    server = _server()
    out = await _import(_drive(server), _Computer(on=False))(link=DOC_URL)
    assert out == dict(_COMPUTER_OFF)
    assert server.reads(DOC) == 0


async def test_with_no_source_wired_a_file_link_is_unavailable_not_unknown():
    computer = _Computer()
    out = await _import(None, computer)(link=DOC_URL)
    assert out["status"] == "unavailable" and "shared files" in out["note"]
    assert computer.files() == {}


async def test_two_copies_are_two_fetches_and_the_second_is_the_file_as_it_is_now():
    """Never cached: a copy is a snapshot, the next one fetches afresh."""
    server = _server()
    computer = _Computer()
    tool = _import(_drive(server), computer)
    first = await tool(link=DOC_URL)
    server.files[DOC].exports[DOCX] = b"PK-docx-edited"
    second = await tool(link=DOC_URL)
    assert _exports(server, DOC) == 2
    assert first["path"] != second["path"]
    assert computer.files()[second["path"]] == b"PK-docx-edited"


# ---- the lease -------------------------------------------------------------


class _SlowSource(FakeExternalFilesSource):
    """A provider that answers only when released: the fetch in flight."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.release = asyncio.Event()
        self.fetching = asyncio.Event()

    async def fetch_bytes(self, *, ref):
        self.fetching.set()
        await self.release.wait()
        return await super().fetch_bytes(ref=ref)


async def test_a_slow_fetch_never_holds_the_rooms_computer():
    """The Drive fetch runs BEFORE the lease: while it is in flight, another
    computer call in the room is not answered busy."""
    source = _SlowSource(files={DOC: FakeFile("Plan", data=b"PK", media_type=DOCX)})
    computer = _Computer(leases=ComputerLeases(live_wait_seconds=0.05))
    tools = _tools(source, computer)
    copy = asyncio.create_task(tools["computer_import"](link=DOC_URL))
    await asyncio.wait_for(source.fetching.wait(), 1)
    other = await tools["computer_write"](path="notes.txt", content="x")
    assert other["status"] == "ok", other
    source.release.set()
    assert (await copy)["status"] == "ok"


async def test_with_the_lease_held_it_is_the_busy_answer_and_writes_nothing():
    computer = _Computer(leases=ComputerLeases(live_wait_seconds=0.05))
    computer.runtime.hold = asyncio.Event()
    tools = _tools(_drive(_server()), computer)
    holder = asyncio.create_task(tools["computer_run"](command="sleep 60"))
    await asyncio.sleep(0.01)
    out = await tools["computer_import"](link=DOC_URL)
    assert out == dict(_COMPUTER_BUSY_LIVE)
    assert computer.files() == {}
    computer.runtime.hold.set()
    await holder


# ---- codes -----------------------------------------------------------------


async def test_a_code_a_sent_file_and_a_link_handle_share_is_ambiguous_and_fetches_nothing():
    """The same six hex may name a held attachment and a link handle. Never a
    guess between them: `ambiguous`, nothing fetched, nothing written."""
    server = _server()
    computer = _Computer()
    collide = MediaBlob(sha256=_code(DOC) + "0" * 58, media_type="text/plain", size=3)
    store = await _store((GROUP, f"[file {_code(DOC)}] and {DOC_URL}"), blobs=(collide,))
    out = await _import(_drive(server), computer, store)(code=_code(DOC))
    assert out["status"] == "ambiguous" and "link" in out["note"]
    assert server.reads(DOC) == 0 and computer.files() == {}
    _no_id_or_url(json.dumps(out), DOC)


async def test_a_link_handle_from_another_room_is_nothing_found():
    server = _server()
    store = await _store(("g2", DOC_URL))
    out = await _import(_drive(server), _Computer(), store)(code=_code(DOC))
    assert out["status"] == "nothing_found"
    assert server.reads(DOC) == 0


async def test_another_networks_link_is_redirected_to_its_owner():
    out = await _import(FakeExternalFilesSource(), _Computer())(
        link="https://x.com/synth_user01/status/9000000000000000001")
    assert out["status"] == "nothing_found" and "x_read" in out["note"]


class _LinkLookupFails(FakeRecordStore):
    """A record whose link rows cannot be read while its media rows can."""

    async def external_file_rows(self, **kwargs):
        raise RuntimeError("link rows unreadable")


async def _sent(store: FakeRecordStore, blobs: FakeBlobStore, data: bytes) -> str:
    key = await blobs.put(data=data)
    await store.append(message=NormalizedMessage(
        "signal", GROUP, "group", "+85200000000", "A", _NOW - 1_000,
        f"[file {key[:6]}]", {},
        media_blobs=(MediaBlob(sha256=key, media_type="text/csv", size=len(data),
                               filename="n.csv"),),
    ))
    return key


async def test_a_sent_file_still_imports_when_only_the_link_lookup_fails():
    """The link lookup failing does not turn a matched sent-file attachment
    `unavailable`."""
    store, blobs, computer = _LinkLookupFails(), FakeBlobStore(), _Computer()
    key = await _sent(store, blobs, b"a,b\n1,2\n")
    out = await _tools(FakeExternalFilesSource(), computer, store, blobs)["computer_import"](
        code=key[:6])
    assert out["status"] == "ok", out
    assert computer.files() == {f"inbox/{key[:12]}.csv": b"a,b\n1,2\n"}


async def test_with_no_sent_file_matched_an_unreadable_link_lookup_is_unavailable():
    computer = _Computer()
    out = await _tools(FakeExternalFilesSource(), computer, _LinkLookupFails())[
        "computer_import"](code="abcdef")
    assert out["status"] == "unavailable" and computer.files() == {}


# ---- the split: file_fetch stays a read -------------------------------------


def test_file_fetch_stays_a_read_and_a_lookup():
    """The copy is `computer_import`'s, so `file_fetch` keeps its declaration:
    read-only (the Dreamer ceiling, R-7's wording) and in R-97's lookup
    set, where a successful file read counts as «found»."""
    registry = ToolRegistry()
    assert "file_fetch" in registry.read_only_tools()
    assert "file_fetch" in registry.lookup_tools()
    assert "computer_import" not in registry.read_only_tools()
    assert NATIVE_TOOLS["file_fetch"].side_effect_state == "none"
    built = _tools(FakeExternalFilesSource(), _Computer())["file_fetch"]
    assert set(inspect.signature(built).parameters) == {"code", "link", "part"}


@pytest.mark.parametrize("made_up", [
    "https://drive.google.com/file/d/{c}", "drive.google.com/drive/folders/{c}",
    "https://docs.google.com/spreadsheets/d/{c}/edit",
])
@pytest.mark.parametrize("slot", ["link", "code"])
async def test_a_link_built_around_a_handles_code_copies_that_file(made_up, slot):
    # A link built around a handle's code (`drive.google.com/file/d/<code>`) is the code.
    computer = _Computer()
    store = await _store((GROUP, f"here {SHEET_URL}"))
    out = await _import(_drive(_server()), computer, store)(
        **{slot: made_up.format(c=_code(SHEET))})
    assert out["status"] == "ok", out
    assert out["kind"] == "spreadsheet"
    assert computer.rows()[0].request == {"code": _code(SHEET)}


async def test_a_made_up_link_beside_a_code_is_still_one_too_many():
    # The made-up-link rewrite must not swallow a second argument.
    # The sheet IS in this room, so swallowing `code` would copy it.
    computer = _Computer()
    store = await _store((GROUP, f"here {SHEET_URL}"))
    out = await _import(_drive(_server()), computer, store)(
        code="a3f2c1", link=f"https://drive.google.com/file/d/{_code(SHEET)}")
    assert out["status"] != "ok" and computer.files() == {}
