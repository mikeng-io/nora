"""Fast CI reports subprocess failure without broadening into complete acceptance."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import ci_fast_tests, pytest_gate


@pytest.mark.parametrize('rc', [0, 1, 124, 137])
def test_fast_baseline_keeps_its_budget_scope_and_failure(monkeypatch, rc):
    calls = []

    def run(command):
        # No wall-clock may wrap the gate: the job's timeout-minutes is the bound,
        # and an inner deadline landed inside healthy teardown (NOR-24).
        assert command[:5] == [
            sys.executable, '-m', 'scripts.pytest_gate', '--junit-dir', 'backend-results',
        ]
        targets = command[5:]
        assert 'tests/unit/test_ci_fast_tests.py' in targets
        assert 'tests/domain' in targets
        assert 'tests/unit/test_release_deployment.py' not in targets
        assert 'tests/unit/test_engagement_pipeline.py' not in targets
        assert all((Path.cwd() / target).is_file() for target in targets if target.endswith('.py'))
        _, serial = pytest_gate.partition(targets)
        assert serial == []
        assert 'tests/integration' not in command and '--testmon' not in command
        calls.append(command)
        return subprocess.CompletedProcess(command, rc)

    monkeypatch.setattr(subprocess, 'run', run)
    assert ci_fast_tests.main() == rc
    assert len(calls) == 1


def test_a_missing_unit_tree_cannot_pass_as_an_empty_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="unit test tree unavailable"):
        ci_fast_tests.main()


def test_file_selection_keeps_both_pytest_patterns_but_no_serial_phase(tmp_path, monkeypatch):
    unit = tmp_path / "tests/unit"
    unit.mkdir(parents=True)
    for name in ("test_new.py", "new_test.py", "test_engagement_pipeline.py"):
        (unit / name).write_text("def test_example(): pass\n")
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(command):
        targets = command[5:]
        assert "tests/unit/test_new.py" in targets
        assert "tests/unit/new_test.py" in targets
        assert "tests/unit/test_engagement_pipeline.py" not in targets
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert ci_fast_tests.main() == 0
    assert len(calls) == 1
