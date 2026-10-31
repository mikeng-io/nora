"""Gate receipt admission/invalidation through real git and make; no Docker writes."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

from scripts import gate


def git(root, *args):
    return subprocess.check_output(  # noqa: S603 — fixed git argv in a generated repo
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        text=True,
    ).strip()


@pytest.fixture
def job(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Generated")
    git(repo, "config", "user.email", "generated")
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"rc": 0}))
    calls = tmp_path / "calls"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"control=Path({str(control)!r});calls=Path({str(calls)!r})\n"
        "with calls.open('a') as f:f.write(sys.argv[1]+'\\n')\n"
        "sys.exit(json.loads(control.read_text())['rc'])\n"
    )
    (repo / "Makefile").write_text(
        f"_gate:\n\t{gate.sys.executable} {driver} code\n"
        f"_full-gate:\n\t{gate.sys.executable} {driver} full\n"
        f"test-affected:\n\t{gate.sys.executable} {driver} docs\n"
    )
    (repo / "source.py").write_text("VALUE = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    context_file = tmp_path / "context"
    context_file.write_text("environment-one")

    def measured_context(path):
        assert path == repo
        return context_file.read_text()

    monkeypatch.setattr(gate, "PROJECT_ROOT", repo)
    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "pytest.lock")
    monkeypatch.setattr(gate, "context", measured_context)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MAKEOVERRIDES", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    receipt = repo / ".git/nora-gate-receipt.json"
    return repo, calls, control, context_file, receipt


def count(calls):
    return calls.read_text().splitlines() if calls.exists() else []


def test_real_make_runs_once_then_reuses_only_the_identical_success(job):
    repo, calls, _, context, receipt = job
    assert gate.main([]) == 0
    assert count(calls) == ["code"] and receipt.exists()
    assert gate.main([]) == 0 and count(calls) == ["code"]
    context.write_text("environment-two")
    assert gate.main([]) == 0 and count(calls) == ["code", "code"]
    (repo / "source.py").write_text("VALUE = 2\n")
    assert gate.main([]) == 0 and len(count(calls)) == 3 and not receipt.exists()
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "changed candidate")
    assert gate.main([]) == 0 and len(count(calls)) == 4
    assert gate.main([]) == 0 and len(count(calls)) == 4
    git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    assert gate.main([]) == 0 and len(count(calls)) == 5


@pytest.mark.parametrize(
    "mode", ["fresh", "CI", "override", "corrupt", "false-rc", "unknown", "error-context"]
)
def test_non_reusable_receipts_execute_and_failure_destroys_the_old_green(job, monkeypatch, mode):
    repo, calls, control, _, receipt = job
    assert gate.main([]) == 0 and receipt.exists()
    args = []
    if mode == "fresh":
        args = ["--fresh"]
    elif mode == "CI":
        monkeypatch.setenv("CI", "true")
    elif mode == "override":
        monkeypatch.setenv("MAKEOVERRIDES", "PYTEST=true")
    elif mode == "corrupt":
        receipt.write_text("{bad json")
    elif mode == "false-rc":
        value = json.loads(receipt.read_text())
        value["rc"] = False
        receipt.write_text(json.dumps(value))
    elif mode == "PYTHONPATH":
        monkeypatch.setenv("PYTHONPATH", str(repo))
    elif mode == "invalid-time":
        value = json.loads(receipt.read_text())
        value["completed_at"] = "not a timestamp"
        receipt.write_text(json.dumps(value))
    elif mode == "unknown":
        (repo / "unknown.py").write_text("x = 1")
    else:

        def unavailable(path):
            raise OSError("context unavailable")

        monkeypatch.setattr(gate, "context", unavailable)
    control.write_text(json.dumps({"rc": 1}))
    assert gate.main(args) != 0
    assert count(calls) == ["code", "code"] and not receipt.exists()
    control.write_text(json.dumps({"rc": 0}))
    assert gate.main(args) == 0 and len(count(calls)) == 3


def test_doc_only_candidate_runs_the_reader_path_and_mixed_code_keeps_static_checks(job):
    repo, calls, _, _, _ = job
    (repo / "README.md").write_text("generated documentation\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "docs")
    assert gate.main([]) == 0 and count(calls) == ["docs"]
    (repo / "source.py").write_text("VALUE = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "code")
    assert gate.main([]) == 0 and count(calls) == ["docs", "code"]


def test_changed_context_during_success_is_not_recorded(job):
    _, calls, control, context, receipt = job
    # The child really changes an external context input while checks execute.
    driver = control.parent / "driver.py"
    driver.write_text(driver.read_text() + f"\nPath({str(context)!r}).write_text('changed')\n")
    # Insert before sys.exit, which ends the generated check process.
    driver.write_text(
        driver.read_text().replace("sys.exit(json.loads(control.read_text())['rc'])\n", "")
    )
    assert gate.main([]) == 0 and count(calls) == ["code"] and not receipt.exists()


def test_a_cached_success_still_requires_host_admission(job):
    _, calls, _, _, _ = job
    assert gate.main([]) == 0
    with gate.host_lease(gate.LOCK_PATH):
        assert gate.main([]) == 2
    assert gate.main([]) == 0 and count(calls) == ["code"]


def test_actual_context_digest_tracks_private_environment_dependencies_tools_and_docker(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    dep = tmp_path / "dependency.py"
    dep.write_text("v1")
    tool = tmp_path / "tool"
    tool.write_text("tool-v1")
    monkeypatch.setattr(gate.shutil, "which", lambda name: str(tool) if name == "uv" else None)

    class Installed:
        def __init__(self):
            self.metadata = {"Name": "generated-dependency"}

        def read_text(self, name):
            assert name == "direct_url.json"
            return None

        version = "1"
        files = ("dependency.py",)

        def locate_file(self, name):
            assert name == "dependency.py"
            return dep

    monkeypatch.setattr(gate.importlib.metadata, "distributions", lambda: [Installed()])
    values = {
        ("context", "show"): "local",
        ("info", "--format", "{{.ID}}"): "daemon-one",
        ("version", "--format", "{{json .}}"): '{"Server":{"Version":"generated-v1"}}',
        (
            "image",
            "ls",
            "--no-trunc",
            "--digests",
            "--format",
            "{{.ID}} {{.Digest}}",
        ): "image-one digest-one",
        ("ps", "-aq", "--no-trunc"): "container-one",
        ("inspect", "container-one"): json.dumps(
            [
                {
                    "Id": "container-one",
                    "Image": "image-one",
                    "Mounts": [],
                    "Config": {"Env": ["GENERATED=fixture"]},
                    "State": {"Running": True, "StartedAt": "generated-start"},
                }
            ]
        ),
    }

    def docker(*args, repo):
        assert repo == tmp_path / "repo"
        if args == ("git", "config", "--null", "--list", "--show-origin"):
            return "generated git config"
        assert args[0] == "docker"
        return values[args[1:]]  # wrong operations fail, never one response for every request

    monkeypatch.setattr(gate, "command", docker)
    previous = gate.context(repo)
    changes = [
        lambda: monkeypatch.setenv("NORA_GENERATED_CONTEXT", "changed"),
        lambda: (repo / "secret.toml").write_text("generated-private-input"),
        lambda: dep.write_text("dependency-v2"),
        lambda: tool.write_text("tool-v2-changed"),
        lambda: values.__setitem__(("info", "--format", "{{.ID}}"), "daemon-two"),
        lambda: values.__setitem__(
            ("inspect", "container-one"),
            values[("inspect", "container-one")].replace("true", "false"),
        ),
        lambda: values.__setitem__(
            ("image", "ls", "--no-trunc", "--digests", "--format", "{{.ID}} {{.Digest}}"),
            "image-two digest-two",
        ),
    ]
    for change in changes:
        change()
        current = gate.context(repo)
        assert current != previous
        assert "generated-private-input" not in current and len(current) == 64
        previous = current


def test_full_gate_is_always_fresh_and_invalidates_narrower_receipts(job):
    _, calls, _, _, receipt = job
    assert gate.main([]) == 0 and receipt.exists()
    assert gate.main(["--full"]) == 0 and not receipt.exists()
    assert gate.main(["--full"]) == 0 and count(calls) == ["code", "full", "full"]
    assert gate.main([]) == 0 and count(calls) == ["code", "full", "full", "code"]


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_index_changes_cannot_reuse_receipts(job, flag):
    repo, calls, _, _, receipt = job
    assert gate.main([]) == 0 and receipt.exists()
    git(repo, "update-index", flag, "source.py")
    (repo / "source.py").write_text("hidden change")
    assert gate.main([]) == 0 and count(calls) == ["code", "code"] and not receipt.exists()


def test_interrupting_gate_stops_make_descendants_and_removes_old_success(job):
    import signal
    import sys
    import time

    repo, _calls, control, _, receipt = job
    assert gate.main([]) == 0 and receipt.exists()
    pidfile = control.parent / "child.pid"
    driver = control.parent / "driver.py"
    driver.write_text(
        "import os,signal\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('READY', flush=True)\nsignal.pause()\n"
    )
    code = (
        "import sys\nfrom pathlib import Path\nfrom scripts import gate\n"
        f"gate.PROJECT_ROOT=Path({str(repo)!r})\n"
        f"gate.LOCK_PATH=Path({str(gate.LOCK_PATH)!r})\n"
        "sys.exit(gate.main(['--fresh']))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    process = subprocess.Popen(  # noqa: S603 — generated no-Docker gate probe
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        assert process.stdout is not None
        for _ in range(5):
            if process.stdout.readline().strip() == "READY":
                break
        else:
            pytest.fail("generated check did not start")
        child = int(pidfile.read_text())
        os.kill(child, 0)
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=20)
        assert process.returncode == 130 and not receipt.exists()
        for _ in range(40):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                child = None
                break
            state = subprocess.run(  # noqa: S603 — fixed ps lookup of owned child
                ["ps", "-p", str(child), "-o", "stat="],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if state.startswith("Z"):
                child = None
                break
            time.sleep(0.05)
        assert child is None
        with gate.host_lease(gate.LOCK_PATH):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if child is not None:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)


def test_source_change_during_context_lookup_cannot_hit_an_old_receipt(job, monkeypatch):
    repo, calls, _, context, receipt = job
    assert gate.main([]) == 0

    def changing(path):
        assert path == repo
        (repo / "source.py").write_text("changed while context was sampled")
        return context.read_text()

    monkeypatch.setattr(gate, "context", changing)
    assert gate.main([]) == 0 and count(calls) == ["code", "code"] and not receipt.exists()


def test_interrupt_during_context_lookup_removes_previous_success(job, monkeypatch):
    repo, _, _, _, receipt = job
    assert gate.main([]) == 0 and receipt.exists()

    def interrupted(path):
        assert path == repo
        raise KeyboardInterrupt

    monkeypatch.setattr(gate, "context", interrupted)
    try:
        rc = gate.main([])
    except KeyboardInterrupt:
        pytest.fail("gate interrupted without invalidating its receipt")
    assert rc == 130 and not receipt.exists()
