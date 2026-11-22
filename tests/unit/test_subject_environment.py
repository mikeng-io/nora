"""`IsolatedServices` must unwind the process-global guard on every exit
path — including teardowns that cannot finish the shielded async close,
because the guard patches `subprocess.Popen`, `socket` and `os.environ`
for the whole process and a leaked install poisons every later case."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
from typing import Any

import pytest

import nora.subject.environment as environment_module
from nora.subject.environment import IsolatedServices


async def _open() -> IsolatedServices:
    environment = IsolatedServices(database_url="sqlite://", applications={})
    await environment.__aenter__()
    return environment


def test_a_teardown_without_a_running_loop_still_unwinds_the_guard() -> None:
    """Teardown driven after the function-scoped loop closed has no
    running loop for `asyncio.create_task`; the exit reports that failure
    and must still release the guard."""
    connect, popen_init = socket.socket.connect, subprocess.Popen.__init__
    environment = asyncio.run(_open())
    assert getattr(socket.socket.connect, "_nora_eval_guard", False)
    with pytest.raises(RuntimeError):
        environment.__aexit__(None, None, None).send(None)
    assert socket.socket.connect is connect
    assert subprocess.Popen.__init__ is popen_init


async def _uncancellable() -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.Event().wait()
    await asyncio.Event().wait()


def test_an_exit_abandoned_mid_close_still_unwinds_the_guard() -> None:
    """A loop torn down while the shielded `_close` is still pending —
    here, held open by a task that survives its cancellation sweep —
    orphans the `__aexit__` coroutine; collecting it must still release
    the guard."""
    connect, popen_init = socket.socket.connect, subprocess.Popen.__init__

    async def suspend_mid_exit() -> tuple[IsolatedServices, Any, Any]:
        environment = await _open()
        held = asyncio.ensure_future(_uncancellable())
        exit_coroutine = environment.__aexit__(None, None, None)
        exit_coroutine.send(None)
        return environment, exit_coroutine, held

    loop = asyncio.new_event_loop()
    _environment, exit_coroutine, _held = loop.run_until_complete(suspend_mid_exit())
    assert getattr(socket.socket.connect, "_nora_eval_guard", False)
    loop.close()
    exit_coroutine.close()
    assert socket.socket.connect is connect
    assert subprocess.Popen.__init__ is popen_init


@pytest.mark.asyncio
async def test_a_close_that_never_finishes_still_releases_the_guard(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The case task's thread can abandon a suspended `__aexit__` — a stuck
    `_close` (a child task that ignores its cancellation, a process that
    ignores its terminate) — and the guard is process-global, so the wait
    is bounded and the guard is released regardless."""
    connect, popen_init = socket.socket.connect, subprocess.Popen.__init__
    monkeypatch.setattr(environment_module, "_CLOSE_BUDGET", 0.1)
    environment = await _open()

    async def never(_owner: Any) -> None:
        await asyncio.Event().wait()

    environment._close = never  # type: ignore[method-assign]
    exit_task = asyncio.ensure_future(environment.__aexit__(None, None, None))
    await asyncio.sleep(0.3)
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
    assert socket.socket.connect is connect
    assert subprocess.Popen.__init__ is popen_init
    with pytest.raises(TimeoutError):
        await exit_task


def test_a_guard_abandoned_on_a_dead_loop_goes_inert() -> None:
    """A teardown abandoned mid-suspension leaves the flag installed, but a
    dead owner loop cannot protect anything: the next case must still
    enter, and the zombie's closures must pass through, not refuse."""
    connect, popen_init = socket.socket.connect, subprocess.Popen.__init__

    async def suspend_mid_exit() -> tuple[IsolatedServices, Any, Any]:
        environment = await _open()
        held = asyncio.ensure_future(_uncancellable())
        exit_coroutine = environment.__aexit__(None, None, None)
        exit_coroutine.send(None)
        return environment, exit_coroutine, held

    loop = asyncio.new_event_loop()
    environment, exit_coroutine, _held = loop.run_until_complete(suspend_mid_exit())
    assert getattr(socket.socket.connect, "_nora_eval_guard", False)
    loop.close()
    try:
        async def enter_and_exit() -> None:
            later = await _open()
            await later.__aexit__(None, None, None)

        asyncio.run(enter_and_exit())
        sock = socket.socket()
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", 1))
        except PermissionError:
            pytest.fail("the dead owner's guard refused")
        except OSError:
            pass
    finally:
        exit_coroutine.close()
        environment._unwind_guard()
    assert socket.socket.connect is connect
    assert subprocess.Popen.__init__ is popen_init
    sock.close()


def test_a_dead_owner_finalized_under_a_live_successor_cannot_strip_it() -> None:
    """Entry retires the dead owner's patch stack, so a predecessor's exit
    finalized while a successor is live is a no-op: it cannot restore the
    originals underneath the live guard, and the successor's own exit
    restores the real surfaces, not the zombie's wrappers."""
    connect = socket.socket.connect

    async def suspend_mid_exit() -> tuple[IsolatedServices, Any, Any]:
        environment = await _open()
        held = asyncio.ensure_future(_uncancellable())
        exit_coroutine = environment.__aexit__(None, None, None)
        exit_coroutine.send(None)
        return environment, exit_coroutine, held

    loop = asyncio.new_event_loop()
    _dead, exit_coroutine, _held = loop.run_until_complete(suspend_mid_exit())
    loop.close()

    async def live_successor() -> None:
        environment = await _open()
        try:
            # The dead predecessor's exit finalizes UNDER the live guard.
            exit_coroutine.close()
            assert getattr(socket.socket.connect,
                           "_nora_eval_environment", None) is environment
            sock = socket.socket()
            with pytest.raises(PermissionError):
                sock.connect(("10.255.255.1", 1))
            sock.close()
        finally:
            await environment.__aexit__(None, None, None)

    try:
        asyncio.run(live_successor())
    finally:
        _dead._unwind_guard()
    assert socket.socket.connect is connect
