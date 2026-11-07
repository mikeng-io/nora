"""computerd's per-request instance enforcement and caller-loss kill
(`deploy/computer/computerd.py`, design/browser-runtime «the instance»).

What is covered, against the real server and real subprocesses:

- a scoped computerd serves only requests pinning ITS boot: a missing or
  mismatched `X-Computerd-Instance` is a 409 `denied`, never executed, and
  every answer is stamped with the boot identity so a client that pinned
  one boot can tell when a different one answered;
- `ComputerdRuntime` sends the header on every verb and refuses a reply
  that does not carry the same stamp — a replaced VM at the same URL
  reads `unavailable`, never silently serves;
- a caller that went away has its command's process GROUP killed — the
  stopped HTTP awaiter is not a stopped guest command.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sys
import time
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

COMPUTERD = Path(__file__).parents[2] / "deploy" / "computer" / "computerd.py"
TOKEN = "t"  # noqa: S105 - a test token
SCOPE = "ab" * 16
BOOT = "boot-under-test"


@pytest.fixture(scope="module")
def computerd():
    spec = importlib.util.spec_from_file_location(
        "computerd_instance_under_test", COMPUTERD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _serve(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", runner


def _app(computerd, tmp_path: Path) -> web.Application:
    return computerd.create_app(
        root=tmp_path / "root", token=TOKEN, isolate_scopes=False,
        tmux_root=tmp_path / "tmux", proc_root=tmp_path,
        bound_scope=SCOPE, boot_id=BOOT)


async def test_scoped_computerd_refuses_missing_and_wrong_instance(
        computerd, tmp_path) -> None:
    """A bound computerd executes only calls that pin its boot: no header
    or a stale one is 409 `denied`; the admitted call answers with the
    stamp on it."""
    url, runner = await _serve(_app(computerd, tmp_path))
    try:
        async with aiohttp.ClientSession() as s:
            auth = {"Authorization": f"Bearer {TOKEN}"}
            body = {"scope": SCOPE, "pattern": "*.none"}
            async with s.post(f"{url}/v1/files", headers=auth, json=body) as r:
                assert r.status == 409
                assert (await r.json())["status"] == "denied"
            async with s.post(
                    f"{url}/v1/files", json=body,
                    headers={**auth, "X-Computerd-Instance": "boot-9"}) as r:
                assert r.status == 409
            good = {**auth, "X-Computerd-Instance": BOOT}
            async with s.post(f"{url}/v1/files", headers=good, json=body) as r:
                assert r.status == 200
                assert r.headers["X-Computerd-Instance"] == BOOT
    finally:
        await runner.cleanup()


async def test_client_sends_the_header_and_refuses_an_unstamped_answer(
        computerd, tmp_path) -> None:
    """The runtime pins every request to the admitted instance: a bound
    computerd denies the wrong pin, and a reply with NO stamp — an old or
    replaced computerd — is `unavailable`, never trusted."""
    from nora.domain.computer import SandboxStatus
    from nora.infra.computer.client import ComputerdRuntime

    url, runner = await _serve(_app(computerd, tmp_path))
    try:
        right = ComputerdRuntime(base_url=url, token=TOKEN, instance=BOOT)
        got = await right.state(scope=SCOPE)
        assert got.status is SandboxStatus.OK
        wrong = ComputerdRuntime(base_url=url, token=TOKEN, instance="boot-9")
        assert (await wrong.state(scope=SCOPE)).status is SandboxStatus.UNAVAILABLE
    finally:
        await runner.cleanup()

    # A computerd that never learned to stamp answers without the header:
    # the pinned client refuses it.
    async def unstamped(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "files": [], "tmux": [],
                                  "processes": []})

    app = web.Application()
    app.router.add_post("/v1/state", unstamped)
    url, runner = await _serve(app)
    try:
        pinned = ComputerdRuntime(base_url=url, token=TOKEN, instance=BOOT)
        assert (await pinned.state(scope=SCOPE)).status is SandboxStatus.UNAVAILABLE
    finally:
        await runner.cleanup()


async def test_a_scoped_computerd_with_no_boot_identity_serves_nothing(
        computerd, tmp_path) -> None:
    """A scoped computerd that cannot name its boot fails CLOSED, before
    the effect: the request is denied and the guest executes nothing — a
    client can only refuse an unstamped ANSWER after it already ran."""
    app = computerd.create_app(
        root=tmp_path / "root", token=TOKEN, isolate_scopes=False,
        tmux_root=tmp_path / "tmux", proc_root=tmp_path,
        bound_scope=SCOPE, boot_id="")
    url, runner = await _serve(app)
    try:
        async with aiohttp.ClientSession() as s:
            auth = {"Authorization": f"Bearer {TOKEN}",
                    "X-Computerd-Instance": "anything"}
            # An effectful write: denied BEFORE the file can be created.
            async with s.post(f"{url}/v1/put?scope={SCOPE}&path=made.txt",
                              headers=auth, data=b"payload") as r:
                assert r.status == 409
            # A guest command: denied BEFORE anything can run.
            async with s.post(f"{url}/v1/run", headers=auth, json={
                    "scope": SCOPE, "timeout_s": 2,
                    "command": "echo ran > ran.txt"}) as r:
                assert r.status == 409
            # The identity read still answers — it is how the client
            # learns the VM cannot be pinned — but nothing else runs.
            async with s.get(f"{url}/v1/identity", headers=auth) as r:
                assert r.status == 200
                assert (await r.json())["instance"] == ""
    finally:
        await runner.cleanup()
    # Negative control, independent of the HTTP status: no file exists,
    # no command ran.
    assert not (tmp_path / "root" / SCOPE / "made.txt").exists()
    assert not (tmp_path / "root" / SCOPE / "ran.txt").exists()


async def test_a_lost_http_connection_kills_the_guest_command(
        computerd, tmp_path) -> None:
    """The REAL disconnect path: the HTTP client goes away mid-command
    over the actual transport — not a synthetic flag — so `is_closing`
    fires, the process group is killed, and the late completion marker
    can never land. The guest is then quiet and owns the next segment."""
    url, runner = await _serve(_app(computerd, tmp_path))
    try:
        marker = tmp_path / "root" / SCOPE / "marker"
        auth = {"Authorization": f"Bearer {TOKEN}",
                "X-Computerd-Instance": BOOT}
        async with aiohttp.ClientSession() as s:
            pending = asyncio.ensure_future(s.post(
                f"{url}/v1/run", headers=auth, json={
                    "scope": SCOPE, "timeout_s": 10,
                    "command": "sleep 3; echo late > marker"}))
            await asyncio.sleep(0.5)  # the command is running on the guest
            await s.close()  # REAL connection loss: nobody can collect the answer
            with contextlib.suppress(Exception):
                await pending
        # Past the sleep: a surviving command would have written its marker.
        await asyncio.sleep(3.5)
        assert not marker.exists()
        # The scope is quiet and serves the NEXT segment.
        async with aiohttp.ClientSession() as s, s.post(
                f"{url}/v1/run", headers=auth, json={
                    "scope": SCOPE, "timeout_s": 5,
                    "command": "echo ok > next"}) as r:
            assert r.status == 200
            assert (await r.json())["status"] == "ok"
        assert (tmp_path / "root" / SCOPE / "next").read_text() == "ok\n"
    finally:
        await runner.cleanup()


async def test_disconnect_kills_the_guest_command_group(computerd, tmp_path) -> None:
    """A caller that went away stops the COMMAND, not just the awaiter: the
    heartbeat loop's process group is killed, so the guest workspace is
    quiet before the row may close."""
    computer = computerd.Computer(
        root=tmp_path / "root", token=TOKEN, isolate_scopes=False,
        tmux_root=tmp_path / "tmux")
    beat = tmp_path / "root" / SCOPE / "heartbeat"
    command = "while :; do echo x >> heartbeat; sleep 0.2; done"
    disconnected_after = time.monotonic() + 0.3
    got = await computer.run(
        SCOPE, {"command": command, "timeout_s": 8},
        disconnected=lambda: time.monotonic() > disconnected_after)
    assert got["status"] == "unavailable" and "caller went away" in got["reason"]
    assert got["duration_ms"] < 5_000  # it did NOT wait out the timeout
    # The process group is dead: the heartbeat stops for good, not pauses.
    assert beat.exists()
    first = beat.read_text().count("x")
    await asyncio.sleep(1.0)
    assert beat.read_text().count("x") == first
