"""computerd's behaviour that only a real process can show: running commands,
timeouts, symlinks, auth, and the client's never-raise contract when the
computer is down. The file semantics both runtimes share are in
`tests/contract/test_sandbox_runtime_contract.py`."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from nora.domain.computer import SandboxStatus, computer_scope
from nora.infra.computer.client import ComputerdRuntime
from tests.contract.test_sandbox_runtime_contract import (
    COMPUTERD,
    TOKEN,
    load_computerd,
    short_tmux_root,
)

SCOPE = computer_scope("signal", "room-a")


def _same_dir(printed: str, expected: Path) -> bool:
    """Symlinked temp roots (macOS /var) print differently; compare real paths."""
    return os.path.realpath(printed) == os.path.realpath(expected)


@pytest.fixture
async def served(tmp_path: Path) -> AsyncIterator[tuple[ComputerdRuntime, TestServer, Path]]:
    server = TestServer(load_computerd().create_app(root=tmp_path, token=TOKEN))
    await server.start_server()
    try:
        yield ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN), server, tmp_path
    finally:
        await server.close()


async def test_a_command_runs_in_the_rooms_directory(served):
    runtime, _, root = served
    result = await runtime.run(scope=SCOPE, command="pwd; echo hi > made.txt", timeout_s=10)
    assert result.status is SandboxStatus.OK and result.exit_code == 0
    assert _same_dir(result.stdout.strip(), root / SCOPE)
    assert (root / SCOPE / "made.txt").read_bytes() == b"hi\n"


async def test_a_failing_command_is_ok_with_its_exit_code(served):
    runtime, _, _ = served
    result = await runtime.run(scope=SCOPE, command="echo oops >&2; exit 3", timeout_s=10)
    assert result.status is SandboxStatus.OK
    assert result.exit_code == 3
    assert "oops" in result.stderr


async def test_a_command_past_its_timeout_is_killed_and_unavailable(served):
    runtime, _, _ = served
    result = await runtime.run(scope=SCOPE, command="echo started; sleep 30", timeout_s=1)
    assert result.status is SandboxStatus.UNAVAILABLE
    assert "timed out" in (result.reason or "")
    assert "started" in result.stdout
    assert (result.duration_ms or 0) < 10_000


def test_runaway_output_is_bounded_with_a_count_of_what_was_dropped(tmp_path):
    computerd = load_computerd()
    with (tmp_path / "out").open("w+b") as f:
        f.write(b"a" * 75 + b"b" * 10_000 + b"z" * 25)
        rendered, cut = computerd._bounded(f, 100)
    assert cut is True
    assert rendered.startswith("a" * 75) and rendered.endswith("z" * 25)
    assert "10000 bytes omitted" in rendered and len(rendered) < 200


async def test_the_token_never_reaches_a_child_process(served, monkeypatch):
    runtime, _, _ = served
    monkeypatch.setenv("COMPUTERD_TOKEN", TOKEN)
    result = await runtime.run(scope=SCOPE, command="env", timeout_s=10)
    assert result.status is SandboxStatus.OK
    assert TOKEN not in result.stdout
    assert os.environ["COMPUTERD_TOKEN"] == TOKEN  # it WAS in the server's env


async def test_the_file_tools_never_follow_a_link(served):
    """A link a command planted cannot redirect a read or a write: reading it
    is refused, and writing replaces the link itself, never its target."""
    runtime, _, root = served
    outside = root / "outside.txt"
    outside.write_text("not yours")
    (root / SCOPE).mkdir(parents=True)
    (root / SCOPE / "link").symlink_to(outside)
    (root / SCOPE / "dirlink").symlink_to(root)
    assert (await runtime.read(scope=SCOPE, path="link")).status is SandboxStatus.DENIED
    assert (await runtime.read(scope=SCOPE, path="dirlink/outside.txt")).status \
        is SandboxStatus.DENIED
    wrote = await runtime.write_text(scope=SCOPE, path="link", content="x")
    assert wrote.status is SandboxStatus.OK
    assert outside.read_text() == "not yours"
    assert not (root / SCOPE / "link").is_symlink()
    through = await runtime.write_text(scope=SCOPE, path="dirlink/outside.txt", content="x")
    assert through.status is SandboxStatus.DENIED
    assert outside.read_text() == "not yours"


async def test_a_planted_temp_name_cannot_redirect_a_write(served):
    """The cross-room write: a command plants `.x.computerd-tmp` as a
    link into another room, then Nora writes `x`. The temp name is random and
    opened O_EXCL|O_NOFOLLOW, so the other room's file is untouched."""
    runtime, _, root = served
    other = computer_scope("signal", "room-b")
    await runtime.write_text(scope=other, path="secret.txt", content="room b")
    (root / SCOPE).mkdir(parents=True, exist_ok=True)
    (root / SCOPE / ".x.computerd-tmp").symlink_to(root / other / "secret.txt")
    assert (await runtime.write_text(scope=SCOPE, path="x", content="a")).status \
        is SandboxStatus.OK
    assert (root / other / "secret.txt").read_text() == "room b"


