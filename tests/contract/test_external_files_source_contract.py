"""`ExternalFilesSource` (design/external-files/00-design.md §4).

One implementation today, the fake; the Google Drive adapter (S2) joins the
`source` fixture's params and must pass the same suite. What is asserted is
what the open tool (S3) will program against: `recognise` is the domain's
pure recogniser and makes no call; a read of an unknown, unshared or
unreachable file raises `ExternalFileError` with the house status, never
another exception and never a silent empty; a part is 1-based and says how
many there are; nothing is kept between opens (each read reaches the source).
"""

from __future__ import annotations

import inspect

import pytest

from nora.domain.external_files import ExternalFileError, recognise
from nora.domain.ports import ExternalFilesSource
from nora.testing.external_files import FakeExternalFilesSource, FakeFile

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
URL = f"https://docs.google.com/document/d/{ID}/edit"


@pytest.fixture(params=["fake"])
def source(request):
    return FakeExternalFilesSource(files={
        ID: FakeFile(name="Plan", parts=("one", "two"), data=b"%PDF"),
        "1NotSharedWithHer000000000": FakeFile(name="Secret", shared=False),
    })


@pytest.mark.parametrize("impl", [FakeExternalFilesSource])
def test_it_is_the_port_and_its_methods_are_keyword_only(impl):
    assert isinstance(impl(), ExternalFilesSource)
    for name in ("recognise", "describe", "read", "fetch_bytes"):
        params = [p for p in inspect.signature(getattr(impl, name)).parameters.values()
                  if p.name != "self"]
        assert all(p.kind is p.KEYWORD_ONLY for p in params), name


def test_recognise_is_the_domain_recogniser_and_reads_nothing(source):
    assert source.recognise(url=URL) == recognise(URL)
    assert source.recognise(url="https://example.com/x") is None
    assert source.calls == []


async def test_a_known_file_describes_reads_by_part_and_fetches(source):
    ref = recognise(URL)
    described = await source.describe(ref=ref)
    assert (described.name, described.kind, described.ref) == ("Plan", "document", ref)
    first = await source.read(ref=ref, part=1)
    assert (first.text, first.part, first.parts) == ("one", 1, 2)
    assert await source.fetch_bytes(ref=ref) == b"%PDF"


async def test_every_open_reaches_the_source_nothing_is_kept(source):
    ref = recognise(URL)
    await source.read(ref=ref, part=1)
    await source.read(ref=ref, part=1)
    assert source.calls.count(("read", ID)) == 2


@pytest.mark.parametrize(("file_id", "status"), [
    ("1NoSuchFile0000000000000000", "nothing_found"),
    ("1NotSharedWithHer000000000", "refused"),
])
async def test_an_unknown_or_unshared_file_raises_its_status(source, file_id, status):
    ref = recognise(f"https://drive.google.com/open?id={file_id}")
    for call in (source.describe(ref=ref), source.read(ref=ref, part=1),
                 source.fetch_bytes(ref=ref)):
        with pytest.raises(ExternalFileError) as raised:
            await call
        assert raised.value.status == status


async def test_a_part_out_of_range_is_nothing_found(source):
    with pytest.raises(ExternalFileError) as raised:
        await source.read(ref=recognise(URL), part=3)
    assert raised.value.status == "nothing_found"


async def test_an_unreachable_provider_is_unavailable(source):
    source.down = True
    with pytest.raises(ExternalFileError) as raised:
        await source.describe(ref=recognise(URL))
    assert raised.value.status == "unavailable"
