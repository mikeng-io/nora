"""`computer_export` and `computer_import`: files crossing the edge of Nora's computer.

Driven through `build_tool_specs` against the contracted fakes:
`FakeSandboxRuntime` (bound to computerd), `FakeComputerEventStore` and
`FakeComputerExportStore` (bound to Postgres), `FakeBlobStore`, and the real
`Deliverer` over `FakeRecordStore` for the chat destination.

What is pinned, each against the property and not a proxy for it:
- a link is the portal's (ADR 0023): its bytes are in Nora's blob store, its
  url is `<public_url>/artifacts/<artifact_id>`, a revision keeps both, and
  without a `public_url` there is no link;
- a room may revise only an artifact IT exported (another room's id is
  refused and nothing is recorded under it);
- the media type comes from the BYTES: a `.png` holding a page leaves as the
  page it is, and a file that is no allowed type does not leave at all;
- the size cap holds even against a runtime that ignores it;
- the sha256 is computed on Nora's side: a runtime whose claimed digest
  disagrees with its bytes publishes nothing, and the recorded digest is the
  one of the bytes received, never the claim;
- every call writes exactly two audit rows on every path, failures included;
- `computer_import` writes only under `inbox/`, whatever the code or the
  sent file's name says.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.doctrine.loader import DoctrineStore
from nora.domain.computer import (
    BytesResult,
    ComputerToolSettings,
    computer_unit_scope,
)
from nora.domain.computer_exports import sniff_export_type
from nora.domain.media import MediaBlob, resolve_room_media
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeComputerEventStore,
    FakeComputerExportStore,
    FakeMediaObjectStore,
    FakeRecordStore,
    FakeRosterSource,
    FakeSandboxRuntime,
)
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

GROUP = "g-export"
OTHER_GROUP = "g-other"
PAGE = b"<!doctype html>\n<html><body><h1>Trip</h1></body></html>\n"
#: A picture: what `computer_export` publishes (files, never pages).
CHART = b"\x89PNG\r\n\x1a\n" + b"\x00\x01" * 40
#: `[documents] public_url`: the portal origin a link is made under.
PORTAL = "https://nora.example.test"


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n[[persons.miles.signal]]\n'
                    'phone = "+85290000101"\n')
    return load_persons(path)


class _World:
    """One deployment's shared state: the VM, the audit, the record, the blobs."""

    def __init__(self, *, runtime: FakeSandboxRuntime | None = None,
                 blobs: FakeBlobStore | None = None) -> None:
        self.runtime = runtime or FakeSandboxRuntime()
        self.events = FakeComputerEventStore()
        self.exports = FakeComputerExportStore()
        self.blobs = blobs if blobs is not None else FakeBlobStore()
        self.records = FakeRecordStore()
        self.media = FakeMediaObjectStore()
        self.metrics = Metrics()

    def put(self, conversation_id: str, path: str, data: bytes) -> None:
        scope = computer_unit_scope(conversation_id)
        self.runtime.files_by_scope.setdefault(scope, {})[path] = data

    def rows(self, tool: str) -> list:
        return [e for e in self.events.events if e.tool == tool]

    def links(self) -> list:
        return [e for e in self.exports.exports if e.destination == "link"]


def _tools(world: _World, persons, *, conversation_id: str = GROUP, fire_turn: bool = False,
           settings: ComputerToolSettings | None = None, deliverer=None, portal=PORTAL):
    room = Room(
        platform="signal", conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}),
    )
    specs = build_tool_specs(
        unit_for=lambda platform, cid: cid,
        store=world.records, conversation_id=conversation_id, conversation_type="group",
        persons=persons, honcho=None, deliverer=deliverer, metrics=world.metrics,
        room=room, transit_source=None, blob_store=world.blobs, fire_turn=fire_turn,
        computer=world.runtime, computer_events=world.events, computer_settings=settings,
        computer_exports=world.exports,
        artifact_link_base=portal, media_store=world.media,
    )
    return {s.name: s.call for s in specs
            if s.name in ("computer_export", "computer_import")}


