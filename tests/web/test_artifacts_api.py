"""`/app/v1/artifacts`: the files Nora sent in a room, for the people that room names
(ADR 0023).

The rooms callable is the real `rooms_named` over a real `Policy`, so these routes
exercise the actual rule, not a stub that agrees with the test by construction. The
bytes are in a real content-addressed `FakeBlobStore` and typed again off those bytes
when served.

Pinned, each against the property:
- VISIBILITY: a viewer outside the room gets the same 404 as for a file that does not
  exist, on the detail and the download; a `keep` export is never listed or served;
  leaving the room revokes on the next request.
- DISPOSITION: only a raster picture, audio or video is `inline`; SVG, PDF, text and
  everything else is an `attachment`; every response carries `nosniff`, a sandbox CSP
  and `no-store`; the file name is plain ASCII whatever the title says.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiohttp import web

from nora.app.documents_service import rooms_named
from nora.domain.computer_exports import ComputerExport
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeComputerExportStore, FakeMemoryStore, FakeRecordStore
from nora.testing.fakes import FakeBlobStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from nora.web.artifacts import download_headers, download_name
from tests.conftest import build_test_doctrine_store
from tests.web.test_api import NO_SKILLS_DIR, _fake_events

KEN = PersonRef(person_id="ken", name="Ken", honcho_peer_id="ken")
MEI = PersonRef(person_id="mei", name="Mei", honcho_peer_id="mei")
SAM = PersonRef(person_id="sam", name="Sam", honcho_peer_id="sam")
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x01" * 40
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
PDF = b"%PDF-1.7\n" + b"x" * 40
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 16
CSV = b"city,price\nHK,10\n"
PAGE = b"<!doctype html><html><body>hi</body></html>"
LINK = "a1b2c3d4e5f6"


def _policy(mei_in_r1: bool = True) -> Policy:
    r1 = frozenset({"ken", "mei"}) if mei_in_r1 else frozenset({"ken"})
    return Policy(conversations=(
        Conversation(platform="signal", ids=frozenset({"r1"}), persons=r1),
        Conversation(platform="signal", ids=frozenset({"r2"}), persons=frozenset({"sam"})),
    ))


class _World:
    def __init__(self) -> None:
        self.policy = _policy()
        self.store = FakeComputerExportStore()
        self.blobs = FakeBlobStore()

    def rooms_of(self, person_id: str) -> frozenset[tuple[str, str]]:
        return rooms_named(self.policy, person_id)

    async def export(self, data: bytes, *, to: str, room: str = "r1", path: str = "out/f",
                     title: str = "file", media_type: str = "application/octet-stream",
                     artifact_id: str | None = None, at: int = 0,
                     revised: bool = False) -> ComputerExport:
        key = await self.blobs.put(data=data)
        row = ComputerExport(
            id=uuid4(), created_at=T0 + timedelta(minutes=at), platform="signal",
            conversation_id=room, scope="ab" * 16, path=path, destination=to,
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            media_type=media_type, title=title, tag_id="t", operation_id=uuid4(),
            artifact_id=artifact_id,
            url=f"https://nora.example.test/artifacts/{artifact_id}" if artifact_id else None,
            revised=revised, blob_key=key,
        )
        await self.store.record(row)
        return row


@pytest.fixture
async def world_client(aiohttp_client):
    world = _World()
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"ken": KEN, "mei": MEI, "sam": SAM}, index={}),
        ai_peer="nora", started_at=0.0, games_client=None, presence=None,
        presence_circles=(), skills_dir=NO_SKILLS_DIR, events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
        rooms_of=world.rooms_of, blobs=world.blobs, artifacts=world.store,
        room_label=lambda platform, cid: {"r1": "Trip crew"}.get(cid, ""),
    )
    return await aiohttp_client(app), world


async def _list(client, person: str, **query: str) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in {"personId": person, **query}.items())
    resp = await client.get(f"/app/v1/artifacts?{qs}")
    assert resp.status == 200
    return await resp.json()


# ------------------------------------------------------------------ visibility
async def test_the_list_is_the_rooms_sent_files_never_a_kept_one(world_client):
    """FALSIFIER: `keep` is private; the list must not make it a share."""
    client, world = world_client
    sent = await world.export(CSV, to="chat", title="prices.csv", media_type="text/csv")
    await world.export(PNG, to="link", title="chart", media_type="image/png",
                       artifact_id=LINK, at=1)
    await world.export(PNG + b"k", to="keep", title="kept", media_type="image/png", at=2)
    body = await _list(client, "mei")
    assert [(a["id"], a["kind"], a["title"]) for a in body["artifacts"]] == [
        (LINK, "link", "chart"), (str(sent.id), "chat", "prices.csv")]
    first = body["artifacts"][0]
    assert (first["roomLabel"], first["mediaType"], first["platform"]) == (
        "Trip crew", "image/png", "signal")
    assert "conversation_id" not in first and "path" not in first


async def test_a_viewer_outside_the_room_gets_not_found_never_forbidden(world_client):
    """FALSIFIER: the room rule. Without it Sam, who is in r2 only, opens and
    downloads r1's files."""
    client, world = world_client
    sent = await world.export(CSV, to="chat", media_type="text/csv")
    await world.export(PNG, to="link", media_type="image/png", artifact_id=LINK)
    assert (await _list(client, "sam"))["artifacts"] == []
    unknown = await client.get("/app/v1/artifacts/ffffffffffff?personId=sam")
    for ref in (LINK, str(sent.id)):
        for suffix in ("", "/download"):
            resp = await client.get(f"/app/v1/artifacts/{ref}{suffix}?personId=sam")
            assert resp.status == 404, (ref, suffix)
            assert await resp.json() == await unknown.json() == {"status": "not_found"}
    # Positive control: Mei, whom r1 names, gets both.
    for ref in (LINK, str(sent.id)):
        assert (await client.get(f"/app/v1/artifacts/{ref}/download?personId=mei")).status == 200


