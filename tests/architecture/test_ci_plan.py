"""Selection and result-join regressions for the hosted merge boundary."""

from __future__ import annotations

import json
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

    def readers(paths, repo):
        calls.append(paths)
        return ["tests/test_docs.py"]

    monkeypatch.setattr(ci_plan.affected_tests, "select", readers)
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert calls == [["docs/runbook.md"]]
    assert json.loads(values["readers"]) == ["tests/test_docs.py"]
    assert values["docs_only"] == "true"
    assert not json.loads(values["plan"])["python"]


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
    monkeypatch.setattr(ci_plan.affected_tests, "select", lambda paths, repo: "ALL")
    assert ci_plan.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["readers"]) == "ALL"
    plan = json.loads(values["plan"])
    assert plan["contracts"] and not plan["python"]
    assert not any(selected for name, selected in plan.items() if name.startswith("codeql-"))


@pytest.mark.parametrize("targets", [
    "ALL", ["tests/integration/test_latency.py"],
    ["tests/unit/test_engagement_pipeline.py",
     "tests/integration/test_bridge_queue_telemetry.py"], [],
])
@pytest.mark.parametrize("failure", [None, 0, 1])
def test_timing_contracts_run_without_competing_pytest_workers(monkeypatch, targets, failure):
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(s for s in workflow["jobs"]["contracts"]["steps"]
                if s.get("name") == "Document readers selected by the existing selector")
    source = step["run"].split("<<'PYTEST'\n", 1)[1].rsplit("PYTEST", 1)[0]
    runner = [sys.executable, "-m", "scripts.pytest_gate"]
    expected = ([runner] if targets == "ALL"
                else [[*runner, *targets]] if targets else [])
    calls = []

    def run(command, *, check):
        assert check is True
        calls.append(command)
        if len(calls) - 1 == failure:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setenv("READERS", json.dumps(targets))
    monkeypatch.setattr(subprocess, "run", run)
    if failure is not None and failure < len(expected):
        with pytest.raises(subprocess.CalledProcessError):
            exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102 — trusted repo workflow
        assert calls == expected[:failure + 1]
    else:
        exec(compile(source, ".github/workflows/ci.yml", "exec"), {})  # noqa: S102 — trusted repo workflow
        assert calls == expected


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
