"""A cancelled media read leaves no process and no scratch file behind.

`_run` and `_run_video` are the two ffmpeg runners. Both must handle a TIMEOUT
and a CANCELLATION: `asyncio.wait_for` abandons `communicate()` and re-raises,
so the child would keep running, and `_run_video` would also leave its scratch
copy of the clip (up to 64 MiB). A read is
cancelled whenever the turn's own `wait_for` fires, and `instagram_read`
cancels its per-item looks on the same path, so this is the ordinary way a
slow read ends.

The child here is the test's own Python interpreter sleeping, not ffmpeg:
what is under test is the runner's cleanup, and it must be a process that
would genuinely keep running. `asyncio.create_subprocess_exec` is wrapped
only to KEEP the process handle so its state can be read back.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from nora.infra.vision import preprocess

_SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def spawned(monkeypatch):
    """Every process the runners start, with the argv it was started with."""
    started: list[tuple[asyncio.subprocess.Process, tuple]] = []
    real = asyncio.create_subprocess_exec

    async def keep(*args, **kwargs):
        proc = await real(*args, **kwargs)
        started.append((proc, args))
        return proc

    monkeypatch.setattr(preprocess.asyncio, "create_subprocess_exec", keep)
    return started


def _exists(path: str) -> bool:
    return os.path.exists(path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _cancel_after(coro, seconds: float) -> None:
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(seconds)
    assert not task.done()   # positive control: it was still running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancelled_video_run_kills_ffmpeg_and_removes_the_scratch_file(spawned):
    await _cancel_after(
        preprocess._run_video([*_SLEEP, "pipe:0"], 30.0, b"clip bytes" * 1000), 0.5,
    )

    (proc, args), = spawned
    scratch = args[-1]
    assert scratch.endswith(".input")
    assert proc.returncode is not None   # killed and reaped
    assert not _alive(proc.pid)
    assert not _exists(scratch)


async def test_a_cancelled_image_run_kills_ffmpeg(spawned):
    await _cancel_after(preprocess._run(_SLEEP, 30.0, b"image bytes"), 0.5)

    (proc, _args), = spawned
    assert proc.returncode is not None
    assert not _alive(proc.pid)


async def test_a_timed_out_video_run_still_cleans_up(spawned):
    """The path that already worked, held so the rewrite keeps it."""
    code, out, _err = await preprocess._run_video([*_SLEEP, "pipe:0"], 0.5, b"clip")

    (proc, args), = spawned
    assert (out, proc.returncode is not None) == (b"", True)
    assert code is not None   # the killed process's own exit status
    assert not _exists(args[-1])


# ---- the remaining leak paths, and the shield --------------------------------


async def test_a_cancel_while_ffmpeg_is_being_spawned_still_removes_the_scratch_file(
    monkeypatch,
):
    """The scratch file exists before the spawn; a cancel landing during
    `create_subprocess_exec` left it on disk (5 of 5 probes)."""
    spawned_with: list[tuple] = []

    async def never_returns(*args, **kwargs):
        spawned_with.append(args)
        await asyncio.Event().wait()

    monkeypatch.setattr(preprocess.asyncio, "create_subprocess_exec", never_returns)
    await _cancel_after(preprocess._run_video(["ffmpeg", "-i", "pipe:0"], 30.0, b"clip"), 0.2)

    (args,) = spawned_with
    assert args[2].endswith(".input")
    assert not _exists(args[2])


class _StuckProcess:
    """A child whose read never ends and whose reap waits for `release`.
    Records whether it was killed, and whether its `wait` was CANCELLED,
    which is what the shield in `_reap` exists to prevent: a cancelled wait
    leaves the killed child unreaped."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.wait_cancelled = False
        self.release = asyncio.Event()

    async def communicate(self, input=None):
        await asyncio.Event().wait()

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.wait_cancelled = True
            raise
        self.returncode = -9
        return -9


async def test_a_second_cancel_during_the_reap_removes_the_file_and_the_reap_goes_on(
    monkeypatch,
):
    """First cancel: ffmpeg is killed and reaped under a shield. A second
    cancel arriving during that reap must not raise out of it before the
    unlink: the file goes anyway, and the shielded wait
    is NOT cancelled, so the killed child is still reaped."""
    proc = _StuckProcess()
    spawned_with: list[tuple] = []

    async def stuck(*args, **kwargs):
        spawned_with.append(args)
        return proc

    monkeypatch.setattr(preprocess.asyncio, "create_subprocess_exec", stuck)
    task = asyncio.ensure_future(
        preprocess._run_video(["ffmpeg", "-i", "pipe:0"], 30.0, b"clip"),
    )
    await asyncio.sleep(0.1)
    task.cancel()   # the first: into the finally, and the reap
    await asyncio.sleep(0.1)
    assert proc.killed and not task.done()   # positive control: waiting on the reap
    task.cancel()   # the second, during the shielded reap
    with pytest.raises(asyncio.CancelledError):
        await task

    (args,) = spawned_with
    assert not _exists(args[2])
    assert proc.wait_cancelled is False
    proc.release.set()
    await asyncio.sleep(0.05)
    assert proc.returncode == -9   # reaped after all