def _deliverer(tmp_path, records, *, conversation_id: str = GROUP):
    pdir = tmp_path / "doctrine"
    pdir.mkdir(exist_ok=True)
    write(pdir, "identity.md", name="soul", order=10, body="You are Nora.")
    doctrine = DoctrineStore(pdir, canary="C", metrics=Metrics()).get()
    sink = SinkSender()
    deliverer = Deliverer(
        store=records, adapter=sink,
        target=ConversationTarget("signal", conversation_id, "group"),
        doctrine=doctrine, canary="C", tag_id="t1", nora_sender_id="nora",
        message_cap=6, audience="tag", metrics=Metrics(),
    )
    return deliverer, sink


def _two_rows(world: _World, tool: str) -> tuple:
    """Exactly one operation's two rows for `tool`: started, then finished."""
    rows = world.rows(tool)
    assert [r.phase for r in rows] == ["started", "finished"], [
        (r.phase, r.status) for r in rows
    ]
    assert rows[0].operation_id == rows[1].operation_id
    return rows[0], rows[1]


# -- the link destination ----------------------------------------------------


async def test_a_link_export_keeps_the_bytes_and_links_the_portal(persons):
    """FALSIFIER: ADR 0023 decision 2. A link's bytes are in Nora's blob store
    and its url is the portal's Artifacts page for a 12-hex handle, never a public
    `/drop/` URL with the bytes in an external bucket."""
    world = _World()
    world.put(GROUP, "projects/trip/out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](
        path="projects/trip/out/chart.png", to="link", title="Trip",
    )
    assert out["status"] == "ok"
    assert re.fullmatch(r"[0-9a-f]{12}", out["artifact_id"])
    assert out["url"] == f"{PORTAL}/artifacts/{out['artifact_id']}"
    [row] = world.exports.exports
    assert (row.destination, row.artifact_id, row.url, row.revised) == (
        "link", out["artifact_id"], out["url"], False)
    assert (row.media_type, row.title) == ("image/png", "Trip")
    assert world.blobs.blobs[row.blob_key] == CHART
    assert (row.platform, row.conversation_id, row.path) == (
        "signal", GROUP, "projects/trip/out/chart.png")
    _, finished = _two_rows(world, "computer_export")
    assert finished.status == "ok"
    assert finished.result["url"] == out["url"]


async def test_the_same_room_revises_its_link_and_keeps_it(persons):
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    tools = _tools(world, persons)
    first = await tools["computer_export"](path="out/chart.png", to="link")
    world.put(GROUP, "out/chart.png", CHART + b"v2")
    second = await tools["computer_export"](
        path="out/chart.png", to="link", artifact_id=first["artifact_id"],
    )
    assert second["status"] == "ok"
    assert (second["artifact_id"], second["url"]) == (first["artifact_id"], first["url"])
    assert [e.revised for e in world.exports.exports] == [False, True]
    newest = world.exports.exports[-1]
    assert world.blobs.blobs[newest.blob_key] == CHART + b"v2"


async def test_without_a_portal_url_a_link_is_unavailable(persons):
    """No `[documents] public_url`, no link: the portal is where it would open."""
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons, portal="")["computer_export"](
        path="out/chart.png", to="link",
    )
    assert out["status"] == "unavailable"
    assert world.exports.exports == []


async def test_a_link_that_could_not_be_recorded_is_not_handed_out(persons):
    """The row IS the link: the portal finds the file through it. A link whose
    row failed would open nothing, so no url is given."""
    world = _World()
    world.exports.fail_record = True
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](path="out/chart.png", to="link")
    assert out["status"] == "unavailable"
    assert "url" not in out


async def test_a_room_cannot_revise_another_rooms_artifact(persons):
    """FALSIFIER: the owner check. Remove it and this room's file is recorded
    as a revision of the other room's link."""
    world = _World()
    world.put(OTHER_GROUP, "out/theirs.png", CHART)
    theirs = await _tools(world, persons, conversation_id=OTHER_GROUP)["computer_export"](
        path="out/theirs.png", to="link",
    )
    world.put(GROUP, "out/mine.png", CHART + b"overwritten")
    out = await _tools(world, persons)["computer_export"](
        path="out/mine.png", to="link", artifact_id=theirs["artifact_id"],
    )
    assert out["status"] == "refused"
    assert out["note"].strip()
    assert len(world.exports.exports) == 1
    rows = [e for e in world.events.finished() if e.conversation_id == GROUP]
    assert [(r.status, r.result["reason"]) for r in rows] == [
        ("refused", "not this room's artifact")]


async def test_an_artifact_the_computer_never_made_is_refused(persons):
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](
        path="out/chart.png", to="link", artifact_id="art_made_before_the_computer",
    )
    assert out["status"] == "refused"
    assert world.links() == []


