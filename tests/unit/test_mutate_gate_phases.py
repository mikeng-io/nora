"""`scripts/mutate.py`'s Python boundary and its custody verdicts.

The runner's only Python test command is this interpreter's
`scripts.pytest_gate` — the public shared-lease mechanism — and a nonzero
exit is never itself proof: a held lease is UNAVAILABLE, an import or setup
error is BROKEN, a selector that ran nothing is BROKEN. Only rc 1 with a real
failure count and NO error count is RED — a failed test beside any setup or
teardown error is contaminated, and a collection exit 2 without the gate's
own busy/stale signal is an error run, never contention — and only a
byte-restored green restore is a catch.

These tests fake the `subprocess` boundary; no real pytest session runs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mutate", Path(__file__).resolve().parents[2] / "scripts" / "mutate.py",
)
assert _SPEC is not None and _SPEC.loader is not None
mutate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mutate  # @dataclass resolves cls.__module__ here
_SPEC.loader.exec_module(mutate)

_HEAD = "ab" * 20 + "\n"
_GREEN = "1 passed in 0.10s\n"
_BUSY = "Nora test host busy; wait for the current job to finish\n"


def _git_output(args: list[str]) -> str:
    return {"rev-parse": _HEAD}.get(args[0], "")


def _fake_run(responses: list[subprocess.CompletedProcess], seen: list | None = None):
    def run(command, **kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(
                command, 0, stdout=_git_output(command[1:]), stderr="")
        if seen is not None:
            seen.append(command)
        return responses.pop(0)
    return run


def _proc(command, rc, out="", err=""):
    return subprocess.CompletedProcess(command, rc, stdout=out, stderr=err)


def test_the_python_phase_runs_this_interpreters_pytest_gate():
    """The boundary IS the gate: the invoking interpreter, never uv or raw
    pytest, with the selector passed through exactly."""
    command = mutate._gate_command("tests/unit/test_x.py::test_a -k b")
    assert command == [sys.executable, "-m", "scripts.pytest_gate",
                       "--workers", "0",
                       "tests/unit/test_x.py::test_a", "-k", "b"]


@pytest.mark.parametrize(("rc", "out", "err", "verdict"), [
    (0, "3 passed in 0.4s", "", "green"),
    (1, "1 failed, 2 passed in 0.5s", "", "red"),
    (1, "1 failed in 95.12s (0:01:35)", "", "red"),
    (1, "1 failed, 1 error in 0.5s", "", "errored"),
    (1, "1 failed, 1 error in 95.12s (0:01:35)", "", "errored"),
    (5, "no tests ran", "", "empty"),
    (2, "", _BUSY, "busy"),
    (4, "", "Nora test lease changed; refusing a stale worker/job", "busy"),
    (1, "1 error in 0.2s", "", "errored"),
    (1, "2 passed, 1 error in 0.2s", "", "errored"),
    (2, "", "ERROR collecting tests/x.py\n1 error in 0.01s", "errored"),
    (2, "", "!!!! Interrupted: keyboard interrupt !!!!", "error"),
    (3, "", "INTERNALERROR> boom", "error"),
    (4, "", "ERROR: file or directory not found: tests/nope.py", "error"),
    (-9, "", "", "error"),
])
def test_pytest_verdict_keeps_proof_apart_from_noise(rc, out, err, verdict):
    """Busy, empty, errored and dead runs are never red and never green."""
    assert mutate.pytest_verdict(rc, out + err) == verdict


def test_a_failed_test_beside_an_error_is_errored_never_red():
    """The decisive contamination: a real failure count AND an error count."""
    assert mutate.pytest_verdict(1, "1 failed, 1 error in 0.5s") == "errored"


def test_a_collection_exit_two_is_an_error_run_not_contention():
    """Exit 2 without the gate's busy signal is an error run, never a wait."""
    out = "ERROR collecting tests/x.py\n1 error in 0.01s"
    assert mutate.pytest_verdict(2, out) == "errored"
    assert mutate.pytest_verdict(2, "Interrupted: no summary") == "error"


def test_an_echoed_fake_count_is_not_the_summary():
    """Failure output echoes test source quoting a count; only the real
    summary line counts, or this runner could never prove itself."""
    out = ('        out = "ERROR collecting tests/x.py\\n1 error in 0.01s"\n'
           '>       assert pytest_verdict(1, "1 failed, 1 error in 0.5s")\n'
           "1 failed in 0.40s\n")
    assert mutate.pytest_verdict(1, out) == "red"


