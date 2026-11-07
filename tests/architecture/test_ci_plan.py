"""Selection and result-join regressions for the hosted merge boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import ci_plan


@pytest.mark.parametrize(
    ("paths", "selected", "absent"),
    [
        (["docs/runbook.md"], {"contracts"}, {"python", "codeql-python", "portal"}),
        (["nora/doctrine/core.md"], {"python", "contracts"}, {"portal", "codeql-python"}),
        (["nora/app.py"], {"python", "codeql-python"}, {"portal", "go"}),
        (["app/src/page.tsx"], {"portal", "codeql-javascript"}, {"python"}),
        (["deploy/instagram-bridge/main.go"], {"go", "codeql-go"}, {"python"}),
        (["deploy/http-egress/main.go"], {"go", "codeql-go"}, {"python"}),
        (["uv.lock"], {"python", "dependency-review", "codeql-python"}, {"portal"}),
        (["app/package-lock.json"], {"portal", "dependency-review"}, {"go"}),
        (
            ["docs/a.md", "nora/a.py", "site/src/a.mdx"],
            {"python", "site", "codeql-javascript"},
            {"go"},
        ),
        ([".github/codeql/config.yml"], set(ci_plan.JOBS), set()),
        ([".github/workflows/ci.yml"], set(ci_plan.JOBS), set()),
        (["unknown.xyz"], set(ci_plan.JOBS), set()),
    ],
)
def test_components(paths, selected, absent):
    plan = ci_plan.classify(paths)
    assert all(plan[job] for job in selected)
    assert all(not plan[job] for job in absent)


@pytest.mark.parametrize(
    "damage", ["failure", "cancelled", "skipped", "missing", "selection-error"]
)
def test_selected_work_cannot_be_green_when_it_did_not_succeed(damage):
    plan = ci_plan.classify(["app/src/page.tsx"])
    needs = {job: {"result": "success" if chosen else "skipped"} for job, chosen in plan.items()}
    needs["classify"] = {"result": "success"}
    assert ci_plan.verify(plan, needs)  # positive control: the selected job really ran
    if damage == "missing":
        del needs["portal"]
    elif damage == "selection-error":
        needs["classify"]["result"] = "failure"
    else:
        needs["portal"]["result"] = damage
    assert not ci_plan.verify(plan, needs)
    assert not ci_plan.verify({}, needs)


@pytest.mark.parametrize("kind", ["pull_request", "push"])
def test_full_git_diff_includes_deleted_and_both_renamed_paths_without_file_cap(tmp_path, kind):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()  # noqa: S603

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (tmp_path / "nora").mkdir()
    (tmp_path / "nora/old.py").write_text("content")
    (tmp_path / "deleted.py").write_text("content")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "nora/old.py").rename(tmp_path / "ordinary.md")
    (tmp_path / "deleted.py").unlink()
    for n in range(350):
        (tmp_path / f"file{n}.md").write_text("doc")
    git("add", ".")
    git("commit", "-qm", "head")
    head = git("rev-parse", "HEAD")
    event = (
        {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
        if kind == "pull_request" else {"before": base, "after": head}
    )
    paths = ci_plan.changed_paths(event, tmp_path)
    assert len(paths) == 353
    assert {"nora/old.py", "ordinary.md", "deleted.py"} <= set(paths)
    assert ci_plan.classify(paths)["python"]


def test_workflow_join_and_trust_boundary():
    root = Path(__file__).resolve().parents[2]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    jobs = ci["jobs"]
    assert ci["name"] == "CI"
    assert ci[True]["pull_request"] is None
    wiki = yaml.safe_load((root / ".github/workflows/wiki.yml").read_text())
    for workflow in (ci, wiki):
        assert workflow["concurrency"]["cancel-in-progress"] == (
            "${{ github.event_name == 'pull_request' }}"
        )
        for key in ("github.workflow", "github.event_name", "github.sha"):
            assert key in workflow["concurrency"]["group"]
    assert jobs["checks"]["if"] == "always()"
    assert set(jobs["checks"]["needs"]) == {"classify", *ci_plan.JOBS}
    assert jobs["checks"]["steps"][-1]["run"] == "python3 -m scripts.ci_plan verify"
    for name in ci_plan.JOBS:
        assert "needs.classify.outputs.plan" in jobs[name]["if"]
    scans = {"codeql-python", "codeql-javascript", "codeql-go", "codeql-actions"}
    assert {name for name, job in jobs.items()
            if job.get("permissions", {}).get("security-events") == "write"} == scans
    for name in scans:
        steps = jobs[name]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["fetch-depth"] == 0
        assert "ref" not in checkout["with"]  # analyze the event's merge checkout
        analysis = next(i for i, s in enumerate(steps)
                        if s.get("uses", "").startswith("github/codeql-action/analyze@"))
        assert steps[analysis]["with"]["output"] == ".codeql-results"
        gates = [i for i, s in enumerate(steps)
                 if s.get("run") == "python3 -m scripts.ci_plan codeql"]
        assert len(gates) == 1 and gates[0] > analysis
    admin = yaml.safe_load((root / ".github/workflows/contribution-content.yml").read_text())
    assert admin["permissions"] == {} and "concurrency" not in admin
    assert "pull_request_target" in admin[True]
    triage = next(s for s in admin["jobs"]["pr-metadata"]["steps"]
                  if s.get("run") == "python3 -m scripts.triage_pr")
    assert triage["continue-on-error"] is True
    for job in admin["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", "")
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["ref"] == "master"
    assert not (root / ".github/workflows/pr-triage.yml").exists()
    assert not (root / ".github/workflows/security.yml").exists()


def test_selection_error_broadens_work_but_exits_nonzero(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {}}))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    assert ci_plan.main() == 1
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert all(json.loads(values["plan"]).values())
    assert json.loads(values["readers"]) == "ALL"
    assert json.loads(values["selection_error"])


@pytest.mark.parametrize("event_data", [{"pull_request": {}}, {"before": "base", "after": "head"}])
def test_document_readers_come_from_the_existing_selector(tmp_path, monkeypatch, event_data):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(event_data))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    monkeypatch.setattr(ci_plan, "changed_paths", lambda event, repo: ["docs/runbook.md"])
    calls = []

    def readers(paths, repo, **_kwargs):
        calls.append(paths)
        return ["tests/test_docs.py"]

    monkeypatch.setattr(ci_plan.affected_tests, "select", readers)
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    if "pull_request" in event_data:
        assert calls == [["docs/runbook.md"]]
        assert json.loads(values["readers"]) == ["tests/test_docs.py"]
        assert not json.loads(values["plan"])["python"]
    else:
        # non-PR events record: the whole suite runs, never a subset
        assert calls == []
        assert json.loads(values["readers"]) == "ALL"
        assert all(json.loads(values["plan"]).values())
    assert values["docs_only"] == "true"
    assert values["testmon"] == "false"


@pytest.mark.parametrize("canonical", [True, False])
def test_agent_skill_link_ci_uses_the_same_document_classification(
    tmp_path, monkeypatch, canonical,
):
    skill = tmp_path / ".agents/skills/nora-generated/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Generated skill\n")
    link = tmp_path / ".claude/skills/nora-generated"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../.agents/skills/nora-generated" if canonical else "/missing")
    (tmp_path / "tests").mkdir()
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {}}))
    output = tmp_path / "output"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    monkeypatch.setattr(ci_plan, "changed_paths", lambda event, repo: [
        ".agents/skills/nora-generated/SKILL.md", ".claude/skills/nora-generated",
    ])
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["docs_only"] == str(canonical).lower()
    selected = {job for job, chosen in json.loads(values["plan"]).items() if chosen}
    assert selected == ({"contracts"} if canonical else set(ci_plan.JOBS))
    if canonical:
        assert "tests/architecture/test_agent_skills.py" in json.loads(values["readers"])


def test_shared_component_contracts_select_their_readers():
    assert ci_plan.classify(["nora/documents/node.schema.json"])["portal"]
    assert ci_plan.classify(["tests/fixtures/instagram_bridge/account.json"])["go"]


def test_shared_document_reader_keeps_full_suite_fallback(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {}}))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    monkeypatch.setattr(ci_plan, "changed_paths", lambda event, repo: ["deploy/computer/README.md"])
    monkeypatch.setattr(ci_plan.affected_tests, "select", lambda paths, repo, **_k: "ALL")
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["readers"]) == "ALL"
    plan = json.loads(values["plan"])
    assert plan["contracts"] and not plan["python"]
    assert not any(selected for name, selected in plan.items() if name.startswith("codeql-"))


def _acceptance_source() -> tuple[str, dict]:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(s for s in workflow["jobs"]["contracts"]["steps"]
                if s.get("name") == "Selected backend acceptance")
    source = step["run"].split("<<'PYTEST'\n", 1)[1].rsplit("PYTEST", 1)[0]
    return source, step


def _acceptance_env(monkeypatch, tmp_path, readers, *, testmon=None,
                    event_name="pull_request", base_sha="b" * 40,
                    record: bool = False):
    """The script's whole input surface, set — never ambient. The exec'd
    workflow step reads READERS, TESTMON, BASE_SHA, EVENT_NAME and
    .testmondata in its cwd; inheriting any of them from the pytest process
    (hosted: TESTMON=true from the step env; local: a gitignored record) made
    this test pass locally and fail hosted."""
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
    """Subprocess.run for the exec'd step: answers the selector call with a
    fixed selection and records the gate command. A fake behaves like the
    real subprocess, including the wrong-call shapes."""
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


def test_a_pull_request_without_a_record_runs_the_whole_suite(
    monkeypatch, tmp_path,
):
    """TESTMON=true with no .testmondata resolves to ALL, never a subset."""
    source, _ = _acceptance_source()
    calls = []
    _acceptance_env(monkeypatch, tmp_path, ["tests/test_docs.py"],
                    testmon="true", record=False)
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, None))
    exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102
    assert calls == [[sys.executable, "-m", "scripts.pytest_gate",
                      "--junit-dir", "backend-results"]]


def test_a_pull_request_with_a_record_uses_the_resolved_selection(
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


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch"])
def test_a_non_pull_request_run_records_the_whole_suite(
    monkeypatch, tmp_path, event_name,
):
    """Push/schedule/dispatch run the full suite WITH recording — a partial
    record would lie about coverage."""
    source, _ = _acceptance_source()
    calls = []
    _acceptance_env(monkeypatch, tmp_path, "ALL", event_name=event_name)
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, None))
    exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102
    assert calls == [[sys.executable, "-m", "scripts.pytest_gate",
                      "--junit-dir", "backend-results", "--testmon"]]


@pytest.mark.parametrize(
    ("severity", "line", "level", "expected"),
    [("7.0", 2, "warning", 1), ("9.9", 1, "warning", 0),
     ("6.9", 2, "warning", 0), ("0", 2, "error", 1)],
)
@pytest.mark.parametrize("component", ["driver", "extension"])
@pytest.mark.parametrize("drift", [False, True])
def test_codeql_gates_added_high_severity_locations(
    tmp_path, severity, line, level, expected, component, drift,
):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()  # noqa: S603

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (tmp_path / "sample.py").write_text("existing\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "sample.py").write_text("existing\nadded\n")
    git("add", ".")
    git("commit", "-qm", "head")
    head = git("rev-parse", "HEAD")
    git("switch", "-qc", "tested-base", base)
    if drift:
        (tmp_path / "sample.py").write_text("prefix\n" * 6 + "existing\n")
        git("add", ".")
        git("commit", "-qm", "master advances above the PR change")
        line += 6  # SARIF uses the analyzed merge tree's line numbers.
    base = git("rev-parse", "HEAD")
    git("merge", "--no-ff", head, "-qm", "tested merge")
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    directory = tmp_path / "sarif"
    directory.mkdir()
    report = {"runs": [{
        "tool": {"driver": {"rules": [{
            "id": "synthetic/rule", "properties": {"security-severity": severity},
        }]}},
        "results": [{"ruleId": "synthetic/rule", "level": level, "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": "sample.py"}, "region": {"startLine": line},
            },
        }]}],
    }]}
    if component == "extension":
        tool = report["runs"][0]["tool"]
        # Current CodeQL puts query-pack rule descriptors in tool.extensions.
        tool["extensions"] = [{"name": "synthetic-query-pack", "rules": tool["driver"]["rules"]}]
        tool["driver"]["rules"] = []
    (directory / "python.sarif").write_text(json.dumps(report))
    assert ci_plan.codeql_findings(event, tmp_path, directory) == expected
    # The same historical findings are uploaded by baseline events without blocking.
    assert ci_plan.codeql_findings({}, tmp_path, directory) == 0
    (directory / "python.sarif").write_text('{"runs": []}')
    with pytest.raises(ValueError, match="no analysis runs"):
        ci_plan.codeql_findings(event, tmp_path, directory)


def test_absent_codeql_output_is_not_a_clean_scan(tmp_path):
    with pytest.raises(ValueError, match="no SARIF"):
        ci_plan.codeql_findings({}, tmp_path, tmp_path)


def test_codeql_rejects_a_pr_checkout_that_is_not_the_analyzed_merge(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
         "commit", "--allow-empty", "-qm", "base"], cwd=tmp_path, check=True,
    )
    (tmp_path / "empty.sarif").write_text(json.dumps({"runs": [{
        "tool": {"driver": {"rules": []}}, "results": [],
    }]}))
    assert ci_plan.codeql_findings({}, tmp_path, tmp_path) == 0
    with pytest.raises(ValueError, match="analyzed PR merge"):
        ci_plan.codeql_findings({"pull_request": {}}, tmp_path, tmp_path)


def test_hosted_go_jobs_cover_each_deployed_module():
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
    modules = {str(path.parent.relative_to(root)) for path in root.glob("deploy/*/go.mod")}
    assert "deploy/http-egress" in modules
    for name in ("go", "codeql-go"):
        job = jobs[name]
        assert set(job["strategy"]["matrix"]["module"]) == modules
        builds = [step for step in job["steps"] if step.get("run", "").startswith("go ")]
        assert builds
        assert all(step["working-directory"] == "${{ matrix.module }}" for step in builds)


@pytest.mark.parametrize(("paths", "readers", "testmon"), [
    (["nora/app.py"], "DEFERRED", True),
    (["nora/doctrine/core.md"], "ALL", False),
    (["evals/rebuilt/entry.py"], "NARROWED", True),  # a mutation table names it
    (["tests/conftest.py"], "ALL", False),
    (["uv.lock"], "ALL", False),
    (["unknown.xyz"], "NARROWED", False),
    (["docs/runbook.md", "nora/app.py"], "NARROWED", True),
    (["nora/renamed_away.py", "docs/new.md"], "NARROWED", True),
])
def test_backend_acceptance_uses_the_complete_diff(
    tmp_path, monkeypatch, paths, readers, testmon,
):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {}}))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    monkeypatch.setattr(ci_plan, "changed_paths", lambda event, repo: paths)
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    got = json.loads(values["readers"])
    if readers == "ALL":
        assert got == "ALL"
    elif readers == "DEFERRED":
        assert got == []  # the contracts job resolves the record side
    else:
        assert got != "ALL" and got
    assert values["testmon"] == str(testmon).lower()
    assert values["docs_only"] == "false"
    plan = json.loads(values["plan"])
    needs = {job: {"result": "success" if chosen else "skipped"}
             for job, chosen in plan.items()}
    needs["classify"] = {"result": "success"}
    assert ci_plan.verify(plan, needs)
    for damage in ("failure", "cancelled", "skipped"):
        needs["contracts"]["result"] = damage
        assert not ci_plan.verify(plan, needs)
    del needs["contracts"]
    assert not ci_plan.verify(plan, needs)


@pytest.mark.parametrize("event_data", [{}, {"schedule": "weekly"}])
def test_baseline_events_select_the_full_backend(tmp_path, monkeypatch, event_data):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(event_data))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["readers"]) == "ALL"
    assert all(json.loads(values["plan"]).values())


def _release_repo(
    root: Path, edits: dict[str, str], *, base_over: dict[str, str] | None = None,
    delete: list[str] | None = None, chmod: dict[str, int] | None = None,
) -> dict:
    """A real git repo + release-shaped event for release_eligible."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=root, check=True,  # noqa: S603 - fixed git argv, tmp repo
                              capture_output=True, text=True)
    root.mkdir(parents=True, exist_ok=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    base_files = {
        "nora/__init__.py": '__version__ = "1.0.0"\n',
        "pyproject.toml": '[project]\nversion = "1.0.0"\n',
        "uv.lock": '[[package]]\nname = "nora"\nversion = "1.0.0"\n'
                   '[[package]]\nname = "dep"\nversion = "3"\n',
        ".release-please-manifest.json": '{\n  ".": "1.0.0"\n}\n',
        "CHANGELOG.md": "# changelog\n",
        **(base_over or {}),
    }
    for rel, content in base_files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    run("add", ".")
    run("commit", "-qm", "base")
    base = run("rev-parse", "HEAD").stdout.strip()
    for rel, content in edits.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    for rel in delete or []:
        run("rm", "-q", rel)
    for rel, mode in (chmod or {}).items():
        os.chmod(root / rel, mode)
    run("add", "-A")
    run("commit", "-qm", "release bump")
    head = run("rev-parse", "HEAD").stdout.strip()
    return {"pull_request": {
        "base": {"sha": base},
        "head": {"sha": head, "ref": ci_plan.RELEASE_HEAD_REF,
                 "repo": {"full_name": "mikeng-io/nora"}},
    }}


_RELEASE_BUMP = {
    "nora/__init__.py": '__version__ = "1.0.1"\n',
    "pyproject.toml": '[project]\nversion = "1.0.1"\n',
    "uv.lock": '[[package]]\nname = "nora"\nversion = "1.0.1"\n'
               '[[package]]\nname = "dep"\nversion = "3"\n',
    ".release-please-manifest.json": '{\n  ".": "1.0.1"\n}\n',
    "CHANGELOG.md": "# changelog\n\n## 1.0.1\n",
}


def test_a_real_release_diff_is_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(tmp_path, _RELEASE_BUMP)
    assert ci_plan.release_eligible(tmp_path, event)


@pytest.mark.parametrize("extra", [
    {"nora/agent/whereis.py": "x = 1\n"},
    {"nora/__init__.py": '__version__ = "1.0.1"\nprint("hi")\n'},
    {"scripts/affected_tests.py": "x = 1\n"},
])
def test_a_runtime_or_content_change_is_not_eligible(tmp_path, monkeypatch, extra):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(tmp_path, {**_RELEASE_BUMP, **extra})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_fork_head_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(tmp_path, _RELEASE_BUMP)
    event["pull_request"]["head"]["repo"]["full_name"] = "someone-else/nora"
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_function_local_version_edit_is_not_eligible(tmp_path, monkeypatch):
    """`__version__ = "safe"` inside a function is not the release field —
    the AST compare, not line spelling, must reject it."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    base_init = ('__version__ = "1.0.0"\n\n'
                 'def f():\n    __version__ = "safe"\n    return __version__\n')
    head_init = base_init.replace('"safe"', '"broken"')
    event = _release_repo(
        tmp_path, {**_RELEASE_BUMP, "nora/__init__.py": head_init},
        base_over={"nora/__init__.py": base_init})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_version_edit_in_an_unrelated_toml_section_is_not_eligible(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    base = '[project]\nversion = "1.0.0"\n[tool.x]\nversion = "9"\n'
    # the release bump lands AND an unrelated section changes
    head = '[project]\nversion = "1.0.1"\n[tool.x]\nversion = "10"\n'
    event = _release_repo(
        tmp_path, {**_RELEASE_BUMP, "pyproject.toml": head},
        base_over={"pyproject.toml": base})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_deleted_release_file_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    edits = {k: v for k, v in _RELEASE_BUMP.items() if k != "uv.lock"}
    event = _release_repo(tmp_path, edits, delete=["uv.lock"])
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_mode_change_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(
        tmp_path, _RELEASE_BUMP, chmod={"CHANGELOG.md": 0o755})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_side_effect_version_expression_is_not_eligible(
    tmp_path, monkeypatch,
):
    """`__version__ = str(print(...))` parses equal after scrubbing but is
    not the version line — the strict byte rule rejects it."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(
        tmp_path,
        {**_RELEASE_BUMP,
         "nora/__init__.py": '__version__ = str(print("SIDE EFFECT"))\n'})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_docstring_line_spelling_the_version_is_not_eligible(
    tmp_path, monkeypatch,
):
    """A docstring line that spells `__version__ = "x.y.z"` passes the byte
    pattern — only the AST compare knows it is not the assignment."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    base_init = '__version__ = "1.0.0"\n"""release notes."""\n'
    head_init = '__version__ = "1.0.0"\n__version__ = "9.9.9"\n"""rel."""\n'
    event = _release_repo(
        tmp_path, {**_RELEASE_BUMP, "nora/__init__.py": head_init},
        base_over={"nora/__init__.py": base_init})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_coding_cookie_change_is_not_eligible(tmp_path, monkeypatch):
    """A `# coding:` change parses to an identical AST but runs different —
    two hunks is already ineligible; non-ASCII makes it doubly so."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    base_init = '# coding: utf-8\n__version__ = "1.0.0"\n"""café."""\n'
    head_init = ('# coding: latin-1\n__version__ = "1.0.1"\n"""café."""\n')
    event = _release_repo(
        tmp_path, {**_RELEASE_BUMP, "nora/__init__.py": head_init},
        base_over={"nora/__init__.py": base_init})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_second_hunk_beside_the_version_bump_is_not_eligible(
    tmp_path, monkeypatch,
):
    """Even a comment/whitespace line next to the bump fails: release PRs
    change nothing else — that strictness is intended."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    head_init = '# touched\n__version__ = "1.0.1"\n'
    event = _release_repo(
        tmp_path, {**_RELEASE_BUMP, "nora/__init__.py": head_init},
        base_over={"nora/__init__.py": '__version__ = "1.0.0"\n'})
    assert not ci_plan.release_eligible(tmp_path, event)