async def test_an_unreadable_owner_is_unavailable_not_a_revision(persons):
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    tools = _tools(world, persons)
    made = await tools["computer_export"](path="out/chart.png", to="link")
    world.exports.fail_read = True
    out = await tools["computer_export"](
        path="out/chart.png", to="link", artifact_id=made["artifact_id"],
    )
    assert out["status"] == "unavailable"


# -- the media type is the bytes' --------------------------------------------


async def test_a_web_page_never_leaves_as_a_link(persons):
    """FALSIFIER: files, never documents. A page is the document path's;
    `computer_export` refuses it, and never publishes it in the portal."""
    world = _World()
    world.put(GROUP, "out/report.html", PAGE)
    out = await _tools(world, persons)["computer_export"](path="out/report.html", to="link")
    assert out["status"] == "refused"
    assert world.links() == []
    _, finished = _two_rows(world, "computer_export")
    assert finished.result["reason"] == "type not allowed"


async def test_a_png_name_holding_a_page_is_refused_as_the_page_it_is(persons):
    """FALSIFIER: MIME from the bytes. Typed from the extension, this page
    would be published as `image/png`."""
    world = _World()
    world.put(GROUP, "out/chart.png", PAGE)
    out = await _tools(world, persons)["computer_export"](path="out/chart.png", to="link")
    assert out["status"] == "refused"
    assert world.links() == []


SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


async def test_an_svg_is_never_a_link_but_may_be_attached_or_kept(persons, tmp_path):
    """FALSIFIER: an SVG can run script, so it is never a link (it may be
    attached or kept)."""
    world = _World()
    world.put(GROUP, "out/flow.svg", SVG)
    deliverer, _ = _deliverer(tmp_path, world.records)
    tools = _tools(world, persons, deliverer=deliverer)
    link = await tools["computer_export"](path="out/flow.svg", to="link")
    kept = await tools["computer_export"](path="out/flow.svg", to="keep")
    sent = await tools["computer_export"](path="out/flow.svg", to="chat")
    assert (link["status"], kept["status"], sent["status"]) == ("refused", "ok", "ok")
    assert world.links() == []
    assert [e.destination for e in world.exports.exports] == ["keep", "chat"]


MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.mark.parametrize(("path", "data", "media_type"), [
    ("projects/ytdl/audio.mp3", MP3, "audio/mpeg"),
    ("projects/ytdl/video_480.mp4", MP4, "video/mp4"),
    ("out/photos.zip", b"PK\x03\x04" + b"\x00\x01\x02" * 50, "application/zip"),
    ("out/model.bin", b"\x00\x01\x02\x03" * 50, "application/octet-stream"),
], ids=["mp3", "mp4", "zip", "opaque"])
async def test_any_kind_of_file_leaves_to_every_destination(persons, tmp_path, path, data,
                                                            media_type):
    """FALSIFIER: any file but a web page leaves, typed off its bytes: an MP3
    and an MP4 go as chat, link and keep."""
    world = _World()
    world.put(GROUP, path, data)
    deliverer, _ = _deliverer(tmp_path, world.records)
    tools = _tools(world, persons, deliverer=deliverer)
    outs = [await tools["computer_export"](path=path, to=to) for to in ("chat", "link", "keep")]
    assert [o["status"] for o in outs] == ["ok", "ok", "ok"]
    assert {e.media_type for e in world.exports.exports} == {media_type}
    assert len(world.links()) == 1


async def test_a_refused_page_is_called_a_page_in_its_note(persons):
    """A page is the one refusal, and its note says so rather than calling every
    refused file a web page, an MP3 included."""
    world = _World()
    world.put(GROUP, "out/report.html", PAGE)
    out = await _tools(world, persons)["computer_export"](path="out/report.html", to="keep")
    assert out["status"] == "refused"
    assert "web page" in out["note"]


