"""The unframed-flood guard kills the browser through its LIFETIME OWNER:
on Linux `chrome.pid` is the `run_browser_child` wrapper, whose finally
SIGKILLs and reaps the whole descendant tree — setsid'd helpers in other
process groups included. `killpg` alone can never reach those helpers:
their parent dies mid-reap and they reparent to init, leaving the scope
reading busy forever. Linux-only: the wrapper needs /proc."""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
import types

import pytest

from deploy.computer.computerd import Browser

pytestmark = pytest.mark.skipif(sys.platform != "linux",
                                reason="the wrapper needs /proc")


def _spawn_wrapper(tmp_path) -> tuple[subprocess.Popen, int]:
    """The REAL `run_browser_child` over a stand-in 'Chrome' that leaves a
    detached setsid helper behind — the crashpad shape that defeats killpg."""
    helper = tmp_path / "helper.pid"
    fixture = tmp_path / "wrap.py"
    fixture.write_text(
        "import os,sys\n"
        "sys.path.insert(0, os.environ['REPO'])\n"
        "r,w = os.pipe(); os.dup2(r,3); os.dup2(w,4)\n"
        "from deploy.computer.computerd import run_browser_child\n"
        f"child = ('setsid sh -c \"echo $$ > {helper}; exec sleep 600\" & '\n"
        "         'sleep 0.3; exec sleep 300')\n"
        "raise SystemExit(run_browser_child(['bash', '-c', child]))\n")
    env = dict(os.environ, REPO=os.getcwd())
    proc = subprocess.Popen(  # noqa: S603 - the controlled fixture wrapper
        [sys.executable, str(fixture)], env=env)
    deadline = time.monotonic() + 10
    while not helper.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    return proc, int(helper.read_text().strip())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie is still "alive" to kill(0): an un-reaped leak counts as one
    try:
        return "Z" not in open(f"/proc/{pid}/stat").read().split(") ")[1][:2]
    except OSError:
        return False


@pytest.mark.asyncio
async def test_the_flood_guard_kills_through_the_owner_not_the_group(
        tmp_path, monkeypatch):
    """The detached helper dies with the browser it detached FROM — never
    left for init — and an unrelated native job in another session is not
    part of the kill."""
    wrapper, helper_pid = _spawn_wrapper(tmp_path)
    native = subprocess.Popen(  # noqa: ASYNC220 - a fixture process, not a loop-blocking call
        ["setsid", "sleep", "600"])
    try:
        browser = object.__new__(Browser)
        browser.chrome = types.SimpleNamespace(
            pid=wrapper.pid, returncode=None)
        browser._startup_reply = None
        browser._client_pending = set()
        browser._own_pending = {}
        browser.last_used = 0.0
        r, w = os.pipe()
        os.write(w, b"x" * 4096)
        monkeypatch.setattr(
            "deploy.computer.computerd.MAX_TRANSFER_BYTES", 1024)
        await asyncio.wait_for(Browser._read(browser, r), timeout=10)

        # The wrapper was SIGTERMed (the owner), so ITS finally SIGKILLs and
        # reaps every descendant — the detached helper is gone, not reparented
        # to init, and the unrelated native job survives.
        for _ in range(50):
            if not _alive(wrapper.pid) and not _alive(helper_pid):
                break
            await asyncio.sleep(0.1)
        assert not _alive(wrapper.pid), "the wrapper should be dead"
        assert not _alive(helper_pid), (
            "the detached helper must be reaped by the wrapper's teardown, "
            "not abandoned to init")
        assert _alive(native.pid), "native scope work is never in the kill"
    finally:
        for pid in (wrapper.pid, helper_pid, native.pid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
