"""`ExternalFilesSource`: the port the open tool programs against.

Two implementations, the fake and the Google Drive adapter (driven through
`FakeDriveServer`, no network); both pass the same suite. What is asserted
is what the open tool programs against: `recognise` is the domain's pure
recogniser and makes no call; a read of a deleted, unshared or unreachable
file raises `ExternalFileError` with the house status, never another
exception and never a silent empty; a part is 1-based and says how many
there are; nothing is kept between opens (each read reaches the source).

The one difference the suite carries per implementation is which file is
"deleted": the fake has a missing id, while Drive answers an unknown id with
the same 404 as an unshared one (refused, the share is the fix), so its
deleted file is one in the bin.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from nora.domain.external_files import NOT_ACCEPTED, ExternalFileError, recognise
from nora.domain.ports import ExternalFilesSource
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.google.auth import GoogleCredentials
from nora.obs.metrics import Metrics
from nora.testing.external_files import FakeExternalFilesSource, FakeFile
from nora.testing.google_drive import GDOC, DriveFile, FakeDriveServer

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
UNSHARED = "1NotSharedWithHer000000000"
URL = f"https://docs.google.com/document/d/{ID}/edit"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass
class World:
    source: ExternalFilesSource
    deleted_id: str
    reads: Callable[[str], int]
    go_down: Callable[[], None]


def _fake() -> World:
    source = FakeExternalFilesSource(files={
        ID: FakeFile(name="Plan", parts=("one", "two"), data=b"%PDF", media_type=DOCX),
        UNSHARED: FakeFile(name="Secret", shared=False),
    })

    def down() -> None:
        source.down = True

    return World(source, "1NoSuchFile0000000000000000",
                 lambda fid: source.calls.count(("read", fid)), down)


def _google() -> World:
    server = FakeDriveServer(files={
        ID: DriveFile("Plan", GDOC, exports={"text/markdown": b"one\ntwo", DOCX: b"%PDF"}),
        UNSHARED: DriveFile("Secret", GDOC, shared=False),
        "1InTheBin00000000000000000": DriveFile("Old", GDOC, trashed=True),
    })
    source = GoogleDriveSource(
        credentials=GoogleCredentials(
            client_id="cid", client_secret="cs",  # noqa: S106 - a test value
            refresh_token=server.refresh_token, metrics=Metrics(), transport=server.transport,
        ),
        transport=server.transport, part_chars=3,
    )

    def down() -> None:
        server.down = True

    return World(source, "1InTheBin00000000000000000", server.reads, down)


@pytest.fixture(params=["fake", "google_drive"])
def world(request) -> World:
    return {"fake": _fake, "google_drive": _google}[request.param]()


@pytest.mark.parametrize("impl", [FakeExternalFilesSource, GoogleDriveSource])
def test_its_methods_are_keyword_only(impl):
    for name in ("recognise", "describe", "read", "fetch_bytes"):
        params = [p for p in inspect.signature(getattr(impl, name)).parameters.values()
                  if p.name != "self"]
        assert all(p.kind is p.KEYWORD_ONLY for p in params), name


def test_it_is_the_port(world):
    assert isinstance(world.source, ExternalFilesSource)
    assert world.source.name == "google_drive"


def test_recognise_is_the_domain_recogniser_and_reads_nothing(world):
    assert world.source.recognise(url=URL) == recognise(URL)
    assert world.source.recognise(url="https://example.com/x") is None
    assert world.reads(ID) == 0


async def test_a_known_file_describes_reads_by_part_and_fetches(world):
    ref = recognise(URL)
    described = await world.source.describe(ref=ref)
    assert (described.name, described.kind, described.ref) == ("Plan", "document", ref)
    first = await world.source.read(ref=ref, part=1)
    assert (first.text, first.part, first.parts) == ("one", 1, 2)
    got = await world.source.fetch_bytes(ref=ref)
    # A native document is fetched as the office format it exports to.
    assert (got.data, got.media_type) == (b"%PDF", DOCX)


async def test_fetch_bytes_refuses_a_type_it_was_not_asked_for_before_fetching(world):
    """`accept` refuses from metadata alone, with the file's kind; an empty
    `accept` (every caller but `media_fetch`) takes every type. That the
    Drive adapter exports nothing first is pinned against `FakeDriveServer`
    (`tests/unit/test_media_fetch_shared_file.py`)."""
    ref = recognise(URL)
    with pytest.raises(ExternalFileError) as raised:
        await world.source.fetch_bytes(ref=ref, accept=("image/", "video/"))
    assert (raised.value.status, raised.value.reason, raised.value.kind) == (
        "refused", NOT_ACCEPTED, "document")
    got = await world.source.fetch_bytes(ref=ref, accept=("application/",))
    assert got.media_type == DOCX


async def test_every_open_reaches_the_source_nothing_is_kept(world):
    ref = recognise(URL)
    await world.source.read(ref=ref, part=1)
    once = world.reads(ID)
    await world.source.read(ref=ref, part=1)
    assert once > 0 and world.reads(ID) == 2 * once


@pytest.mark.parametrize("which", ["deleted", "unshared"])
async def test_a_deleted_or_unshared_file_raises_its_status(world, which):
    file_id, status = (
        (world.deleted_id, "nothing_found") if which == "deleted" else (UNSHARED, "refused")
    )
    ref = recognise(f"https://drive.google.com/open?id={file_id}")
    for call in (world.source.describe(ref=ref), world.source.read(ref=ref, part=1),
                 world.source.fetch_bytes(ref=ref)):
        with pytest.raises(ExternalFileError) as raised:
            await call
        assert raised.value.status == status


async def test_a_part_out_of_range_is_nothing_found(world):
    with pytest.raises(ExternalFileError) as raised:
        await world.source.read(ref=recognise(URL), part=3)
    assert raised.value.status == "nothing_found"


async def test_an_unreachable_provider_is_unavailable(world):
    world.go_down()
    with pytest.raises(ExternalFileError) as raised:
        await world.source.describe(ref=recognise(URL))
    assert raised.value.status == "unavailable"