@pytest.mark.parametrize(("data", "path", "expected"), [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "a.html", "image/png"),
    (b"%PDF-1.7\n...", "a.txt", "application/pdf"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "a.png", "image/svg+xml"),
    (b"<?xml version='1.0'?>\n<svg></svg>", "a.txt", "image/svg+xml"),
    (b'{"a": 1}', "a.csv", "application/json"),
    (b"city,temp\nHK,31\n", "data.csv", "text/csv"),
    (b"# Title\n\nbody\n", "notes.md", "text/markdown"),
    (b"# Title\n\nbody\n", "notes.png", "text/plain"),
    (b"  <P>hello</P>", "x.md", None),
    (b"<!-- c -->", "x.txt", None),
    (b"<!doctype html><html></html>", "x.png", None),
    (b"\x00\x00binary", "x.txt", "application/octet-stream"),
    (b"\xc3\x28\xa0\xa1", "x.txt", "application/octet-stream"),
    (b"\x00\x00\x00\x18ftypmp42", "x.png", "video/mp4"),
    (b"\x00\x00\x00\x18ftypM4A ", "x.mp4", "audio/mp4"),
    (b"ID3\x04\x00", "x.txt", "audio/mpeg"),
    (b"\xff\xfb\x90\x00", "x.bin", "audio/mpeg"),
    (b"OggS\x00\x02", "x.mp3", "audio/ogg"),
    (b"fLaC\x00", "x", "audio/flac"),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", "x", "audio/wav"),
    (b"\x1aE\xdf\xa3\x01", "x", "video/webm"),
    (b"PK\x03\x04\x14\x00", "x.pdf", "application/zip"),
    (b"\x1f\x8b\x08\x00", "x", "application/gzip"),
    # A page in latin-1 or UTF-16 is still a page; a UTF-16
    # CSV is not an MP3 (its BOM `FF FE` passes a bare frame-sync test); and
    # "ID3" with no version byte is text.
    ("<html><body>caf\xe9</body></html>".encode("latin-1"), "x.bin", None),
    ("<html><body>hi</body></html>".encode("utf-16"), "x.bin", None),
    ("a,b\n1,2\n".encode("utf-16"), "data.csv", "text/csv"),
    (b"ID3 tag notes\n", "notes.md", "text/markdown"),
    (b"\xff\xf9\x00\x00", "x", "application/octet-stream"),
])
def test_the_type_is_read_off_the_bytes(data, path, expected):
    assert sniff_export_type(data, path) == expected


# -- the size cap --------------------------------------------------------------


class _CapIgnoringRuntime(FakeSandboxRuntime):
    """A runtime that hands back more than `max_bytes`: the cap is Nora's."""

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        return await super().fetch(scope=scope, path=path, max_bytes=10**9)


async def test_the_size_cap_refuses_a_large_file(persons):
    world = _World()
    world.put(GROUP, "big.txt", b"x" * 101)
    out = await _tools(world, persons, settings=ComputerToolSettings(export_max_bytes=100))[
        "computer_export"](path="big.txt", to="keep")
    assert out["status"] == "refused"
    assert world.blobs.blobs == {}


async def test_the_size_cap_holds_against_a_runtime_that_ignores_it(persons):
    """FALSIFIER: the cap is checked on Nora's side, on the bytes received."""
    world = _World(runtime=_CapIgnoringRuntime())
    world.put(GROUP, "big.txt", b"x" * 101)
    out = await _tools(world, persons, settings=ComputerToolSettings(export_max_bytes=100))[
        "computer_export"](path="big.txt", to="keep")
    assert out["status"] == "refused"
    assert world.blobs.blobs == {}
    _, finished = _two_rows(world, "computer_export")
    assert (finished.status, finished.result["reason"], finished.result["size"]) == (
        "refused", "over the cap", 101)


# -- the digest is Nora's --------------------------------------------------------


class _TamperingRuntime(FakeSandboxRuntime):
    """Claims the digest of the bytes it was ASKED for while handing back
    different ones: what a VM that swapped a file mid-transfer looks like."""

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        got = await super().fetch(scope=scope, path=path, max_bytes=max_bytes)
        return BytesResult(status=got.status, data=got.data + b"<script>x</script>",
                           sha256=got.sha256)


async def test_a_runtime_whose_digest_disagrees_with_its_bytes_publishes_nothing(persons):
    """FALSIFIER: the sha256 is computed on Nora's side. Trusting the claim,
    the tampered page would be published under the original's digest."""
    world = _World(runtime=_TamperingRuntime())
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](path="out/chart.png", to="link")
    assert out["status"] == "unavailable"
    assert world.links() == []
    assert world.exports.exports == []
    _, finished = _two_rows(world, "computer_export")
    received = CHART + b"<script>x</script>"
    assert finished.result["sha256"] == hashlib.sha256(received).hexdigest()
    assert finished.result["claimed_sha256"] == hashlib.sha256(CHART).hexdigest()