@pytest.mark.parametrize("message", [
    "Nora test host busy; wait for the current job to finish",
    "Nora test lease changed; refusing a stale worker/job",
])
def test_an_echoed_busy_message_is_not_contention(message):
    out = (f'        (4, "", "{message}", "busy"),\n'
           "2 failed, 13 passed, 2 warnings in 0.40s\n")
    assert mutate.pytest_verdict(1, out) == "red"


def test_a_held_lease_is_waited_out_then_counted(monkeypatch):
    monkeypatch.setattr(mutate, "LEASE_WAIT_S", 0)
    calls = [_proc([], 2, err=_BUSY), _proc([], 2, err=_BUSY),
             _proc([], 0, out=_GREEN)]
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    phase = mutate.run("tests/unit/test_x.py::test_a")
    assert (phase.verdict, phase.waits, phase.ran) == ("green", 2, 1)


def test_a_lease_that_never_frees_is_busy_not_a_verdict(monkeypatch):
    monkeypatch.setattr(mutate, "LEASE_WAIT_S", 0)
    monkeypatch.setattr(mutate, "LEASE_RETRIES", 2)
    monkeypatch.setattr(subprocess, "run",
                        _fake_run([_proc([], 2, err=_BUSY)] * 3))
    phase = mutate.run("tests/unit/test_x.py::test_a")
    assert (phase.verdict, phase.waits) == ("busy", 2)


def _entry(tmp_path):
    src = tmp_path / "src" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("VERSION = 2\n")
    return src, {"name": "the version guard", "file": "src/foo.py",
                 "old": "VERSION = 2\n", "new": "VERSION = 1\n",
                 "target": "tests/unit/test_x.py::test_v"}


def _prove(tmp_path, monkeypatch, responses):
    src, entry = _entry(tmp_path)
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    seen: list[bytes] = []

    def run(command, **kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(
                command, 0, stdout=_git_output(command[1:]), stderr="")
        seen.append(src.read_bytes())
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", run)
    rec = mutate.prove(entry, "00", tmp_path / "proofs")
    return rec, seen


def test_an_errored_mutant_is_broken_never_a_catch(tmp_path, monkeypatch):
    """A setup/collection error kills the mutant without proving the guard."""
    rec, seen = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 error in 0.2s\n"),
        _proc([], 0, out=_GREEN)])
    assert rec["disposition"] == (
        "BROKEN ENTRY: mutant errored — a setup/teardown/collection "
        "error is not proof")
    assert seen == [b"VERSION = 2\n", b"VERSION = 1\n", b"VERSION = 2\n"]
    assert (tmp_path / "src" / "foo.py").read_bytes() == b"VERSION = 2\n"


def test_a_failed_test_beside_an_error_is_broken_not_caught(tmp_path, monkeypatch):
    """A mutant run of `1 failed, 1 error` is contaminated, never a catch."""
    rec, seen = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed, 1 error in 0.2s\n"),
        _proc([], 0, out=_GREEN)])
    assert rec["disposition"] == (
        "BROKEN ENTRY: mutant errored — a setup/teardown/collection "
        "error is not proof")
    assert seen == [b"VERSION = 2\n", b"VERSION = 1\n", b"VERSION = 2\n"]


def test_a_collection_exit_two_is_not_waited_as_contention(tmp_path, monkeypatch):
    """An rc-2 collection error is never retried as a held lease."""
    monkeypatch.setattr(mutate, "LEASE_WAIT_S", 0)
    monkeypatch.setattr(mutate, "LEASE_RETRIES", 2)
    collection = "ERROR collecting tests/x.py\n1 error in 0.01s"
    rec, seen = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 2, err=collection),
        _proc([], 2, err=collection), _proc([], 2, err=collection),
        _proc([], 0, out=_GREEN)])
    assert rec["disposition"] == (
        "BROKEN ENTRY: mutant errored — a setup/teardown/collection "
        "error is not proof")
    assert rec["mutant"]["lease_waits"] == 0
    assert seen == [b"VERSION = 2\n", b"VERSION = 1\n", b"VERSION = 2\n"]


