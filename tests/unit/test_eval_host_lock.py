"""One eval run per host. The shared template (`nora_eval_blank`) and the
source are read by every session on the deploy host, and a second run that
initialises the template in the same instant is how one run's cases read
another's half-built state. `python -m evals` holds an exclusive host lock
from its first line to its last, and refuses (never waits) while another run
holds it.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

import evals.__main__ as m
from nora.subject import sandbox


def _set_argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["python -m evals", *args])


def _holder(path: Path) -> int:
    """Another process's hold, as this test process models it: a separate
    open file description, which `flock` treats as a second holder."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _stub_run(monkeypatch: pytest.MonkeyPatch, reached: list[str]) -> None:
    """A run that gets past the lock: every step after it is recorded, and
    the source check passes, so `main()` returns 0 through its real path."""
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "postgresql+asyncpg://u:p@h/nora_eval")

    def _record(name: str, result: object = None):
        def _call(*_a, **_k):
            reached.append(name)
            return result

        return _call

    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", _record("refuse"))
    monkeypatch.setattr(m, "_sweep_stale_private_blanks", _record("sweep"))
    monkeypatch.setattr(m, "shared_database_versions", _record("versions"))


def test_a_second_run_is_refused_while_the_host_lock_is_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
) -> None:
    lock = tmp_path / "nora-eval.lock"
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", lock)
    reached: list[str] = []
    _stub_run(monkeypatch, reached)
    _set_argv(monkeypatch, "no_trigger")
    held = _holder(lock)
    try:
        assert m.main() == 2
    finally:
        os.close(held)
    assert reached == []  # nothing past the lock: no source check, no template, no case
    assert "another eval run holds" in capsys.readouterr().err


class _Stop(Exception):
    """Ends a run once it is inside the lock; raised past every later step."""


def test_the_lock_is_held_through_the_run_and_released_when_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    lock = tmp_path / "nora-eval.lock"
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", lock)
    reached: list[str] = []
    _stub_run(monkeypatch, reached)
    seen_during: list[bool] = []

    def _probe_inside_the_run(*_a, **_k):
        try:
            os.close(_holder(lock))
            seen_during.append(False)
        except BlockingIOError:
            seen_during.append(True)
        raise _Stop

    monkeypatch.setattr(m, "_sweep_stale_private_blanks", _probe_inside_the_run)
    _set_argv(monkeypatch, "no_trigger")
    with pytest.raises(_Stop):
        m.main()
    assert seen_during == [True]
    # Released when the run ends, even by an exception: a fresh hold now succeeds.
    os.close(_holder(lock))


def test_builtin_lock_does_not_self_deadlock_with_documented_outer_flock() -> None:
    # PROFILE_COVERAGE.md already takes this outer lock around python -m evals.
    outer = _holder(sandbox.EVAL_HOST_LOCK.parent / "nora-eval.lock")
    try:
        with sandbox.eval_host_lock():
            pass
    finally:
        os.close(outer)
