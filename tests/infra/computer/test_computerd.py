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
from tests.contract.test_sandbox_runtime_contract import TOKEN, load_computerd

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
    """The review's cross-room write: a command plants `.x.computerd-tmp` as a
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
    """os.setsid() puts the child outside computerd's kill (portable: macOS
    has no setsid command); with pipes, its open
    stdout kept the call alive forever (the review measured past 40 s)."""
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


async def test_commands_run_as_the_computer_user_when_computerd_is_root(tmp_path, monkeypatch):
    """Root computerd drops every command to `computer`, so a command cannot
    read its token, kill it or bind its port. Proven on the wiring: the uid is
    handed to the subprocess."""
    computerd = load_computerd()
    monkeypatch.setattr(computerd.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        computerd.pwd, "getpwnam",
        lambda name: type("P", (), {"pw_uid": 1500, "pw_gid": 1500, "pw_dir": "/home/c"})(),
    )
    owner = computerd.Owner("computer")
    assert (owner.uid, owner.gid) == (1500, 1500)
    assert computerd.Owner(None).uid is None


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
