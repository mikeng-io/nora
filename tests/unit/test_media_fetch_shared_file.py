"""`media_fetch` looks at a picture or a video somebody shared a LINK to: one
neutral question, «the bytes for this code or link», answered in order by a chat
attachment's code, then a shared file's link handle or a pasted file link (the one
`ExternalFilesSource`), then a public download.

Built through `build_tool_specs`, as production builds it (the link-owner
post-pass included), over the REAL Google Drive adapter driven by
`FakeDriveServer` (no network). What is pinned, each against the property:
every way a shared file reaches the tool is looked at; a file that is not a
picture or a video points at `file_fetch`; a code a sent picture and a link
handle share is `ambiguous`; an unshared file is `refused` with the share
note; unwired is `unavailable`; nothing is cached or kept (two calls are two
fetches and two looks, with a cache that would answer anything); and neither
the answer nor the log carries the file id or the link.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import typing

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.external_files import file_refs_in
from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.google.auth import GoogleCredentials
from nora.infra.vision import VisionResult
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeBlobStore,
    FakeComputerEventStore,
    FakeMediaObjectStore,
    FakeSandboxRuntime,
    FakeVideoReadingStore,
)
from nora.testing.external_files import FakeExternalFilesSource, FakeFile
from nora.testing.fakes import FakeRecordStore
from nora.testing.google_drive import GDOC, DriveFile, FakeDriveServer

GROUP = "g1"
PHOTO = "1SynthPhotoLink00000000000"
CLIP = "1SynthClipLink000000000000"
DOC = "1SynthDocLink000000000000"
UNSHARED = "1SynthNotShared0000000000"
ARCHIVE = "1SynthArchiveLink000000000"
#: Two file ids whose handles share one code (found by search: 24 bits).
TWIN_A, TWIN_B, TWIN_CODE = (
    "1SynthTwin000000000000458", "1SynthTwin000000000000677", "87b845",
)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-photo"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_NOW = int(time.time() * 1000)


def _code(file_id: str) -> str:
    return hashlib.sha256(f"google_drive:{file_id}".encode()).hexdigest()[:6]


def _url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


class _Vision:
    """Counts looks and keeps the bytes each one saw."""

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    async def describe(self, *, data: bytes, media_type: str, prompt: str = "",
                       model: str = "", provider: str = "omniroute",
                       max_bytes: int | None = None,
                       windows: int | None = None) -> VisionResult:
        self.seen.append(data)
        return VisionResult(available=True, description=f"look {len(self.seen)}")


class _AlwaysHitCache:
    """A reading cache that would answer ANY key: a shared file's look that
    consulted it would be served `stale`, so its absence is observable."""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.puts: list[str] = []

    async def aget(self, key: str) -> str | None:
        self.gets.append(key)
        return "stale"

    async def aput(self, key: str, value: str, *, model: str) -> None:
        self.puts.append(key)


def _server() -> FakeDriveServer:
    return FakeDriveServer(files={
        PHOTO: DriveFile("beach.png", "image/png", data=PNG),
        CLIP: DriveFile("clip.mp4", "video/mp4", data=b"\x00\x00\x00\x18ftypmp42"),
        DOC: DriveFile("Trip plan", GDOC, exports={DOCX: b"PK-docx-trip"}),
        UNSHARED: DriveFile("Secret", "image/png", data=PNG, shared=False),
        ARCHIVE: DriveFile("photos.zip", "application/zip", data=b"PK-zip"),
        TWIN_A: DriveFile("a.png", "image/png", data=PNG),
        TWIN_B: DriveFile("b.png", "image/png", data=PNG),
    })


def _drive(server: FakeDriveServer) -> GoogleDriveSource:
    return GoogleDriveSource(
        credentials=GoogleCredentials(
            client_id="cid", client_secret="cs",  # noqa: S106 - a test value
            refresh_token=server.refresh_token, metrics=Metrics(), transport=server.transport,
        ),
        transport=server.transport,
    )


async def _store(*bodies: str, blobs: tuple[MediaBlob, ...] = (),
                 conv: str = GROUP) -> FakeRecordStore:
    store = FakeRecordStore()
    for n, body in enumerate(bodies):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id=conv, conversation_type="group",
            sender_id="+85200000000", sender_name="A", timestamp=_NOW - 60_000 + n,
            body=body, raw={}, external_files=file_refs_in(body), media_blobs=blobs,
        ))
    return store


class _World:
    def __init__(self, source, store=None, *, video: bool = False,
                 blobs: FakeBlobStore | None = None, computer: bool = False) -> None:
        self.vision = _Vision()
        self.cache = _AlwaysHitCache()
        self.readings = FakeVideoReadingStore()
        self.blobs = blobs or FakeBlobStore()
        self.index = FakeMediaObjectStore([])
        models = {"image": "test/image-model"}
        if video:
            models["video"] = "test/video-model"
        specs = build_tool_specs(
            unit_for=lambda platform, cid: cid,
            store=store or FakeRecordStore(), conversation_id=GROUP,
            conversation_type="group", persons=_NoPersons(), honcho=None, deliverer=None,
            metrics=Metrics(),
            room=Room(platform="signal", conversation_id=GROUP, roster_source=None,
                      persons=_NoPersons()),
            transit_source=None, vision_source=self.vision, blob_store=self.blobs,
            vision_cache=self.cache, video_readings=self.readings,
            describe_models=models, media_store=self.index, external_files=source,
            **({"computer": FakeSandboxRuntime(), "computer_events": FakeComputerEventStore()}
               if computer else {}),
        )
        self.tools = {s.name: s.call for s in specs}

    async def look(self, **kw) -> dict:
        return await self.tools["media_fetch"](**kw)

    def kept_nothing(self) -> None:
        assert self.cache.gets == [] and self.cache.puts == []
        assert self.readings.gets == [] and self.readings.puts == []
        assert self.blobs.blobs == {} and self.index.saved == []


def _downloads(server: FakeDriveServer, file_id: str) -> int:
    return sum(1 for r in server.requests
               if r.url.path.endswith(f"/{file_id}") and r.url.params.get("alt") == "media")


def _exports(server: FakeDriveServer, file_id: str) -> int:
    return sum(1 for r in server.requests if r.url.path.endswith(f"/{file_id}/export"))


class _LinkLookupFails(FakeRecordStore):
    """A record whose link rows cannot be read while its media rows can."""

    async def external_file_rows(self, **kwargs):
        raise RuntimeError("link rows unreadable")


def _no_id_or_url(text: str, *file_ids: str) -> None:
    assert "http" not in text.lower() and "google" not in text.lower(), text
    for file_id in file_ids:
        assert file_id not in text, text


# ---- every way in is looked at ------------------------------------------------


@pytest.mark.parametrize("args", [
    {"code": _code(PHOTO)},                                   # a bare code, this room
    {"code": f"[Google Drive link {_code(PHOTO)}]"},          # the handle as shown
    {"url": f"Google Drive link {_code(PHOTO)}"},             # the handle in `url`
    {"url": f"https://drive.google.com/file/d/{_code(PHOTO)}/view"},  # a made-up link
    {"url": _url(PHOTO)},                                     # the pasted link
    {"code": _url(PHOTO)},                                    # a link pasted into `code`
])
async def test_a_shared_picture_is_looked_at_through_the_source(args):
    server = _server()
    world = _World(_drive(server), await _store(f"look {_url(PHOTO)}"))
    out = await world.look(**args)
    assert out["status"] == "ok", out
    assert out["description"] == "look 1"
    assert out["code"] == _code(PHOTO)
    assert world.vision.seen == [PNG]
    _no_id_or_url(json.dumps(out), PHOTO)


async def test_two_looks_are_two_fetches_and_nothing_is_kept():
    """NEVER CACHED: a linked file changes. A cache
    that would answer anything is never asked, nothing is written to it, to
    custody or to the index, and each call fetches the bytes afresh."""
    server = _server()
    world = _World(_drive(server), await _store(_url(PHOTO)))
    # The same bytes were once sent and read: a stored reading by content
    # hash is a cache too, and a linked file never reads it.
    await world.index.comprehend(
        sha256=hashlib.sha256(PNG).hexdigest(), title="stale", description="stale",
        content="stale", model="test/image-model",
    )
    saved_before = list(world.index.saved)
    first = await world.look(code=_code(PHOTO))
    second = await world.look(code=_code(PHOTO))
    assert [first["description"], second["description"]] == ["look 1", "look 2"]
    assert _downloads(server, PHOTO) == 2
    assert world.index.saved == saved_before
    world.index.saved = []
    world.kept_nothing()


async def test_a_shared_clip_skips_the_video_reading_cache():
    server = _server()
    world = _World(_drive(server), await _store(_url(CLIP)), video=True)
    out = await world.look(code=_code(CLIP))
    assert out["status"] == "ok", out
    assert _downloads(server, CLIP) == 1 and len(world.vision.seen) == 1
    world.kept_nothing()


# ---- the negatives -----------------------------------------------------------


async def test_a_shared_file_that_is_not_a_picture_points_at_file_fetch():
    server = _server()
    world = _World(_drive(server), await _store(f"https://docs.google.com/document/d/{DOC}/edit"))
    out = await world.look(code=_code(DOC))
    assert out["status"] == "not_an_image"
    assert "file_fetch" in out["note"] and _code(DOC) in out["note"]
    assert world.vision.seen == []
    _no_id_or_url(json.dumps(out), DOC)


async def test_asking_about_a_doc_exports_and_downloads_nothing():
    """The source refuses a file that is not a picture or a video from its
    one metadata call: a Doc is never exported just to say so."""
    server = _server()
    world = _World(_drive(server), await _store(f"https://docs.google.com/document/d/{DOC}/edit"))
    out = await world.look(code=_code(DOC))
    assert out["status"] == "not_an_image"
    assert _exports(server, DOC) == 0 and _downloads(server, DOC) == 0
    assert server.reads(DOC) == 1  # the metadata call alone


@pytest.mark.parametrize("computer", [False, True])
async def test_a_shared_file_that_is_not_text_is_not_sent_to_file_fetch(computer):
    server = _server()
    world = _World(_drive(server), await _store(_url(ARCHIVE)), computer=computer)
    out = await world.look(code=_code(ARCHIVE))
    assert out["status"] == "not_an_image"
    assert "file_fetch" not in out["note"]
    assert "look at or read here" in out["note"]
    assert ("computer_import" in out["note"]) is computer
    assert _downloads(server, ARCHIVE) == 0


class _IgnoresAccept(FakeExternalFilesSource):
    """A source that broke the port's promise and returns a Doc's bytes to a
    caller that asked for a picture."""

    async def fetch_bytes(self, *, ref, accept=()):
        return await super().fetch_bytes(ref=ref)


async def test_a_source_ignoring_accept_still_never_sends_a_doc_to_vision():
    """The tail's modality check refuses it like any file: no second check."""
    source = _IgnoresAccept(files={DOC: FakeFile(name="Trip plan", data=b"PK-docx",
                                                 media_type=DOCX)})
    world = _World(source)
    out = await world.look(url=f"https://docs.google.com/document/d/{DOC}/edit")
    assert out["status"] == "not_an_image", out
    assert world.vision.seen == []