class _SilentRuntime(FakeSandboxRuntime):
    """Sends no digest at all: the record's digest must still be there."""

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        got = await super().fetch(scope=scope, path=path, max_bytes=max_bytes)
        return BytesResult(status=got.status, data=got.data)


async def test_the_recorded_digest_is_computed_from_the_bytes_received(persons):
    world = _World(runtime=_SilentRuntime())
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](path="out/chart.png", to="link")
    assert out["status"] == "ok"
    [row] = world.exports.exports
    assert row.sha256 == hashlib.sha256(CHART).hexdigest()


# -- every call audited, two rows, on every path ----------------------------------


@pytest.mark.parametrize(("case", "status"), [
    ("ok", "ok"),
    ("missing", "nothing_found"),
    ("escape", "refused"),
    ("page", "refused"),
    ("store_down", "unavailable"),
    ("computer_down", "unavailable"),
    ("no_portal", "unavailable"),
    ("chat_without_a_room_sender", "unavailable"),
    ("artifact_id_on_keep", "refused"),
])
async def test_every_export_writes_two_rows_whatever_happens(persons, case, status):
    world = _World(blobs=FakeBlobStore(fail_on_put=case == "store_down"))
    world.put(GROUP, "out/chart.png", CHART)
    world.put(GROUP, "out/page.txt", PAGE)
    world.runtime.down = case == "computer_down"
    kwargs = {
        "ok": {"path": "out/chart.png", "to": "link"},
        "missing": {"path": "out/nope.png", "to": "link"},
        "escape": {"path": "../other/chart.png", "to": "link"},
        "page": {"path": "out/page.txt", "to": "keep"},
        "store_down": {"path": "out/chart.png", "to": "link"},
        "computer_down": {"path": "out/chart.png", "to": "link"},
        "no_portal": {"path": "out/chart.png", "to": "link"},
        "chat_without_a_room_sender": {"path": "out/chart.png", "to": "chat"},
        "artifact_id_on_keep": {"path": "out/chart.png", "to": "keep", "artifact_id": "a"},
    }[case]
    tools = _tools(world, persons, portal="" if case == "no_portal" else PORTAL)
    out = await tools["computer_export"](**kwargs)
    assert out["status"] == status
    assert out["note"].strip()
    started, finished = _two_rows(world, "computer_export")
    assert started.request["path"] == kwargs["path"]
    assert finished.status == status
    assert (finished.actor, finished.conversation_id) == ("tag", GROUP)


async def test_an_export_with_the_computer_off_is_still_two_rows(persons):
    world = _World()
    room = Room(platform="signal", conversation_id=GROUP,
                roster_source=FakeRosterSource(groups={GROUP: ()}), persons=persons)
    specs = build_tool_specs(
        unit_for=lambda platform, cid: cid,
        store=None, conversation_id=GROUP, conversation_type="group", persons=persons,
        honcho=None, deliverer=None, metrics=Metrics(), room=room, transit_source=None,
        computer=None, computer_events=world.events, computer_exports=world.exports,
    )
    call = next(s.call for s in specs if s.name == "computer_export")
    out = await call(path="out/chart.png", to="link")
    assert out["status"] == "unavailable"
    _, finished = _two_rows(world, "computer_export")
    assert finished.result["reason"] == "off"


async def test_a_failed_record_still_answers_and_is_counted(persons):
    world = _World()
    world.exports.fail_record = True
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](path="out/chart.png", to="keep")
    assert out["status"] == "ok"
    assert world.metrics.get("nora_computer_audit_failures_total",
                             tool="computer_export", stage="export") == 1
    _, finished = _two_rows(world, "computer_export")
    assert finished.result["recorded"] is False


# -- containment: an outside effect waits for a person ---------------------------


async def test_a_fire_turn_may_keep_but_never_send(persons):
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    tools = _tools(world, persons, fire_turn=True)
    link = await tools["computer_export"](path="out/chart.png", to="link")
    kept = await tools["computer_export"](path="out/chart.png", to="keep")
    assert (link["status"], kept["status"]) == ("refused", "ok")
    assert world.links() == []


# -- keep and chat -----------------------------------------------------------------


