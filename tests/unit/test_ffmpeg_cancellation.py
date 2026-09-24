"""A cancelled media read leaves no process and no scratch file behind
(design/instagram-adoption/18 §"Typed views, one renderer, and readings",
#486 review).

`_run` and `_run_video` are the two ffmpeg runners. Both handled a TIMEOUT
and neither handled a CANCELLATION: `asyncio.wait_for` abandons
`communicate()` and re-raises, so the child kept running, and `_run_video`
also skipped removing its scratch copy of the clip (up to 64 MiB). A read is
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
