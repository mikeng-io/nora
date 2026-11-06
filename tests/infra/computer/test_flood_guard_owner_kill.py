"""The unframed-flood guard kills the browser through its LIFETIME OWNER:
on Linux `chrome.pid` is the `run_browser_child` wrapper, whose finally
SIGKILLs and reaps the whole descendant tree — setsid'd helpers in other
process groups included. `killpg` alone can never reach those helpers:
their owner dies mid-reap and they reparent to init, leaving the scope
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
from pathlib import Path

import pytest

from deploy.computer.computerd import Browser

pytestmark = pytest.mark.skipif(sys.platform != "linux",
                                reason="the wrapper needs /proc")


def _detached_child_of(owner: int) -> int | None:
    """A descendant of `owner` whose process group is NOT the owner's —
    the setsid'd helper shape killpg cannot reach. Its immediate parent
    is the exec'd 'chrome' child, so scan the owner's children too."""
    kids = {owner}
    found = None
    for _ in range(2):  # owner -> chrome child -> detached helper
        nxt = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                # fields after `) ` start at state: [1]=ppid, [2]=pgrp
                rest = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                pid, ppid, pgrp = int(entry.name), int(rest[1]), int(rest[2])
            except (OSError, IndexError, ValueError):
                continue
            if ppid in kids:
                nxt.add(pid)
                if pgrp != owner:
                    found = pid
        kids = nxt
        if found is not None:
            return found
    return None


def _children_of(pid: int) -> list[int]:
    return [int(p) for p in Path(
        f"/proc/{pid}/task/{pid}/children").read_text().split()]


def _ppid_of(pid: int) -> int:
    return int(
        Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[1])


def _gone(pid: int) -> bool:
    """The pid has no /proc entry at all — a zombie does NOT pass."""
    return not os.path.exists(f"/proc/{pid}")


def _kill_tree(pid: int) -> None:
    """Bounded teardown for a known fixture pid: TERM, wait, then KILL."""
    for sig, wait in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 0.5)):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)
        deadline = time.monotonic() + wait
        while not _gone(pid) and time.monotonic() < deadline:
            time.sleep(0.05)


@pytest.mark.asyncio
async def test_the_flood_guard_kills_through_the_owner_not_the_group(
        tmp_path, monkeypatch):
    """The detached helper dies with the browser it detached FROM — never
    abandoned to init — and an unrelated native job is never in the kill."""
    fixture = tmp_path / "wrap.py"
    fixture.write_text(
        "import os,sys\n"
        "sys.path.insert(0, os.environ['REPO'])\n"
        "r,w = os.pipe(); os.dup2(r,3); os.dup2(w,4)\n"
        "from deploy.computer.computerd import run_browser_child\n"
        "child = 'setsid sleep 600 & exec sleep 300'\n"
        "raise SystemExit(run_browser_child(['bash', '-c', child]))\n")
    env = dict(os.environ, REPO=os.getcwd())
    # start_new_session mirrors production's spawn: the wrapper leads its
    # OWN process group, so killpg(pid) actually reaches it — the property
    # the killpg mutation must violate for a REAL red, not a suppressed
    # ESRCH.
    wrapper = subprocess.Popen(  # noqa: ASYNC220,S603 - fixture wrapper
        [sys.executable, str(fixture)], env=env, start_new_session=True)
    native = subprocess.Popen(  # noqa: ASYNC220 - a fixture process
        ["setsid", "sleep", "600"], start_new_session=False)
    helper_pid = None
    rfd = wfd = None
    try:
        # The helper execs AFTER the setsid fork — it reparents to the
        # wrapper (subreaper) in its own process group.
        deadline = time.monotonic() + 10
        while helper_pid is None and time.monotonic() < deadline:
            helper_pid = _detached_child_of(wrapper.pid)
            await asyncio.sleep(0.1)
        assert helper_pid is not None, "fixture helper never appeared"
        # The helper exists, is a descendant of the wrapper's tree, and is
        # in a DIFFERENT process group — the exact shape killpg cannot see.
        assert not _gone(helper_pid)
        assert os.getpgid(helper_pid) != os.getpgid(wrapper.pid)
        assert _ppid_of(helper_pid) in {
            wrapper.pid, *_children_of(wrapper.pid)}

        browser = object.__new__(Browser)
        browser.chrome = types.SimpleNamespace(
            pid=wrapper.pid, returncode=None)
        browser._startup_reply = None
        browser._own_pending = {}
        browser._client_pending = set()
        browser.last_used = 0.0
        rfd, wfd = os.pipe()
        os.write(wfd, b"x" * 4096)
        monkeypatch.setattr(
            "deploy.computer.computerd.MAX_TRANSFER_BYTES", 1024)
        # The REAL flood branch: buffer crosses the cap, the guard fires.
        await asyncio.wait_for(Browser._read(browser, rfd), timeout=10)

        # The wrapper exits on its own SIGTERM handling — wait on it
        # directly, then the helper's /proc entry must be ABSENT (a zombie
        # is a failed reap, not a clean kill).
        # The wrapper exits on its SIGTERM handler — REAP it directly; a
        # child we never wait() stays a zombie under this test process.
        await asyncio.wait_for(
            asyncio.to_thread(wrapper.wait, timeout=10), timeout=12)
        for _ in range(100):
            if _gone(helper_pid):
                break
            await asyncio.sleep(0.1)
        assert _gone(helper_pid), (
            "the detached helper must be reaped by the owner's teardown, "
            "not abandoned to init or left as a zombie")
        assert not _gone(native.pid), "native scope work is never in the kill"
    finally:
        if wfd is not None:
            with contextlib.suppress(OSError):
                os.close(wfd)
        if rfd is not None:
            with contextlib.suppress(OSError):
                os.close(rfd)
        _kill_tree(wrapper.pid)
        if helper_pid is not None:
            _kill_tree(helper_pid)
        with contextlib.suppress(ProcessLookupError):
            native.terminate()
        with contextlib.suppress(Exception):
            native.wait(timeout=2)