async def test_keep_holds_the_bytes_privately_and_records_the_key(persons):
    world = _World()
    world.put(GROUP, "data/prices.csv", b"city,price\nHK,10\n")
    out = await _tools(world, persons)["computer_export"](path="data/prices.csv", to="keep")
    assert out["status"] == "ok"
    digest = hashlib.sha256(b"city,price\nHK,10\n").hexdigest()
    assert world.blobs.blobs[digest] == b"city,price\nHK,10\n"
    [row] = world.exports.exports
    assert (row.destination, row.blob_key, row.media_type) == ("keep", digest, "text/csv")
    assert world.links() == []


async def test_chat_sends_the_file_through_the_rooms_own_sender(persons, tmp_path):
    world = _World()
    world.put(GROUP, "out/chart.png", b"\x89PNG\r\n\x1a\n" + b"\x01" * 32)
    deliverer, sink = _deliverer(tmp_path, world.records)
    out = await _tools(world, persons, deliverer=deliverer)["computer_export"](
        path="out/chart.png", to="chat",
    )
    assert out["status"] == "ok"
    [attachments] = sink.sent_attachments
    [sent] = attachments
    assert (sent.media_type, sent.filename) == ("image/png", "chart.png")
    [row] = world.exports.exports
    assert row.destination == "chat"
    assert row.blob_key in world.blobs.blobs


# -- import --------------------------------------------------------------------------


async def _sent_in_room(world: _World, data: bytes, *, media_type: str, filename: str,
                        conversation_id: str = GROUP) -> str:
    key = await world.blobs.put(data=data)
    await world.records.append(message=NormalizedMessage(
        "signal", conversation_id, "group", "+85290000101", "Miles", 1_000,
        f"[file {key[:6]}]", {},
        media_blobs=(MediaBlob(sha256=key, media_type=media_type, size=len(data),
                               filename=filename),),
    ))
    return key


async def test_import_copies_a_sent_file_into_the_inbox(persons):
    world = _World()
    key = await _sent_in_room(world, b"a,b\n1,2\n", media_type="text/csv",
                              filename="numbers.csv")
    out = await _tools(world, persons)["computer_import"](code=f"[file {key[:6]}]")
    assert out["status"] == "ok"
    assert out["path"] == f"inbox/{key[:12]}.csv"
    scope = computer_unit_scope(GROUP)
    assert world.runtime.files_by_scope[scope][out["path"]] == b"a,b\n1,2\n"
    _, finished = _two_rows(world, "computer_import")
    assert (finished.status, finished.result["path"]) == ("ok", out["path"])


@pytest.mark.parametrize("filename", [
    "../../../etc/cron.d/x", "/abs/path.sh", "..", "a/b/../../../c.py", "x.verylongsuffix",
])
async def test_import_writes_only_under_inbox_whatever_the_name_says(persons, filename):
    """FALSIFIER: the path is Nora's, built from the hash. Taken from the
    sent file's name, the first case writes outside `inbox/`."""
    world = _World()
    key = await _sent_in_room(world, b"hello", media_type="text/plain", filename=filename)
    out = await _tools(world, persons)["computer_import"](code=key[:6])
    assert out["status"] == "ok"
    written = set(world.runtime.files_by_scope[computer_unit_scope(GROUP)])
    assert written == {out["path"]}
    assert out["path"].startswith("inbox/")
    assert "/" not in out["path"].removeprefix("inbox/")
    assert ".." not in out["path"]


async def test_import_cannot_reach_another_rooms_attachment(persons):
    world = _World()
    key = await _sent_in_room(world, b"secret", media_type="text/plain", filename="s.txt",
                              conversation_id=OTHER_GROUP)
    out = await _tools(world, persons)["computer_import"](code=key[:6])
    assert out["status"] == "nothing_found"
    assert world.runtime.files_by_scope == {}
    _two_rows(world, "computer_import")


async def test_import_over_the_cap_is_refused(persons):
    world = _World()
    key = await _sent_in_room(world, b"x" * 50, media_type="text/plain", filename="x.txt")
    out = await _tools(world, persons, settings=ComputerToolSettings(import_max_bytes=10))[
        "computer_import"](code=key[:6])
    assert out["status"] == "refused"
    assert world.runtime.files_by_scope == {}
    _, finished = _two_rows(world, "computer_import")
    assert finished.status == "refused"