async def test_with_no_picture_matched_an_unreadable_link_lookup_is_unavailable():
    """«I couldn't check», never «there is nothing»: the link rows could not
    be read and no sent picture has the code (R-27)."""
    world = _World(_drive(_server()), _LinkLookupFails())
    for code in (_code(PHOTO), f"[Google Drive link {_code(PHOTO)}]"):
        out = await world.look(code=code)
        assert out["status"] == "unavailable", out


@pytest.mark.parametrize("code", [TWIN_CODE, f"[Google Drive link {TWIN_CODE}]"])
async def test_two_link_handles_with_one_code_are_ambiguous_and_fetch_nothing(code):
    assert _code(TWIN_A) == _code(TWIN_B) == TWIN_CODE
    server = _server()
    world = _World(_drive(server), await _store(f"{_url(TWIN_A)} and {_url(TWIN_B)}"))
    out = await world.look(code=code)
    assert out["status"] == "ambiguous", out
    assert server.reads(TWIN_A) == 0 and server.reads(TWIN_B) == 0
    _no_id_or_url(json.dumps(out), TWIN_A, TWIN_B)


async def test_a_link_the_domain_knows_and_the_source_does_not_is_unavailable(monkeypatch):
    """A provider this deployment's source cannot open: `unavailable`, and
    never the public download of its sign-in page."""
    async def no_download(url: str) -> tuple[bytes, str]:
        raise AssertionError("a file link must never reach the public download")
    monkeypatch.setattr("nora.agent.tools._download_media", no_download)
    world = _World(FakeExternalFilesSource(name="another_provider"))
    for args in ({"url": _url(PHOTO)}, {"code": _url(PHOTO)}):
        out = await world.look(**args)
        assert out["status"] == "unavailable", out
        _no_id_or_url(json.dumps(out), PHOTO)


