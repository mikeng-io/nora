"""Real process admission and the canonical pytest phase/failure boundary; no Docker."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import textwrap
from contextlib import suppress
from pathlib import Path

import pytest

from scripts import pytest_gate as gate

ROOT = Path(__file__).resolve().parents[2]


def test_admission_covers_a_non_atomic_lock_downgrade(tmp_path, monkeypatch):
    lock = tmp_path / "host.lock"
    real = fcntl.flock
    interleaved = []

    def flock(fd, operation):
        if operation == fcntl.LOCK_SH and not interleaved:
            interleaved.append(True)
            real(fd, fcntl.LOCK_UN)
            with pytest.raises(RuntimeError, match="host busy"), gate.host_lease(lock):
                pytest.fail("a second controller entered during conversion")
        real(fd, operation)

    monkeypatch.setattr(fcntl, "flock", flock)
    with gate.host_lease(lock):
        assert interleaved == [True]


def _holder(path: Path, generation: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 — this interpreter, generated lock-holder script
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            from pathlib import Path
            from scripts.pytest_gate import host_lease
            with host_lease(Path(sys.argv[1]), sys.argv[2]):
                print('held', flush=True)
                sys.stdin.read()
        """), str(path), generation],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def test_workers_retain_admission_after_controller_exit_and_reject_stale_generation(tmp_path):
    lock = tmp_path / "host.lock"
    worker = None
    try:
        with gate.host_lease(lock) as generation:
            worker = _holder(lock, generation)
            assert worker.stdout is not None
            assert worker.stdout.readline().strip() == "held"
            with pytest.raises(RuntimeError, match="host busy"), gate.host_lease(lock):
                pytest.fail("second controller entered")
        # The controller is gone; the independently held worker lease still blocks a new job.
        with pytest.raises(RuntimeError, match="host busy"), gate.host_lease(lock):
            pytest.fail("worker's live lease was lost")
    finally:
        if worker is not None:
            worker.kill()
            worker.communicate(timeout=5)
    with gate.host_lease(lock) as replacement:
        assert replacement != generation
        with pytest.raises(RuntimeError, match="lease changed"), gate.host_lease(lock, generation):
            pytest.fail("late worker joined a different job")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock = tmp_path / "shared-host.lock"
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nfrom scripts import pytest_gate\n"
        f"pytest_gate.LOCK_PATH = Path({str(lock)!r})\n"
        f"pytest_gate.PROJECT_ROOT = Path({str(tmp_path)!r})\n"
        "pytest_plugins = ['scripts.pytest_gate']\n"
    )
    (tmp_path / "test_generated.py").write_text(
        "from pathlib import Path\nimport os, pytest\n"
        "@pytest.mark.parametrize('attempt', [0, 1])\n"
        "def test_it(attempt):\n"
        "    assert '_NORA_PYTEST_GENERATION' not in os.environ\n"
        "    Path(f'ran-{attempt}').write_text('ran')\n"
    )
    return tmp_path, lock


