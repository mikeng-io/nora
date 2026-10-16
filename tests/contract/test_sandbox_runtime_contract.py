"""One suite, both implementations of `SandboxRuntime`.

"The real implementation" is `ComputerdRuntime` talking to the REAL computerd
(`deploy/computer/computerd.py`), loaded from its file and served in-process on a tmp
workspace. So this suite exercises the actual protocol both halves speak, not a double of
one of them. The other is `FakeSandboxRuntime`.

What both must hold: a scope is a hex key; a path outside the workspace is `denied`; a
missing file is `nothing_found`, never an empty `ok`; an edit changes exactly one match or
refuses; caps are `denied`; a write reads back byte for byte, with the sha256 of what was
written. The observed-state read (`state`) lists the newest files newest first and hides
what no one would name; tmux sessions are per room, a job reads as running and a shell at
its prompt does not.

The tmux half needs a real `tmux` for the computerd side and is SKIPPED where there is
none; the image installs it (`deploy/computer/Dockerfile`).
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import importlib.util
import shutil
import sys
import tempfile
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


def short_tmux_root() -> Path:
    """A tmux root short enough for a Unix socket path (about 104 bytes),
    which a pytest `tmp_path` is not."""
    return Path(tempfile.mkdtemp(prefix="ct", dir="/tmp"))


@pytest.fixture(params=["fake", "computerd"])
async def runtime(request, tmp_path: Path) -> AsyncIterator[SandboxRuntime]:
    if request.param == "fake":
        yield FakeSandboxRuntime()
        return
    tmux_root = short_tmux_root()
    app = load_computerd().create_app(root=tmp_path, token=TOKEN, tmux_root=tmux_root)
    server = TestServer(app)
    await server.start_server()
    try:
        yield ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN)
    finally:
        await server.close()
        await _kill_tmux_servers(tmux_root)
        shutil.rmtree(tmux_root, ignore_errors=True)


async def _kill_tmux_servers(tmux_root: Path) -> None:
    """Every tmux server a test started under `tmux_root`, ended."""
    if shutil.which("tmux") is None:
        return
    for sock in glob.glob(f"{tmux_root}/*/tmux-*/default"):
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-S", sock, "kill-server",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


async def start_job(runtime: SandboxRuntime, scope: str, name: str, *, running: bool) -> None:
    """A tmux session in `scope`: a job (`sleep`) or a bare shell. The fake
    runs nothing, so it is told; computerd gets the command a model would run."""
    if isinstance(runtime, FakeSandboxRuntime):
        runtime.start_tmux(scope, name, running=running)
        return
    command = f"tmux new-session -d -s {name}" + (" 'sleep 60'" if running else "")
    ran = await runtime.run(scope=scope, command=command, timeout_s=10)
    assert ran.status is SandboxStatus.OK and ran.exit_code == 0, ran
    await asyncio.sleep(0.3)  # the shell reaches its prompt


def needs_tmux(runtime: SandboxRuntime) -> None:
    if not isinstance(runtime, FakeSandboxRuntime) and shutil.which("tmux") is None:
        pytest.skip("no tmux on this machine; the computer image installs it")


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
    """The audit's before and after: a new file has no `previous_sha256`; an overwrite and an
    edit report the sha256 of the bytes they replaced."""
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
    """`**/*` needs a literal slash in fnmatch, so a naive match drops every top-level file;
    both runtimes must list them."""
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


# -- the observed state -------------------------------------------------


async def test_state_lists_the_newest_files_first_with_size_and_age(runtime):
    await runtime.write_text(scope=SCOPE, path="projects/a/old.csv", content="1,2\n")
    await asyncio.sleep(0.02)
    await runtime.write_text(scope=SCOPE, path="new.txt", content="hello")
    state = await runtime.state(scope=SCOPE)
    assert state.status is SandboxStatus.OK
    assert [(f.path, f.size) for f in state.files] == [("new.txt", 5), ("projects/a/old.csv", 4)]
    assert state.files[0].mtime > state.files[1].mtime
    assert state.files[0].mtime.tzinfo is not None


async def test_state_hides_dot_paths_and_tool_caches(runtime):
    await runtime.write_text(scope=SCOPE, path="kept.py", content="x")
    for hidden in (".bashrc", ".venv/lib/a.py", "p/node_modules/m/i.js", "p/__pycache__/c.pyc"):
        await runtime.write_text(scope=SCOPE, path=hidden, content="x")
    state = await runtime.state(scope=SCOPE)
    assert [f.path for f in state.files] == ["kept.py"]


async def test_state_of_an_unused_room_is_ok_and_empty(runtime):
    state = await runtime.state(scope=OTHER)
    assert state.status is SandboxStatus.OK
    assert state.files == ()


async def test_state_caps_the_file_list_at_fifty(runtime):
    for i in range(55):
        await runtime.write_text(scope=SCOPE, path=f"f{i:02}.txt", content="x")
    assert len((await runtime.state(scope=SCOPE)).files) == 50


@pytest.mark.parametrize("scope", ["Room-A", "../x", ""])
async def test_state_refuses_a_scope_that_is_not_a_hex_key(runtime, scope):
    assert (await runtime.state(scope=scope)).status is SandboxStatus.DENIED


async def test_a_tmux_job_is_listed_as_running_and_a_shell_is_not(runtime):
    needs_tmux(runtime)
    await start_job(runtime, SCOPE, "scrape", running=True)
    await start_job(runtime, SCOPE, "shell", running=False)
    state = await runtime.state(scope=SCOPE)
    assert state.tmux is not None
    listed = {t.name: t.running for t in state.tmux}
    assert listed == {"scrape": True, "shell": False}
    assert all(t.created.tzinfo is not None for t in state.tmux)


async def test_tmux_sessions_are_per_room(runtime):
    needs_tmux(runtime)
    await start_job(runtime, SCOPE, "mine", running=True)
    other = await runtime.state(scope=OTHER)
    assert other.tmux == ()


async def test_state_lists_a_process_the_room_owns_outside_tmux(runtime):
    """A `nohup … &` job: no tmux session, still a process of the room's
    user. computerd lists processes only when scopes are isolated (root, in
    the image); here it runs as a normal user, so it must answer None
    (unknown, never an empty list) rather than every room's processes."""
    scope = "cd" * 16
    if isinstance(runtime, FakeSandboxRuntime):
        runtime.start_process(scope, "python tick.py", pid=159)
        got = await runtime.state(scope=scope)
        assert got.processes is not None
        assert [p.command for p in got.processes] == ["python tick.py"]
        return
    got = await runtime.state(scope=scope)
    assert got.status is SandboxStatus.OK and got.processes is None
    assert got.processes_reason