async def test_a_labelled_handle_names_the_file_not_a_picture_sharing_its_code():
    """`[Google Drive link 87b845]` already says it is a file's link: a sent
    picture whose hash starts with the same hex does not make it ambiguous.
    The bare code still is (below)."""
    server = _server()
    collide = MediaBlob(sha256=_code(PHOTO) + "0" * 58, media_type="image/png", size=3)
    store = await _store(f"[image {_code(PHOTO)}] and {_url(PHOTO)}", blobs=(collide,))
    world = _World(_drive(server), store)
    out = await world.look(code=f"[Google Drive link {_code(PHOTO)}]")
    assert out["status"] == "ok", out
    assert world.vision.seen == [PNG]


async def test_a_code_a_sent_picture_and_a_link_handle_share_is_ambiguous():
    server = _server()
    collide = MediaBlob(sha256=_code(PHOTO) + "0" * 58, media_type="image/png", size=3)
    store = await _store(f"[image {_code(PHOTO)}] and {_url(PHOTO)}", blobs=(collide,))
    world = _World(_drive(server), store)
    out = await world.look(code=_code(PHOTO))
    assert out["status"] == "ambiguous" and "link" in out["note"]
    assert server.reads(PHOTO) == 0 and world.vision.seen == []


async def test_an_unshared_file_is_refused_with_the_share_note():
    server = _server()
    world = _World(_drive(server), await _store(_url(UNSHARED)))
    for args in ({"code": _code(UNSHARED)}, {"url": _url(UNSHARED)}):
        out = await world.look(**args)
        assert out["status"] == "refused", out
        assert "nora@67.zone" in out["note"]
        _no_id_or_url(json.dumps(out), UNSHARED)
    assert world.vision.seen == []


