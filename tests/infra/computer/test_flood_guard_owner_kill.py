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

# Unique argv markers so residue checks can never match another
# session's processes.
HELPER_CMD, CHROME_CMD, NATIVE_CMD = "sleep 611", "sleep 613", "sleep 617"


def _gone(pid: int) -> bool:
    """The pid has no /proc entry at all — a zombie does NOT pass."""
    return not os.path.exists(f"/proc/{pid}")


def _descendants(root: int) -> set[int]:
    """Every live pid in `root`'s tree (breadth-first over /proc)."""
    tree, frontier = set(), {root}
    while frontier:
        nxt = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                rest = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                pid, ppid = int(entry.name), int(rest[1])
            except (OSError, IndexError, ValueError):
                continue
            if ppid in frontier and pid not in tree:
                nxt.add(pid)
        tree |= nxt
        frontier = nxt
    return tree - {root}


def _detached_in(root: int, owner_pgid: int) -> int | None:
    """A descendant of `root` in a different process group — the shape
    killpg cannot reach."""
    for pid in _descendants(root):
        with contextlib.suppress(OSError):
            if os.getpgid(pid) != owner_pgid:
                return pid
    return None


def _reap_child(proc: subprocess.Popen) -> None:
    """Bounded TERM + wait, then KILL + wait — a direct child we spawned
    is never left as our zombie."""
    for act in (proc.terminate, proc.kill):
        with contextlib.suppress(ProcessLookupError):
            act()
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def _kill_orphans(pids) -> None:
    """Orphaned fixture pids: SIGKILL, then require /proc absent — init
    reaps them; an absent entry is the only honest proof of reaping."""
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    for pid in pids:
        deadline = time.monotonic() + 3
        while not _gone(pid) and time.monotonic() < deadline:
            time.sleep(0.05)


def _residue() -> list[int]:
    """Any surviving process carrying a fixture argv marker."""
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_text()
        except OSError:
            continue
        if any(c in cmd for c in (HELPER_CMD, CHROME_CMD, NATIVE_CMD)):
            out.append(int(entry.name))
    return out


@pytest.mark.asyncio
async def test_the_flood_guard_kills_through_the_owner_not_the_group(
        tmp_path, monkeypatch):
    """The detached helper dies with the browser it detached FROM — never
    abandoned to init — and an unrelated native job is never in the kill."""
    wrapper = native = None
    helper_pid = rfd = wfd = None
    try:
        fixture = tmp_path / "wrap.py"
        fixture.write_text(
            "import os,sys\n"
            "sys.path.insert(0, os.environ['REPO'])\n"
            "r,w = os.pipe(); os.dup2(r,3); os.dup2(w,4)\n"
            "from deploy.computer.computerd import run_browser_child\n"
            f"child = 'setsid {HELPER_CMD} & exec {CHROME_CMD}'\n"
            "raise SystemExit(run_browser_child(['bash', '-c', child]))\n")
        env = dict(os.environ, REPO=os.getcwd())
        # start_new_session mirrors production's spawn: the wrapper leads
        # its OWN process group, so killpg(pid) actually reaches it — the
        # property the killpg mutation must violate for a REAL red, not a
        # suppressed ESRCH.
        wrapper = subprocess.Popen(  # noqa: ASYNC220,S603 - fixture wrapper
            [sys.executable, str(fixture)], env=env, start_new_session=True)
        native = subprocess.Popen(  # noqa: ASYNC220,S603 - fixture proc
            ["setsid", *NATIVE_CMD.split()])

        # The helper execs after the setsid fork — a descendant of the
        # wrapper's tree in a DIFFERENT process group: the exact shape
        # killpg cannot reach.
        deadline = time.monotonic() + 10
        while helper_pid is None and time.monotonic() < deadline:
            helper_pid = _detached_in(wrapper.pid, wrapper.pid)
            await asyncio.sleep(0.1)
        assert helper_pid is not None, "fixture helper never appeared"
        assert not _gone(helper_pid)
        assert os.getpgid(helper_pid) != wrapper.pid
        assert os.getpgid(native.pid) != wrapper.pid

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
        # Every fixture process is reaped — direct children via wait(),
        # orphans via KILL + /proc-absence (init reaps them). This holds
        # on partial setup and under the old killpg mutant too.
        if wrapper is not None and wrapper.returncode is None:
            _reap_child(wrapper)
        if helper_pid is not None:
            _kill_orphans({helper_pid})
        if native is not None and native.returncode is None:
            _reap_child(native)
        # Nothing carrying a fixture marker survives — zombies count.
        _kill_orphans(set(_residue()))
        assert not _residue()