async def test_a_wrong_token_is_unavailable_not_a_refusal(served):
    """A bad token is a configuration fault. Reported as `denied`, the model
    would read it as "refused as asked"."""
    _, server, _ = served
    wrong = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN + "-wrong")
    wrote = await wrong.write_text(scope=SCOPE, path="a.txt", content="x")
    ran = await wrong.run(scope=SCOPE, command="true", timeout_s=5)
    assert wrote.status is SandboxStatus.UNAVAILABLE and "401" in (wrote.reason or "")
    assert ran.status is SandboxStatus.UNAVAILABLE


async def test_health_answers_a_body_without_a_token(served):
    _, server, _ = served
    async with ClientSession() as session, session.get(server.make_url("/v1/health")) as response:
        assert await response.json() == {"service": "computerd", "status": "ok"}


async def test_an_unreachable_computer_is_unavailable_never_a_raise():
    down = ComputerdRuntime(base_url="http://127.0.0.1:9", token=TOKEN, timeout_seconds=2)
    for result in (
        await down.run(scope=SCOPE, command="true", timeout_s=1),
        await down.read(scope=SCOPE, path="a"),
        await down.write_text(scope=SCOPE, path="a", content="x"),
        await down.files(scope=SCOPE),
        await down.fetch(scope=SCOPE, path="a", max_bytes=10),
        await down.put(scope=SCOPE, path="a", data=b"x"),
    ):
        assert result.status is SandboxStatus.UNAVAILABLE
        assert "unreachable" in (result.reason or "")


def test_a_scope_is_a_hash_and_never_carries_the_room_id():
    scope = computer_scope("signal", "group.abc+/=")
    assert len(scope) == 32 and all(c in "0123456789abcdef" for c in scope)
    assert scope != computer_scope("whatsapp", "group.abc+/=")


async def test_a_backgrounded_command_returns_without_waiting_for_it(served):
    runtime, _, _ = served
    result = await runtime.run(scope=SCOPE, command="sleep 30 & echo started", timeout_s=20)
    assert result.status is SandboxStatus.OK
    assert "started" in result.stdout
    assert (result.duration_ms or 0) < 5_000


async def test_a_child_that_escapes_the_group_cannot_hang_a_timeout(served):
    """os.setsid() puts the child outside computerd's kill (portable: macOS has no setsid
    command); with pipes, its open stdout would keep the call alive indefinitely."""
    runtime, _, _ = served
    result = await runtime.run(
        scope=SCOPE,
        command=(
            "python3 -c 'import os, time; os.setsid(); time.sleep(60)' & "
            "echo started; sleep 30"
        ),
        timeout_s=2,
    )
    assert result.status is SandboxStatus.UNAVAILABLE
    # computerd's own timeout, not the client giving up on a hung call
    assert "timed out after 2s" in (result.reason or "")
    assert result.duration_ms is not None and result.duration_ms < 10_000


async def test_home_is_the_rooms_own_directory(served):
    runtime, _, root = served
    result = await runtime.run(scope=SCOPE, command='echo "$HOME"', timeout_s=10)
    assert _same_dir(result.stdout.strip(), root / SCOPE)


