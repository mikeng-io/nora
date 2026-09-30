"""computerd's process reading (`scope_processes`, deploy/computer/computerd.py):
the half of `/v1/state` that sees a job with no tmux session, read from a
fabricated `/proc` so it runs on any machine.

Pinned: only the scope user's processes; kernel threads (no argv, pid 2 and
its children) and tmux's own processes (a server, a pane's shell: tmux's
pane-with-a-child rule judges those) are the only ones left out; a process's command is its argv,
cut; start and elapsed come from `starttime` and `btime`; a `/proc/<pid>` that
is a link is never followed; the read caps at twenty, newest first.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

COMPUTERD = Path(__file__).parents[2] / "deploy" / "computer" / "computerd.py"
UID = 1234
BOOT = 1_790_000_000
NOW = BOOT + 10_000


@pytest.fixture(scope="module")
def computerd():
    spec = importlib.util.spec_from_file_location("computerd_processes_under_test", COMPUTERD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _proc(root: Path, pid: int, *, uid: int = UID, argv: list[str], ppid: int = 1,
          start_s: int = 100, comm: str = "x") -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start_s * 100)]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + (b"\0" if argv else b""))


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    (tmp_path / "stat").write_text(f"cpu 1 2 3\nbtime {BOOT}\n")
    return tmp_path


def test_only_the_scope_users_processes_are_listed(computerd, proc, monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    _proc(proc, 159, argv=["python3", "tick.py"], start_s=8_980)
    _proc(proc, 160, uid=0, argv=["sshd"])
    got = computerd.scope_processes(proc, UID, NOW)
    assert got == [{"pid": 159, "command": "python3 tick.py",
                    "started": BOOT + 8_980, "elapsed": 1_020}]


def test_kernel_threads_are_the_only_ones_left_out(computerd, proc, monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    _proc(proc, 2, argv=[], comm="kthreadd")
    _proc(proc, 40, argv=[], ppid=2, comm="kworker/0:1")
    _proc(proc, 41, argv=["bash"])
    _proc(proc, 42, argv=["tmux", "new", "-d", "-s", "job"])
    got = sorted(p["pid"] for p in computerd.scope_processes(proc, UID, NOW))
    assert got == [41, 42]


def test_a_command_is_cut_to_eighty_characters(computerd, proc, monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    _proc(proc, 7, argv=["python3", "x" * 200])
    [got] = computerd.scope_processes(proc, UID, NOW)
    assert len(got["command"]) == 80


def test_a_pid_directory_that_is_a_link_is_not_followed(computerd, proc, tmp_path_factory,
                                                        monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    _proc(elsewhere, 99, argv=["planted"])
    (proc / "99").symlink_to(elsewhere / "99")
    assert computerd.scope_processes(proc, UID, NOW) == []


def test_state_caps_processes_at_twenty_newest_first(computerd, proc, tmp_path, monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    for pid in range(100, 125):
        _proc(proc, pid, argv=["sleep", str(pid)], start_s=pid)
    comp = computerd.Computer(root=tmp_path / "ws", token="t", isolate_scopes=False,  # noqa: S106
                              tmux_root=tmp_path / "tm", proc_root=proc)
    monkeypatch.setattr(computerd.os, "getuid", lambda: UID)
    got = comp.processes("ab" * 16)
    assert got["processes_truncated"] is True
    assert [p["pid"] for p in got["processes"]] == list(range(124, 104, -1))


def test_an_unreadable_proc_is_unknown_not_none_running(computerd, tmp_path):
    comp = computerd.Computer(root=tmp_path / "ws", token="t", isolate_scopes=False,  # noqa: S106
                              tmux_root=tmp_path / "tm", proc_root=tmp_path / "no-proc")
    got = comp.processes("ab" * 16)
    assert got["processes"] is None and got["processes_reason"]


def test_an_idle_tmux_session_is_not_a_running_process(computerd, proc, monkeypatch):
    """FALSIFIER (operator, 2026-10-01): a tmux server and a bare shell in a
    pane are tmux's to judge, by the pane-with-a-child rule, never counted
    as a running process. Counted, a room with a session left open would
    stay active for days."""
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    _proc(proc, 50, argv=["tmux", "new-session", "-d", "-s", "job"], comm="tmux: server")
    _proc(proc, 51, argv=["-bash"], ppid=50, comm="bash")
    assert computerd.scope_processes(proc, UID, NOW, exclude=frozenset({51})) == []


def test_a_panes_child_is_a_running_process(computerd, proc, monkeypatch):
    monkeypatch.setattr(computerd.os, "sysconf", lambda name: 100)
    _proc(proc, 50, argv=["tmux", "new-session", "-d", "-s", "job"], comm="tmux: server")
    _proc(proc, 51, argv=["-bash"], ppid=50, comm="bash")
    _proc(proc, 52, argv=["python3", "train.py"], ppid=51, comm="python3")
    got = computerd.scope_processes(proc, UID, NOW, exclude=frozenset({51}))
    assert [p["pid"] for p in got] == [52]