def test_a_crlf_or_bom_version_line_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    for i, init in enumerate(
        ('__version__ = "1.0.1"\r\n', '\ufeff__version__ = "1.0.1"\n')
    ):
        root = tmp_path / str(i)
        event = _release_repo(
            root, {**_RELEASE_BUMP, "nora/__init__.py": init})
        assert not ci_plan.release_eligible(root, event)


def test_an_unrelated_branch_name_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo(tmp_path, _RELEASE_BUMP)
    event["pull_request"]["head"]["ref"] = "feature/x"
    assert not ci_plan.release_eligible(tmp_path, event)


@pytest.mark.parametrize("event", [
    {"before": "a" * 40, "after": "b" * 40},
    {"schedule": "cron"},
    {"workflow_dispatch": {}},
])
def test_non_pull_request_events_select_the_full_suite(tmp_path, monkeypatch, event):
    """Push, schedule and dispatch record the whole suite — a subset record
    would lie about coverage and the release check could not trust it."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr("sys.argv", ["ci_plan"])
    monkeypatch.setattr(ci_plan, "changed_paths", lambda e, r: ["docs/runbook.md"])
    monkeypatch.setattr(
        ci_plan.affected_tests, "select", lambda p, r, **_k: ["tests/test_docs.py"],
    )
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["readers"]) == "ALL"


def test_the_escape_report_is_unavailable_when_the_diff_cannot_be_read(
    tmp_path, monkeypatch,
):
    def broken(event, repo):
        raise RuntimeError("object not found")
    monkeypatch.setattr(ci_plan, "changed_paths", broken)
    monkeypatch.setattr(ci_plan.testmon_record, "fetch", lambda repo: None)
    lines = ci_plan._escape(tmp_path, {"before": "a", "after": "b"})
    assert lines[0] == "### Escape report"
    assert "unavailable" in lines[1]
    assert "object not found" in lines[1]


@pytest.mark.parametrize(("runs", "verdict"), [
    ([], "missing"),
    ([{"status": "completed", "conclusion": "success"}], "success"),
    ([{"status": "completed", "conclusion": "failure"}], "failure"),
    ([{"status": "completed", "conclusion": "cancelled"}], "failure"),
    ([{"status": "completed", "conclusion": "timed_out"}], "failure"),
    ([{"status": "in_progress", "conclusion": None}], "pending"),
    ([{"status": "queued", "conclusion": None}], "pending"),
])
def test_release_pr_waits_for_the_base_commit_full_run(runs, verdict):
    assert ci_plan.base_run_verdict(runs) == verdict


def test_escape_report_marks_only_failures_the_selection_skipped():
    failed = [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_b.py::test_two",
        "tests/architecture/test_meta.py::test_three",
    ]
    selected = ["tests/unit/test_b.py", "tests/architecture"]
    lines = ci_plan.escape_lines(failed, selected)
    report = "\n".join(lines)
    assert "escapes (red, not selected): 1" in report
    assert "`tests/unit/test_a.py::test_one` — ESCAPED" in report
    assert "`tests/unit/test_b.py::test_two` — selected" in report
    assert "`tests/architecture/test_meta.py::test_three` — selected" in report
    # positive control: a selected-all run escapes nothing
    all_lines = ci_plan.escape_lines(failed, "ALL")
    assert "escapes (red, not selected): 0" in "\n".join(all_lines)


def test_full_backend_replaces_the_nonblocking_measurement():
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
    assert "suite-timing" not in jobs
    assert "contracts" in jobs["checks"]["needs"]
    assert jobs["contracts"]["env"]["NORA_TEST_MAX_WORKERS"] == "4"
    steps = jobs["contracts"]["steps"]
    fixed = next(step for step in steps
                 if step.get("name") == "Architecture and documentation contracts")
    assert "needs.classify.outputs.readers != '\"ALL\"'" in fixed["if"]
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
    assert record["if"] == "${{ github.event_name != 'pull_request' && success() }}"
    assert record["with"]["name"].startswith("testmon-data-")
    assert record["with"]["if-no-files-found"] == "error"


def _release_repo_with_symlink(root: Path, mode: int = 0o120000) -> dict:
    """Base/head where nora/__init__.py is a symlink (or a mode-100755 file):
    the raw diff is modification-only but the object is not a regular file."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=root, check=True,  # noqa: S603
                              capture_output=True, text=True)
    root.mkdir(parents=True, exist_ok=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    for rel, content in {
        "pyproject.toml": '[project]\nversion = "1.0.0"\n',
        "uv.lock": '[[package]]\nname = "nora"\nversion = "1.0.0"\n',
        ".release-please-manifest.json": '{\n  ".": "1.0.0"\n}\n',
        "CHANGELOG.md": "# changelog\n",
        "real_v1.py": "X = 10\n", "real_v2.py": "X = 11\n",
    }.items():
        (root / rel).write_text(content)
    link = root / "nora/__init__.py"
    link.parent.mkdir(parents=True)
    if mode == 0o120000:
        link.symlink_to('__version__ = "1.0.0"')
        run("add", ".")
        run("commit", "-qm", "base")
        base = run("rev-parse", "HEAD").stdout.strip()
        link.unlink()
        link.symlink_to('__version__ = "1.0.1"')
    else:
        (root / "nora").mkdir(exist_ok=True)
        link.write_text('__version__ = "1.0.0"\n')
        os.chmod(link, mode)
        run("add", ".")
        run("commit", "-qm", "base")
        base = run("rev-parse", "HEAD").stdout.strip()
        link.write_text('__version__ = "1.0.1"\n')
        os.chmod(link, mode)
    run("add", "-A")
    run("commit", "-qm", "retarget")
    head = run("rev-parse", "HEAD").stdout.strip()
    return {"pull_request": {
        "base": {"sha": base},
        "head": {"sha": head, "ref": ci_plan.RELEASE_HEAD_REF,
                 "repo": {"full_name": "mikeng-io/nora"}},
    }}


def test_a_symlink_retarget_is_not_a_release_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo_with_symlink(tmp_path / "ln")
    assert not ci_plan.release_eligible(tmp_path / "ln", event)


def test_an_executable_release_file_is_not_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "mikeng-io/nora")
    event = _release_repo_with_symlink(tmp_path / "x", mode=0o100755)
    assert not ci_plan.release_eligible(tmp_path / "x", event)
