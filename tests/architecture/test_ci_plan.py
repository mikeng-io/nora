"""Selection and result-join regressions for the hosted merge boundary."""

from __future__ import annotations

import json
import subprocess
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
    assert jobs["checks"]["if"] == "always()"
    assert set(jobs["checks"]["needs"]) == {"classify", *ci_plan.JOBS}
    assert jobs["checks"]["steps"][-1]["run"] == "python3 -m scripts.ci_plan verify"
    for name in ci_plan.JOBS:
        assert "needs.classify.outputs.plan" in jobs[name]["if"]
    admin = yaml.safe_load((root / ".github/workflows/contribution-content.yml").read_text())
    assert admin["permissions"] == {} and "concurrency" not in admin
    assert "pull_request_target" in admin[True]
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


@pytest.mark.parametrize(
    ("severity", "line", "level", "expected"),
    [("7.0", 2, "warning", 1), ("9.9", 1, "warning", 0),
     ("6.9", 2, "warning", 0), ("0", 2, "error", 1)],
)
@pytest.mark.parametrize("component", ["driver", "extension"])
def test_codeql_gates_added_high_severity_locations(
    tmp_path, severity, line, level, expected, component,
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
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": git("rev-parse", "HEAD")}}}
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