def test_each_scope_runs_as_its_own_user_when_computerd_is_root(monkeypatch):
    """Root computerd maps every scope to its own system user, `c-<12 hex>`,
    created once with `useradd --system --no-create-home`, so one room's
    command cannot read another room's 0700 directory. Proven on the mapping
    with root and the user database faked; the end-to-end check (two rooms,
    one reading the other's file) runs on norty."""
    computerd = load_computerd()
    monkeypatch.setattr(computerd.os, "geteuid", lambda: 0)
    users: dict[str, int] = {}
    created: list[list[str]] = []

    def getpwnam(name):
        if name not in users:
            raise KeyError(name)
        uid = users[name]
        return type("P", (), {"pw_uid": uid, "pw_gid": uid, "pw_dir": "/nonexistent"})()

    def run(argv, **_kwargs):
        created.append(argv)
        users[argv[-1]] = 2000 + len(users)

    monkeypatch.setattr(computerd.pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(computerd.subprocess, "run", run)
    owner = computerd.Owner(isolate=True)
    other = computer_scope("signal", "room-b")

    first, again, second = owner.ids(SCOPE), owner.ids(SCOPE), owner.ids(other)
    assert first == again
    assert first != second
    assert [argv[-1] for argv in created] == [f"c-{SCOPE[:12]}", f"c-{other[:12]}"]
    assert all({"--system", "--no-create-home"} <= set(argv) for argv in created)


def test_not_root_or_not_isolating_changes_no_owner(monkeypatch):
    computerd = load_computerd()
    monkeypatch.setattr(computerd.os, "geteuid", lambda: 1000)
    assert computerd.Owner(isolate=True).ids(SCOPE) is None
    monkeypatch.setattr(computerd.os, "geteuid", lambda: 0)
    assert computerd.Owner(isolate=False).ids(SCOPE) is None


async def test_a_scope_directory_is_private_and_has_its_own_tmp(served):
    runtime, _server, root = served
    got = await runtime.run(scope=SCOPE, command='echo "$TMPDIR"', timeout_s=5)
    assert got.stdout.strip() == str(root / SCOPE / ".tmp")
    assert (root / SCOPE).stat().st_mode & 0o777 == 0o700
    assert (root / SCOPE / ".tmp").stat().st_mode & 0o777 == 0o700


async def test_a_hostile_reply_never_raises_out_of_the_client():
    from aiohttp import web
    async def bad_files(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "entries": [{"path": "a", "size": "big"}]})
    async def json_list(_: web.Request) -> web.Response:
        return web.json_response([1])
    app = web.Application()
    app.router.add_post("/v1/files", bad_files)
    app.router.add_post("/v1/{op}", json_list)
    server = TestServer(app)
    await server.start_server()
    try:
        runtime = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN)
        assert (await runtime.files(scope=SCOPE)).entries == ()
        for result in (
            await runtime.fetch(scope=SCOPE, path="a", max_bytes=10),
            await runtime.read(scope=SCOPE, path="a"),
            await runtime.run(scope=SCOPE, command="true", timeout_s=1),
        ):
            assert result.status is SandboxStatus.UNAVAILABLE
    finally:
        await server.close()


async def test_the_reserved_limits_slot_goes_on_the_wire_only_when_set():
    """`[computer] limits` is reserved: unset, the run body carries no limits
    slot; set, it carries only the limits named."""
    from aiohttp import web
    bodies: list[dict] = []

    async def run(request: web.Request) -> web.Response:
        bodies.append(await request.json())
        return web.json_response({"status": "ok", "exit_code": 0, "stdout": "", "stderr": ""})
    app = web.Application()
    app.router.add_post("/v1/run", run)
    server = TestServer(app)
    await server.start_server()
    try:
        runtime = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN)
        await runtime.run(scope=SCOPE, command="true", timeout_s=1)
        await runtime.run(scope=SCOPE, command="true", timeout_s=1, limits={})
        await runtime.run(scope=SCOPE, command="true", timeout_s=1, limits={"cpu_seconds": 5})
    finally:
        await server.close()
    assert ["limits" in b for b in bodies] == [False, False, True]
    assert bodies[2]["limits"] == {"cpu_seconds": 5}


async def test_computerd_accepts_and_ignores_the_limits_slot(served):
    runtime, _server, _root = served
    got = await runtime.run(scope=SCOPE, command="echo ran", timeout_s=5,
                            limits={"cpu_seconds": 1, "max_processes": 1})
    assert got.status is SandboxStatus.OK
    assert got.stdout == "ran\n"


