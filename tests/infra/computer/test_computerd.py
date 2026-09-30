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
    assert result.stdout.strip() == str((root / SCOPE).resolve())
    assert (root / SCOPE / "made.txt").read_text() == "hi\n"


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


async def test_runaway_output_is_bounded_with_a_count_of_what_was_dropped():
    computerd = load_computerd()
    sink = computerd._Bounded(100)
    sink.feed("a" * 50 + "b" * 10_000 + "z" * 50)
    rendered = sink.render()
    assert sink.truncated and sink.total == 10_100
    assert rendered.startswith("a" * 50) and rendered.endswith("z" * 25)
    assert "characters omitted" in rendered and len(rendered) < 200


async def test_the_token_never_reaches_a_child_process(served, monkeypatch):
    runtime, _, _ = served
    monkeypatch.setenv("COMPUTERD_TOKEN", TOKEN)
    result = await runtime.run(scope=SCOPE, command="env", timeout_s=10)
    assert result.status is SandboxStatus.OK
    assert TOKEN not in result.stdout
    assert os.environ["COMPUTERD_TOKEN"] == TOKEN  # it WAS in the server's env


async def test_a_symlink_out_of_the_workspace_is_denied(served):
    runtime, _, root = served
    outside = root / "outside.txt"
    outside.write_text("not yours")
    (root / SCOPE).mkdir(parents=True)
    (root / SCOPE / "link").symlink_to(outside)
    assert (await runtime.read(scope=SCOPE, path="link")).status is SandboxStatus.DENIED
    wrote = await runtime.write_text(scope=SCOPE, path="link", content="x")
    assert wrote.status is SandboxStatus.DENIED
    assert outside.read_text() == "not yours"


async def test_a_wrong_token_is_refused(served):
    _, server, _ = served
    wrong = ComputerdRuntime(base_url=str(server.make_url("")), token=TOKEN + "-wrong")
    wrote = await wrong.write_text(scope=SCOPE, path="a.txt", content="x")
    ran = await wrong.run(scope=SCOPE, command="true", timeout_s=5)
    assert wrote.status is SandboxStatus.DENIED
    assert ran.status is SandboxStatus.DENIED


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
