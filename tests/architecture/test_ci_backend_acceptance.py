"""Backend CI execution, dependency records and validation budgets."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def _acceptance_source() -> tuple[str, dict]:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(s for s in workflow["jobs"]["ory"]["steps"]
                if s.get("name") == "Selected backend acceptance")
    source = step["run"].split("<<'PYTEST'\n", 1)[1].rsplit("PYTEST", 1)[0]
    return source, step


def _acceptance_env(monkeypatch, tmp_path, readers, *, testmon=None,
                    event_name="pull_request", base_sha="b" * 40,
                    record: bool = False):
    """Set every workflow input explicitly, including event and record presence."""
    monkeypatch.setenv("READERS", json.dumps(readers))
    monkeypatch.setenv("EVENT_NAME", event_name)
    monkeypatch.setenv("BASE_SHA", base_sha)
    if testmon is None:
        monkeypatch.delenv("TESTMON", raising=False)
    else:
        monkeypatch.setenv("TESTMON", testmon)
    monkeypatch.chdir(tmp_path)
    if record:
        (tmp_path / ".testmondata").write_bytes(b"record")


def _fake_run(calls, failure, selected_line=None):
    """Record gate calls and return the configured selector result or failure."""
    def run(command, **kwargs):
        if "affected_tests.py" in " ".join(map(str, command)):
            assert kwargs.get("capture_output") is True
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{selected_line}\n")
        assert kwargs.get("check") is True
        calls.append(command)
        if len(calls) - 1 == failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    return run


@pytest.mark.parametrize("targets", [
    "ALL", ["tests/integration/test_latency.py"],
    ["tests/unit/test_engagement_pipeline.py",
     "tests/integration/test_bridge_queue_telemetry.py"], [],
])
@pytest.mark.parametrize("failure", [None, 0, 1])
def test_timing_contracts_run_without_competing_pytest_workers(
    monkeypatch, tmp_path, targets, failure,
):
    source, _ = _acceptance_source()
    runner = [sys.executable, "-m", "scripts.pytest_gate",
              "--junit-dir", "backend-results"]
    expected = ([runner] if targets == "ALL"
                else [[*runner, *targets]] if targets else [])
    calls = []
    _acceptance_env(monkeypatch, tmp_path, targets)
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, failure))
    if failure is not None and failure < len(expected):
        with pytest.raises(subprocess.CalledProcessError):
            exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102 — trusted repo workflow
        assert calls == expected[:failure + 1]
    else:
        exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102 — trusted repo workflow
        assert calls == expected


def test_acceptance_script_without_a_record_resolves_to_all(
    monkeypatch, tmp_path,
):
    """The complete-acceptance helper fails broad without a record."""
    source, _ = _acceptance_source()
    calls = []
    _acceptance_env(monkeypatch, tmp_path, ["tests/test_docs.py"],
                    testmon="true", record=False)
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, None))
    exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102
    assert calls == [[sys.executable, "-m", "scripts.pytest_gate",
                      "--junit-dir", "backend-results"]]


def test_acceptance_script_with_a_record_uses_the_resolved_selection(
    monkeypatch, tmp_path,
):
    """The step resolves the real selector's line with the record present."""
    source, _ = _acceptance_source()
    calls = []
    _acceptance_env(monkeypatch, tmp_path, ["docs-reader"],
                    testmon="true", record=True)
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(calls, None, selected_line="tests/test_docs.py"))
    exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102
    assert calls == [[sys.executable, "-m", "scripts.pytest_gate",
                      "--junit-dir", "backend-results", "tests/test_docs.py"]]


def test_manual_dispatch_records_complete_acceptance(monkeypatch, tmp_path):
    """The manually selected acceptance step records the complete suite."""
    source, _ = _acceptance_source()
    calls = []
    _acceptance_env(monkeypatch, tmp_path, "ALL", event_name="workflow_dispatch")
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, None))
    exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102
    assert calls == [[sys.executable, "-m", "scripts.pytest_gate",
                      "--junit-dir", "backend-results", "--testmon"]]


def test_full_backend_replaces_the_nonblocking_measurement():
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
    assert "suite-timing" not in jobs
    assert "contracts" in jobs["checks"]["needs"]
    assert jobs["contracts"]["env"]["NORA_TEST_MAX_WORKERS"] == "4"
    fixed = next(step for step in jobs["contracts"]["steps"]
                 if step.get("name") == "Architecture and documentation contracts")
    assert "needs.classify.outputs.readers != '\"ALL\"'" in fixed["if"]
    steps = jobs["ory"]["steps"]
    upload = next(
        step for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
        and "backend-results/*" in step["with"]["path"]
    )
    assert upload["if"] == "${{ !cancelled() }}"
    record = next(
        step for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
        and step["with"]["path"] == ".testmondata"
    )
    # a partial record (failed run) must never reach pull requests
    assert record["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/master' && success() }}"
    )
    assert record["with"]["name"].startswith("testmon-data-")
    assert record["with"]["if-no-files-found"] == "error"


def test_automatic_backend_is_bounded_and_full_acceptance_is_manual():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / '.github/workflows/ci.yml').read_text())
    assert 'schedule' not in workflow[True]
    steps = workflow['jobs']['contracts']['steps']
    _, full = _acceptance_source()
    fast = next(s for s in steps if s.get('name') == 'Fast backend baseline')
    assert full['if'] == "github.event_name == 'workflow_dispatch'"
    assert fast['if'] == "github.event_name != 'workflow_dispatch'"
    assert fast['run'] == 'uv run python -m scripts.ci_fast_tests'
    assert not any(s.get('run') == 'uv run python -m scripts.ci_plan release' for s in steps)
    assert workflow['jobs']['contracts']['timeout-minutes'] == 4
    assert not any(s.get('continue-on-error', False) for s in steps)