def test_the_lifecycle_script_starts_computerd_with_flags_it_accepts():
    """`nora-computer ensure` once passed `--run-as computer` after computerd
    dropped that flag: computerd exited at start and the computer stayed down.
    Parse the script's own computerd command line with computerd's parser."""
    import re
    import shlex

    script = (COMPUTERD.parent / "nora-computer").read_text()
    # Anchored on the `exec sbx exec` that starts computerd: the first
    # `computerd.py` in the script is a `pkill` pattern, and matching that
    # parsed an empty argv, so the test passed with the bad flag present.
    match = re.search(
        r"exec sbx exec[^\n]*\\\n[^\n]*?/computerd\.py([^<>&'\n\\]*)", script,
    )
    assert match, "nora-computer no longer execs computerd.py where expected"
    argv = shlex.split(match.group(1).replace("\\\n", " "))
    load_computerd().build_parser().parse_args(argv)  # SystemExit on an unknown flag


async def test_state_without_a_tmux_says_unknown_never_none_running(tmp_path):
    """A computerd that cannot list tmux answers `tmux` None with a reason,
    which a reader must take as UNKNOWN: the sweeper skips such a room
    rather than expire a session a live job still holds."""
    computerd = load_computerd()
    server = TestServer(computerd.create_app(
        root=tmp_path, token=TOKEN, tmux_root=short_tmux_root(),
        tmux_bin="no-such-tmux-binary",
    ))
    await server.start_server()
    try:
        runtime = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN)
        await runtime.write_text(scope=SCOPE, path="a.txt", content="x")
        state = await runtime.state(scope=SCOPE)
        assert state.status is SandboxStatus.OK
        assert [f.path for f in state.files] == ["a.txt"]
        assert state.tmux is None and "not installed" in (state.tmux_reason or "")
    finally:
        await server.close()


async def test_a_command_gets_its_rooms_own_tmux_directory(served):
    """`tmux new` in a command and the state read must meet on one socket:
    TMUX_TMPDIR is the room's directory under the tmux root, never the
    shared default."""
    runtime, _, _ = served
    result = await runtime.run(scope=SCOPE, command="echo $TMUX_TMPDIR", timeout_s=10)
    assert result.stdout.strip().endswith(f"/{SCOPE}")
    # tmux runs a pane through SHELL; a scope user's login shell is nologin,
    # so without this every `tmux new` dies at once.
    shell = await runtime.run(scope=SCOPE, command="echo $SHELL", timeout_s=10)
    assert shell.stdout.strip() == "/bin/bash"
    other = computer_scope("signal", "room-b")
    theirs = await runtime.run(scope=other, command="echo $TMUX_TMPDIR", timeout_s=10)
    assert theirs.stdout.strip() != result.stdout.strip()


def _fake_proc(root: Path, table: dict[int, int]) -> Path:
    """A `/proc` with one `stat` per pid, `pid (comm) S ppid ...`, the comm
    holding a space and a parenthesis as real ones can."""
    for pid, ppid in table.items():
        (root / str(pid)).mkdir(parents=True)
        (root / str(pid) / "stat").write_text(f"{pid} (a (b) c) S {ppid} 1 1 0\n")
    (root / "self").mkdir()
    return root


def test_a_shell_pane_with_a_child_counts_as_running(tmp_path):
    """`bash train.sh`, a `bash -c` chain or a backgrounded server all show
    `bash` as the pane's foreground command; the child is what says a job
    is running (the sweeper and the block both read this)."""
    computerd = load_computerd()
    parents = computerd.child_parents(_fake_proc(tmp_path / "proc", {100: 1, 200: 100}))
    assert parents == frozenset({1, 100})
    running = computerd.pane_running
    assert running(dead=False, command="bash", pid=100, parents=parents) is True
    assert running(dead=False, command="bash", pid=300, parents=parents) is False
    assert running(dead=False, command="python3", pid=300, parents=parents) is True
    assert running(dead=True, command="python3", pid=100, parents=parents) is False


def test_no_proc_means_no_children_never_a_crash(tmp_path):
    assert load_computerd().child_parents(tmp_path / "missing") == frozenset()


def test_computerd_has_no_way_to_end_a_tmux_session_or_delete_a_file():
    """Never destroy work: neither the server nor the port offers a kill or a
    delete."""
    from nora.domain.ports import SandboxRuntime
    names = {n for n in dir(SandboxRuntime) if not n.startswith("_")}
    assert not {n for n in names if "kill" in n or "delete" in n or "remove" in n}
    assert "tmux-kill" not in COMPUTERD.read_text() and "kill-session" not in COMPUTERD.read_text()
