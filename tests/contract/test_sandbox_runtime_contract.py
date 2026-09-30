"""One suite, both implementations of `SandboxRuntime` (design/sandbox-runtime §3).

"The real implementation" is `ComputerdRuntime` talking to the REAL computerd
(`deploy/computer/computerd.py`), loaded from its file and served in-process
on a tmp workspace. So this suite exercises the actual protocol both halves
speak, not a double of one of them. The other is `FakeSandboxRuntime`.

What both must hold: a scope is a hex key; a path outside the workspace is
`denied`; a missing file is `nothing_found`, never an empty `ok`; an edit
changes exactly one match or refuses; caps are `denied`; a write reads back
byte for byte, with the sha256 of what was written.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import pytest
from aiohttp.test_utils import TestServer

from nora.domain.computer import SandboxStatus, computer_scope
from nora.domain.ports import SandboxRuntime
from nora.infra.computer.client import ComputerdRuntime
from nora.testing import FakeSandboxRuntime

TOKEN = "contract-computerd-token"  # noqa: S105
SCOPE = computer_scope("signal", "room-a")
OTHER = computer_scope("signal", "room-b")
COMPUTERD = Path(__file__).parents[2] / "deploy" / "computer" / "computerd.py"


def load_computerd() -> ModuleType:
    """computerd lives in the VM image, outside the `nora` package, so it is
    loaded from its file, as the image runs it."""
    spec = importlib.util.spec_from_file_location("computerd_under_test", COMPUTERD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["fake", "computerd"])
async def runtime(request, tmp_path: Path) -> AsyncIterator[SandboxRuntime]:
    if request.param == "fake":
        yield FakeSandboxRuntime()
        return
    app = load_computerd().create_app(root=tmp_path, token=TOKEN)
    server = TestServer(app)
    await server.start_server()
    try:
        yield ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN)
    finally:
        await server.close()


def test_both_satisfy_the_port():
    assert isinstance(FakeSandboxRuntime(), SandboxRuntime)
    assert isinstance(ComputerdRuntime(base_url="http://x", token=TOKEN), SandboxRuntime)


async def test_a_write_reads_back_byte_for_byte(runtime):
    text = "第一行\nsecond line\n"
    wrote = await runtime.write_text(scope=SCOPE, path="projects/a/notes.txt", content=text)
    assert wrote.status is SandboxStatus.OK
    assert wrote.sha256 == hashlib.sha256(text.encode()).hexdigest()
    read = await runtime.read(scope=SCOPE, path="projects/a/notes.txt")
    assert read.status is SandboxStatus.OK
    assert read.content == text
    assert read.sha256 == wrote.sha256
    assert read.lines_total == 2


async def test_a_write_and_an_edit_report_what_the_file_held_before(runtime):
    """The audit's before and after (design/sandbox-runtime §12): a new file
    has no `previous_sha256`; an overwrite and an edit report the sha256 of
    the bytes they replaced."""
    first = await runtime.write_text(scope=SCOPE, path="p/a.txt", content="one\n")
    assert first.status is SandboxStatus.OK
    assert first.previous_sha256 is None
    second = await runtime.write_text(scope=SCOPE, path="p/a.txt", content="two\n")
    assert second.previous_sha256 == hashlib.sha256(b"one\n").hexdigest()
    edited = await runtime.edit(scope=SCOPE, path="p/a.txt", old="two", new="three")
    assert edited.status is SandboxStatus.OK
    assert edited.previous_sha256 == hashlib.sha256(b"two\n").hexdigest()
    assert edited.sha256 == hashlib.sha256(b"three\n").hexdigest()


async def test_a_missing_file_is_nothing_found_not_an_empty_ok(runtime):
    for result in (
        await runtime.read(scope=SCOPE, path="nope.txt"),
        await runtime.fetch(scope=SCOPE, path="nope.txt", max_bytes=100),
        await runtime.edit(scope=SCOPE, path="nope.txt", old="a", new="b"),
    ):
        assert result.status is SandboxStatus.NOTHING_FOUND


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "a/../../b", "", ".", "a/.."])
async def test_a_path_outside_the_workspace_is_denied(runtime, path):
    wrote = await runtime.write_text(scope=SCOPE, path=path, content="x")
    assert wrote.status is SandboxStatus.DENIED
    assert (await runtime.read(scope=SCOPE, path=path)).status is SandboxStatus.DENIED


@pytest.mark.parametrize("scope", ["Room-A", "../x", "", "signal:group123"])
async def test_a_scope_must_be_a_hex_key(runtime, scope):
    wrote = await runtime.write_text(scope=scope, path="a.txt", content="x")
    assert wrote.status is SandboxStatus.DENIED


async def test_rooms_do_not_see_each_others_files(runtime):
    await runtime.write_text(scope=SCOPE, path="secret.txt", content="room a only")
    other = await runtime.read(scope=OTHER, path="secret.txt")
    assert other.status is SandboxStatus.NOTHING_FOUND
    assert (await runtime.files(scope=OTHER)).entries == ()


async def test_an_edit_changes_exactly_one_match_or_refuses(runtime):
    await runtime.write_text(scope=SCOPE, path="f.py", content="x = 1\ny = 1\n")
    assert (await runtime.edit(scope=SCOPE, path="f.py", old="= 1", new="= 2")).status \
        is SandboxStatus.DENIED  # two matches: ambiguous
    assert (await runtime.edit(scope=SCOPE, path="f.py", old="z", new="w")).status \
        is SandboxStatus.NOTHING_FOUND
    assert (await runtime.edit(scope=SCOPE, path="f.py", old="y = 1", new="y = 3")).status \
        is SandboxStatus.OK
    assert (await runtime.read(scope=SCOPE, path="f.py")).content == "x = 1\ny = 3\n"


async def test_files_lists_what_was_written_and_matches_a_pattern(runtime):
    await runtime.write_text(scope=SCOPE, path="projects/p/a.csv", content="1")
    await runtime.write_text(scope=SCOPE, path="projects/p/b.txt", content="22")
    listed = await runtime.files(scope=SCOPE, pattern="*.csv")
    assert listed.status is SandboxStatus.OK
    assert [(e.path, e.size) for e in listed.entries] == [("projects/p/a.csv", 1)]
    assert (await runtime.files(scope=SCOPE, pattern="*.pdf")).status is SandboxStatus.NOTHING_FOUND


async def test_put_and_fetch_round_trip_bytes_with_their_hash(runtime):
    data = bytes(range(256)) * 10
    stored = await runtime.put(scope=SCOPE, path="bin/blob.dat", data=data)
    assert stored.status is SandboxStatus.OK
    fetched = await runtime.fetch(scope=SCOPE, path="bin/blob.dat", max_bytes=10_000)
    assert fetched.status is SandboxStatus.OK
    assert fetched.data == data
    assert fetched.sha256 == hashlib.sha256(data).hexdigest()
    binary = await runtime.read(scope=SCOPE, path="bin/blob.dat")
    assert binary.status is SandboxStatus.OK and binary.binary and binary.content == ""


async def test_a_fetch_over_the_cap_is_denied(runtime):
    await runtime.write_text(scope=SCOPE, path="big.txt", content="x" * 100)
    assert (await runtime.fetch(scope=SCOPE, path="big.txt", max_bytes=10)).status \
        is SandboxStatus.DENIED


async def test_a_read_honours_offset_and_limit(runtime):
    await runtime.write_text(scope=SCOPE, path="lines.txt", content="a\nb\nc\nd\n")
    read = await runtime.read(scope=SCOPE, path="lines.txt", offset=1, limit=3)
    assert read.content == "b\nc"
    assert read.truncated is True


async def test_the_default_listing_includes_top_level_files(runtime):
    """`**/*` needs a literal slash in fnmatch, so it once dropped every
    top-level file, and both runtimes shared the bug."""
    await runtime.write_text(scope=SCOPE, path="top.txt", content="1")
    await runtime.write_text(scope=SCOPE, path="d/sub.txt", content="2")
    listed = await runtime.files(scope=SCOPE)
    assert sorted(e.path for e in listed.entries) == ["d/sub.txt", "top.txt"]


async def test_megabyte_transfers_round_trip_whole(runtime):
    """A 5 MB put once stored 130 KB and answered ok, and a 5 MB fetch failed
    its digest check: one socket read is not the whole body."""
    data = bytes(range(256)) * (5 * 1024 * 1024 // 256)
    stored = await runtime.put(scope=SCOPE, path="big.bin", data=data)
    assert stored.status is SandboxStatus.OK and stored.size == len(data)
    fetched = await runtime.fetch(scope=SCOPE, path="big.bin", max_bytes=len(data))
    assert fetched.status is SandboxStatus.OK and fetched.data == data


async def test_a_read_limit_of_zero_means_the_default(runtime):
    await runtime.write_text(scope=SCOPE, path="z.txt", content="abc")
    assert (await runtime.read(scope=SCOPE, path="z.txt", limit=0)).content == "abc"