async def test_import_of_a_code_two_files_share_is_ambiguous(persons):
    world = _World()
    await world.records.append(message=NormalizedMessage(
        "signal", GROUP, "group", "+85290000101", "Miles", 1_000, "[file abcdef]", {},
        media_blobs=tuple(
            MediaBlob(sha256="abcdef" + digit * 58, media_type="text/plain", size=1)
            for digit in "01"
        ),
    ))
    out = await _tools(world, persons)["computer_import"](code="abcdef")
    assert out["status"] == "ambiguous"
    assert out["could_be"] == ["abcdef", "abcdef"]
    assert world.runtime.files_by_scope == {}
    _two_rows(world, "computer_import")


async def test_import_with_bytes_missing_is_unavailable(persons):
    world = _World()
    key = await _sent_in_room(world, b"gone", media_type="text/plain", filename="g.txt")
    world.blobs.blobs.clear()
    out = await _tools(world, persons)["computer_import"](code=key[:6])
    assert out["status"] == "unavailable"
    assert world.runtime.files_by_scope == {}
    _two_rows(world, "computer_import")


# -- a kept image has a code in its room (the document path takes codes) ------


async def test_a_kept_image_gets_a_code_that_resolves_only_in_its_room(persons):
    """FALSIFIER: the code is scoped by the room the image was born in. A
    document in another room cannot pull this picture by its code."""
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    out = await _tools(world, persons)["computer_export"](
        path="out/chart.png", to="keep", title="Power use")
    assert out["status"] == "ok" and out["kept"] is True
    code = out["code"]
    assert code == hashlib.sha256(CHART).hexdigest()[:6]
    assert "document" in out["note"]
    here = await resolve_room_media(world.media, code=code, platform="signal",
                                    conversation_id=GROUP)
    there = await resolve_room_media(world.media, code=code, platform="signal",
                                     conversation_id=OTHER_GROUP)
    [obj] = here.objects.values()
    assert obj.sha256 == hashlib.sha256(CHART).hexdigest()
    assert (obj.source, obj.title) == ("computer", "Power use")
    assert (there.available, dict(there.objects)) == (True, {})
    _, finished = _two_rows(world, "computer_export")
    assert finished.result["code"] == code


@pytest.mark.parametrize("form", ["{code}", "[image {code}]", "image {code}", "  {code} "])
async def test_a_kept_images_code_resolves_in_every_form_the_model_quotes(persons, form):
    """FALSIFIER: the code is the LAST token's hex. Taking every hex
    character, `[image a3f2c1]` would become `ea3f2c1` (the label's `e` and
    `a` joined to it) and resolve to nothing."""
    world = _World()
    world.put(GROUP, "out/chart.png", CHART)
    code = (await _tools(world, persons)["computer_export"](
        path="out/chart.png", to="keep"))["code"]
    got = await resolve_room_media(world.media, code=form.format(code=code),
                                   platform="signal", conversation_id=GROUP)
    assert list(got.objects) == [hashlib.sha256(CHART).hexdigest()]


async def test_an_unreadable_index_is_unavailable_never_no_such_picture(persons):
    """FALSIFIER: `by_prefix` answers an empty tuple when the database is
    down, which reads as «no such picture». The resolver uses the read that
    says it could not read."""
    world = _World()
    world.media.fail = True
    got = await resolve_room_media(world.media, code="a3f2c1", platform="signal",
                                   conversation_id=GROUP)
    assert got.available is False and dict(got.objects) == {}


async def test_a_kept_non_image_gets_no_code(persons):
    world = _World()
    world.put(GROUP, "data/prices.csv", b"city,price\nHK,10\n")
    out = await _tools(world, persons)["computer_export"](path="data/prices.csv", to="keep")
    assert out["status"] == "ok" and "code" not in out
    assert await world.media.by_prefix(
        handle=hashlib.sha256(b"city,price\nHK,10\n").hexdigest()[:6]) == ()


async def test_an_image_first_indexed_in_another_room_gets_no_code_here(persons):
    """First source wins in the media index: the same bytes kept in a second
    room are not that room's to cite, so no code is handed out there."""
    world = _World()
    world.put(OTHER_GROUP, "out/chart.png", CHART)
    world.put(GROUP, "out/chart.png", CHART)
    first = await _tools(world, persons, conversation_id=OTHER_GROUP)["computer_export"](
        path="out/chart.png", to="keep")
    second = await _tools(world, persons)["computer_export"](path="out/chart.png", to="keep")
    assert "code" in first
    assert second["status"] == "ok" and "code" not in second