def _pytest(
    root: Path, *args: str, generation: str | None = None, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_ADDOPTS": ""}
    if generation is not None:
        env["_NORA_PYTEST_GENERATION"] = generation
    return subprocess.run(  # noqa: S603 — this interpreter, generated pytest project
        [sys.executable, "-m", "pytest", "-q", *args], cwd=cwd or root,
        env=env,
        capture_output=True, text=True, timeout=30,
    )


def test_raw_pytest_refuses_second_job_before_starting_workers_then_runs_normally(tmp_path):
    root, lock = _project(tmp_path)
    with gate.host_lease(lock) as generation:
        refused = _pytest(root, "-n", "2")
        assert refused.returncode == 4, refused.stdout + refused.stderr
        assert "host busy" in refused.stderr
        assert "bringing up nodes" not in refused.stdout
        assert not list(root.glob("ran-*"))
        delegated = _pytest(root, "-n", "2", generation=generation)
        assert delegated.returncode == 0, delegated.stdout + delegated.stderr
        assert "2 passed" in delegated.stdout
    allowed = _pytest(root, "-n", "2")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "2 passed" in allowed.stdout
    assert len(list(root.glob("ran-*"))) == 2
    with gate.host_lease(lock):
        pass  # actual worker/controller shutdown released every share


@pytest.mark.parametrize("workers", [None, "2", "3"])
def test_collection_can_read_the_catalogue_during_a_job_without_running_tests(tmp_path, workers):
    root, lock = _project(tmp_path)
    args = [] if workers is None else ["-n", workers]
    with gate.host_lease(lock):
        collected = _pytest(root, *args, "--collect-only")
        assert collected.returncode == 0, collected.stdout + collected.stderr
        assert "2 tests collected" in collected.stdout
        assert "bringing up nodes" not in collected.stdout
        assert not list(root.glob("ran-*"))
        refused = _pytest(root)
        assert refused.returncode == 4 and "host busy" in refused.stderr
        assert not list(root.glob("ran-*"))


def test_interrupt_stops_the_actual_child_and_releases_admission(tmp_path):
    lock = tmp_path / "host.lock"
    child = "import os, signal; print(os.getpid(), flush=True); signal.pause()"
    wrapper = subprocess.Popen(  # noqa: S603 — this interpreter, generated interrupt probe
        [sys.executable, "-c", textwrap.dedent("""
            import os, sys
            from pathlib import Path
            from scripts.pytest_gate import host_lease, _run
            with host_lease(Path(sys.argv[1])):
                rc = _run([sys.executable, '-c', sys.argv[2]], dict(os.environ))
            sys.exit(rc)
        """), str(lock), child],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    child_pid = None
    try:
        assert wrapper.stdout is not None
        child_pid = int(wrapper.stdout.readline())
        os.kill(child_pid, 0)  # positive control: the child really ran
        wrapper.send_signal(signal.SIGINT)
        wrapper.communicate(timeout=5)
        assert wrapper.returncode == 130
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        child_pid = None
        with gate.host_lease(lock):
            pass
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.communicate(timeout=5)
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_raw_pytest_cannot_bypass_worker_cap_or_parallelize_timing_contracts(tmp_path):
    root, _ = _project(tmp_path)
    capped = _pytest(root, "-n", "3")
    assert capped.returncode == 4 and "at most 2" in capped.stderr
    assert not list(root.glob("ran-*"))
    timing = root / gate.TIMING_TESTS[0]
    timing.parent.mkdir(parents=True)
    timing.write_text("def test_timing():\n    assert True\n")
    refused = _pytest(root, "-n", "2", str(timing))
    assert refused.returncode == 4 and "serial phase" in refused.stderr
    allowed = _pytest(root, str(timing))
    assert allowed.returncode == 0 and "1 passed" in allowed.stdout


def test_timing_admission_and_default_targets_are_independent_of_caller_cwd(tmp_path, monkeypatch):
    root, _ = _project(tmp_path / "checkout")
    outside = tmp_path / "caller"
    outside.mkdir()
    timing = root / gate.TIMING_TESTS[0]
    timing.parent.mkdir(parents=True)
    timing.write_text("def test_timing():\n    assert True\n")
    refused = _pytest(root, "-n", "2", str(timing), cwd=outside)
    assert refused.returncode == 4 and "serial phase" in refused.stderr
    assert "bringing up nodes" not in refused.stdout
    allowed = _pytest(root, str(timing), cwd=outside)
    assert allowed.returncode == 0 and "1 passed" in allowed.stdout
    monkeypatch.setattr(gate, "PROJECT_ROOT", root)
    monkeypatch.chdir(outside)
    commands = gate.phases([], 2)
    assert str(root / "tests") in commands[0]
    assert str(timing) in commands[1]


@pytest.mark.parametrize("targets", [
    [], ["tests"], ["tests/integration"],
    ["tests/integration/test_latency.py::test_specific"],
    ["tests/unit/test_engagement_pipeline.py", "tests/test_plain.py"],
    ["tests/test_plain.py"],
])
def test_phases_preserve_selected_targets_and_keep_timing_out_of_parallel(
    tmp_path, monkeypatch, targets,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate, "PROJECT_ROOT", tmp_path)
    for name in (*gate.TIMING_TESTS, "tests/test_plain.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    commands = gate.phases(targets, 2)
    parallel = [c for c in commands if "-n" in c]
    serial = [c for c in commands if "-n" not in c]
    for command in parallel:
        assert all(f"--ignore={tmp_path / name}" in command for name in gate.TIMING_TESTS)
        assert command[command.index("-n") + 1] == "2"
    expected_serial = {
        (): [str(tmp_path / name) for name in gate.TIMING_TESTS],
        ("tests",): [str(tmp_path / name) for name in gate.TIMING_TESTS],
        ("tests/integration",): [str(tmp_path / gate.TIMING_TESTS[i]) for i in (0, 2)],
        ("tests/integration/test_latency.py::test_specific",): [
            "tests/integration/test_latency.py::test_specific"],
        ("tests/unit/test_engagement_pipeline.py", "tests/test_plain.py"): [
            gate.TIMING_TESTS[1]],
        ("tests/test_plain.py",): [],
    }[tuple(targets)]
    assert [target for command in serial for target in command[4:]] == expected_serial
    for target in targets:
        assert any(target in command for command in commands)


@pytest.mark.parametrize(("results", "expected", "calls"), [
    ([0, 0], 0, 2), ([1], 1, 1), ([0, 1], 1, 2),
    ([2], 2, 1), ([5, 0], 0, 2), ([0, 5], 5, 2), ([-9], 137, 1),
])
def test_phase_failures_propagate_and_the_lease_spans_both_phases(
    tmp_path, monkeypatch, results, expected, calls,
):
    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "host.lock")
    seen = []

    def run(command, env):
        seen.append(command)
        with pytest.raises(RuntimeError, match="host busy"), gate.host_lease(gate.LOCK_PATH):
            pytest.fail("another gate entered between phases")
        with gate.host_lease(gate.LOCK_PATH, env["_NORA_PYTEST_GENERATION"]):
            pass  # delegated controller genuinely joins the same lease
        return results[len(seen) - 1]

    monkeypatch.setattr(gate, "_run", run)
    assert gate.main([]) == expected
    assert len(seen) == calls
    with gate.host_lease(gate.LOCK_PATH):
        pass


def test_helper_subprocesses_do_not_depend_on_caller_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    test_workers_retain_admission_after_controller_exit_and_reject_stale_generation(tmp_path)
    test_interrupt_stops_the_actual_child_and_releases_admission(tmp_path)
