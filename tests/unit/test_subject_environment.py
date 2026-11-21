"""`IsolatedServices` must unwind the process-global guard on every exit
path — including teardowns that cannot finish the shielded async close,
because the guard patches `subprocess.Popen`, `socket` and `os.environ`
for the whole process and a leaked install poisons every later case."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from typing import Any

import pytest

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
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    await asyncio.Event().wait()


def test_an_exit_abandoned_mid_close_still_unwinds_the_guard() -> None:
    """A loop torn down while the shielded `_close` is still pending —
    here, held open by a task that survives its cancellation sweep —
    orphans the `__aexit__` coroutine; collecting it must still release
    the guard."""
    connect, popen_init = socket.socket.connect, subprocess.Popen.__init__

    async def suspend_mid_exit() -> tuple[IsolatedServices, Any]:
        environment = await _open()
        asyncio.ensure_future(_uncancellable())
        exit_coroutine = environment.__aexit__(None, None, None)
        exit_coroutine.send(None)
        return environment, exit_coroutine

    loop = asyncio.new_event_loop()
    _environment, exit_coroutine = loop.run_until_complete(suspend_mid_exit())
    assert getattr(socket.socket.connect, "_nora_eval_guard", False)
    loop.close()
    exit_coroutine.close()
    assert socket.socket.connect is connect
    assert subprocess.Popen.__init__ is popen_init