async def test_leaving_the_room_revokes_on_the_next_request(world_client):
    client, world = world_client
    await world.export(PNG, to="link", media_type="image/png", artifact_id=LINK)
    assert (await client.get(f"/app/v1/artifacts/{LINK}/download?personId=mei")).status == 200
    world.policy = _policy(mei_in_r1=False)
    assert (await client.get(f"/app/v1/artifacts/{LINK}/download?personId=mei")).status == 404
    assert (await client.get(f"/app/v1/artifacts/{LINK}?personId=mei")).status == 404
    assert (await _list(client, "mei"))["artifacts"] == []


async def test_a_kept_file_is_not_served_by_its_id(world_client):
    client, world = world_client
    kept = await world.export(PNG, to="keep", media_type="image/png")
    resp = await client.get(f"/app/v1/artifacts/{kept.id}/download?personId=ken")
    assert resp.status == 404


async def test_a_link_serves_its_newest_revision(world_client):
    client, world = world_client
    await world.export(PNG, to="link", media_type="image/png", artifact_id=LINK, title="v1")
    await world.export(PNG + b"v2", to="link", media_type="image/png", artifact_id=LINK,
                       title="v2", at=5, revised=True)
    resp = await client.get(f"/app/v1/artifacts/{LINK}/download?personId=mei")
    assert await resp.read() == PNG + b"v2"
    assert [a["title"] for a in (await _list(client, "mei"))["artifacts"]] == ["v2"]


@pytest.mark.parametrize("ref", ["nope", "A1B2C3D4E5F6", "a1b2c3d4e5f", "..", "%2e%2e"])
async def test_a_ref_of_neither_shape_is_not_found(world_client, ref):
    client, _ = world_client
    assert (await client.get(f"/app/v1/artifacts/{ref}?personId=mei")).status == 404