def test_an_oserror_mutant_still_byte_restores(tmp_path, monkeypatch):
    """A mutant phase that cannot even spawn restores the source bytes."""
    src, entry = _entry(tmp_path)
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    calls = iter([_proc([], 0, out=_GREEN), OSError("spawn failed")])

    def run(command, **kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(
                command, 0, stdout=_git_output(command[1:]), stderr="")
        item = next(calls)
        if isinstance(item, OSError):
            raise item
        return item

    monkeypatch.setattr(subprocess, "run", run)
    rec = mutate.prove(entry, "00", tmp_path / "proofs")
    assert rec["disposition"] == "UNAVAILABLE: mutant phase exited rc=-1"
    assert src.read_bytes() == b"VERSION = 2\n"
    assert rec["byte_restored"]


def test_phase_receipts_bind_their_head_and_source(tmp_path, monkeypatch):
    """Every phase receipt names the HEAD and the exact source bytes it ran."""
    rec, _ = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed in 0.1s\n"),
        _proc([], 0, out=_GREEN)])
    fixed = hashlib.sha256(b"VERSION = 2\n").hexdigest()
    mutant_src = hashlib.sha256(b"VERSION = 1\n").hexdigest()
    for phase in ("baseline", "mutant", "restored"):
        assert rec[phase].get("head") == _HEAD.strip()
    assert rec["baseline"].get("source_sha256") == fixed
    assert rec["mutant"].get("source_sha256") == mutant_src
    assert rec["restored"].get("source_sha256") == fixed


def test_proof_receipts_are_private(tmp_path, monkeypatch):
    """Retained evidence is 0700 directories and 0600 files."""
    _, entry = _entry(tmp_path)
    table = tmp_path / "table.json"
    table.write_text(json.dumps([entry]))
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed in 0.1s\n"),
        _proc([], 0, out=_GREEN)]))
    proofs = tmp_path / "proofs"
    rc = mutate.main(["mutate.py", str(table), "--proof-dir", str(proofs)])
    assert rc == 0
    assert stat.S_IMODE(proofs.stat().st_mode) == 0o700
    for receipt in proofs.iterdir():
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600, receipt


def test_the_caught_wording_is_observation_not_proof(tmp_path, monkeypatch,
                                                   capsys):
    """Success wording describes the observed phases and names the manual step."""
    assert "intended assertion" in mutate.CAUGHT
    assert "caught" not in mutate.CAUGHT.lower()
    _, entry = _entry(tmp_path)
    table = tmp_path / "table.json"
    table.write_text(json.dumps([entry]))
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed in 0.1s\n"),
        _proc([], 0, out=_GREEN)]))
    rc = mutate.main(["mutate.py", str(table), "--proof-dir",
                      str(tmp_path / "proofs")])
    out = capsys.readouterr().out
    assert rc == 0 and "ALL MUTATIONS RED AND RESTORED" in out
    assert "CAUGHT" not in out


def test_a_survived_mutant_is_not_a_catch(tmp_path, monkeypatch):
    rec, _ = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 0, out=_GREEN),
        _proc([], 0, out=_GREEN)])
    assert rec["disposition"] == "SURVIVED: mutant stayed green"


def test_a_caught_mutant_is_red_then_byte_restored_green(tmp_path, monkeypatch):
    rec, seen = _prove(tmp_path, monkeypatch, [
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed in 0.1s\n"),
        _proc([], 0, out=_GREEN)])
    assert rec["disposition"] == mutate.CAUGHT
    assert rec["byte_restored"] and rec["restored_to_pre_run_state"]
    assert seen == [b"VERSION = 2\n", b"VERSION = 1\n", b"VERSION = 2\n"]


def test_a_busy_baseline_leaves_the_entry_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(mutate, "LEASE_WAIT_S", 0)
    monkeypatch.setattr(mutate, "LEASE_RETRIES", 0)
    rec, seen = _prove(tmp_path, monkeypatch, [_proc([], 2, err=_BUSY)])
    assert rec["disposition"] == "UNAVAILABLE: test host lease never freed"
    assert seen == [b"VERSION = 2\n"]


def test_the_red_and_restored_banner_needs_every_entry_caught(
        tmp_path, monkeypatch, capsys):
    _, entry = _entry(tmp_path)
    table = tmp_path / "table.json"
    table.write_text(json.dumps(
        [entry, {**entry, "name": "a second clause"}]))
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([
        _proc([], 0, out=_GREEN), _proc([], 1, out="1 failed in 0.1s\n"),
        _proc([], 0, out=_GREEN),
        _proc([], 0, out=_GREEN), _proc([], 0, out=_GREEN),
        _proc([], 0, out=_GREEN)]))
    rc = mutate.main(
        ["mutate.py", str(table), "--proof-dir", str(tmp_path / "proofs")])
    out = capsys.readouterr().out
    assert rc == 1 and "ALL MUTATIONS RED AND RESTORED" not in out
    assert "SURVIVED" in out
