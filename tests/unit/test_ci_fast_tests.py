"""Fast CI reports subprocess failure without broadening into complete acceptance."""

import subprocess

import pytest

from scripts import ci_fast_tests


@pytest.mark.parametrize('rc', [0, 1, 124, 137])
def test_fast_baseline_keeps_its_budget_scope_and_failure(monkeypatch, rc):
    calls = []

    def run(command, *, env):
        assert command[:4] == ['timeout', '--signal=INT', '--kill-after=10s', '170s']
        assert command[5:8] == ['-m', 'scripts.pytest_gate', '--junit-dir']
        assert command[9:11] == ['tests/unit', 'tests/domain']
        assert 'tests/integration' not in command and '--testmon' not in command
        assert '--ignore=tests/unit/test_release_deployment.py' in env['PYTEST_ADDOPTS']
        calls.append(command)
        return subprocess.CompletedProcess(command, rc)

    monkeypatch.setattr(subprocess, 'run', run)
    assert ci_fast_tests.main() == rc
    assert len(calls) == 1