# ----------------------------------------------------------------- disposition
@pytest.mark.parametrize(("data", "path", "media_type", "disposition"), [
    (PNG, "out/chart.png", "image/png", "inline"),
    (MP3, "out/song.mp3", "audio/mpeg", "inline"),
    (SVG, "out/flow.svg", "image/svg+xml", "attachment"),
    (PDF, "out/report.pdf", "application/pdf", "attachment"),
    (CSV, "out/prices.csv", "text/csv", "attachment"),
    (b"\x00\x01\x02\x03" * 8, "out/model.bin", "application/octet-stream", "attachment"),
], ids=["png", "mp3", "svg", "pdf", "csv", "opaque"])
async def test_only_pictures_audio_and_video_display_inline(
    world_client, data, path, media_type, disposition,
):
    """FALSIFIER: the portal origin holds the session cookie. Served inline, an
    SVG or a PDF runs in that origin's context; every non-media type must be a
    download, and every response must carry nosniff, a sandbox and no-store."""
    client, world = world_client
    row = await world.export(data, to="chat", path=path, title=path.rsplit("/", 1)[-1],
                             media_type=media_type)
    resp = await client.get(f"/app/v1/artifacts/{row.id}/download?personId=mei")
    assert resp.status == 200
    assert await resp.read() == data
    assert resp.headers["Content-Type"].split(";")[0] == media_type
    assert resp.headers["Content-Disposition"].split(";")[0] == disposition
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"].startswith("sandbox")
    assert "no-store" in resp.headers["Cache-Control"]


async def test_bytes_that_are_a_page_are_never_served_whatever_the_row_says(world_client):
    """The type is read off the bytes being served, not the row: a page is not a
    file the portal hands out, even under a row that says `image/png`."""
    client, world = world_client
    row = await world.export(PAGE, to="chat", path="out/chart.png", media_type="image/png")
    resp = await client.get(f"/app/v1/artifacts/{row.id}/download?personId=mei")
    assert resp.status == 404


def test_the_download_name_is_plain_ascii_whatever_the_title_says():
    row = ComputerExport(
        id=uuid4(), created_at=T0, platform="signal", conversation_id="r1", scope="s",
        path="out/report.pdf", destination="chat", sha256="c" * 64, size_bytes=1,
        media_type="application/pdf", title='旅行 "plan"\r\n../../etc', tag_id="t",
        operation_id=uuid4(), blob_key="b" * 64,
    )
    name = download_name(row)
    assert name.isascii() and name.endswith(".pdf")
    assert not any(ch in name for ch in '"/\\\r\n;')
    assert download_headers("application/pdf", name)["Content-Disposition"] == (
        f'attachment; filename="{name}"')


# ----------------------------------------------------------------- byte ranges
@pytest.mark.parametrize(("header", "status", "body", "content_range"), [
    ("bytes=0-1", 206, MP3[0:2], f"bytes 0-1/{len(MP3)}"),
    ("bytes=3-", 206, MP3[3:], f"bytes 3-{len(MP3) - 1}/{len(MP3)}"),
    ("bytes=-4", 206, MP3[-4:], f"bytes {len(MP3) - 4}-{len(MP3) - 1}/{len(MP3)}"),
    (f"bytes={len(MP3)}-", 416, b"", f"bytes */{len(MP3)}"),
    ("bytes=0-1,4-5", 200, MP3, None),
    ("items=0-1", 200, MP3, None),
], ids=["first-two", "open-end", "suffix", "past-end", "multi", "other-unit"])
async def test_a_byte_range_is_answered_with_206(world_client, header, status, body,
                                                 content_range):
    """Safari plays a <video> only after a 206 answers its `Range: bytes=0-1`, and seeking
    needs ranges everywhere. A whole-file 200 to every request leaves the portal's video
    and long audio unplayable on iOS."""
    client, world = world_client
    row = await world.export(MP3, to="chat", path="out/song.mp3", media_type="audio/mpeg")
    resp = await client.get(f"/app/v1/artifacts/{row.id}/download?personId=mei",
                            headers={"Range": header})
    assert resp.status == status
    assert await resp.read() == body
    assert resp.headers.get("Content-Range") == content_range
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_a_long_suffix_never_pushes_the_name_past_what_the_portal_repeats():
    """The portal repeats a name of at most 160 characters and says "file" past that, losing
    the suffix."""
    row = ComputerExport(
        id=uuid4(), created_at=T0, platform="signal", conversation_id="r1", scope="s",
        path="out/x." + "e" * 300, destination="chat", sha256="c" * 64, size_bytes=1,
        media_type="application/octet-stream", title="t" * 300, tag_id="t",
        operation_id=uuid4(), blob_key="b" * 64,
    )
    assert len(download_name(row)) <= 160