async def test_unwired_a_file_link_is_unavailable_and_never_downloaded(monkeypatch):
    async def no_download(url: str) -> tuple[bytes, str]:
        raise AssertionError("a file link must never reach the public download")
    monkeypatch.setattr("nora.agent.tools._download_media", no_download)
    world = _World(None, await _store(_url(PHOTO)))
    for args in ({"code": _code(PHOTO)}, {"url": _url(PHOTO)}):
        out = await world.look(**args)
        assert out["status"] == "unavailable", out
        _no_id_or_url(json.dumps(out), PHOTO)


async def test_a_link_handle_from_another_room_is_nothing_found():
    server = _server()
    world = _World(_drive(server), await _store(_url(PHOTO), conv="g2"))
    out = await world.look(code=_code(PHOTO))
    assert out["status"] == "nothing_found"
    assert server.reads(PHOTO) == 0


async def test_neither_the_answer_nor_the_log_carries_the_id_or_url(caplog):
    caplog.set_level(logging.DEBUG)
    server = _server()
    world = _World(_drive(server), await _store(f"{_url(PHOTO)} {_url(UNSHARED)}"))
    answers = [
        await world.look(code=_code(PHOTO)), await world.look(url=_url(PHOTO)),
        await world.look(code=_code(UNSHARED)), await world.look(url=_url(UNSHARED)),
    ]
    assert [a["status"] for a in answers] == ["ok", "ok", "refused", "refused"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # The positive control: the provider's requests WERE logged, redacted.
    assert "/drive/v3/files/<file>" in logged, logged
    for secret in (PHOTO, UNSHARED, _url(PHOTO), _url(UNSHARED)):
        assert secret not in logged, logged
    _no_id_or_url(json.dumps(answers), PHOTO, UNSHARED)


async def test_a_sent_picture_is_still_its_own_code():
    """Step 1 unchanged: a code only a sent picture has is served from it,
    and the source is never asked."""
    server = _server()
    blobs = FakeBlobStore()
    key = await blobs.put(data=PNG)
    store = await _store(f"[image {key[:6]}]",
                         blobs=(MediaBlob(sha256=key, media_type="image/png", size=len(PNG)),))
    world = _World(_drive(server), store, blobs=blobs)
    out = await world.look(code=key[:6])
    assert out["status"] == "ok" and out["code"] == key[:6]
    # A sent picture's reading is still cached by content (the positive
    # control for `kept_nothing`): this cache answers anything.
    assert out["description"] == "stale" and world.cache.gets == [key]
    assert server.requests == []
