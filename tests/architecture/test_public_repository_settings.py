"""The owner settings command must preserve privacy and send structured API bodies."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "public_settings", ROOT / "scripts/configure_public_github.py"
)
assert spec and spec.loader
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


def test_private_repository_is_read_only_even_with_apply(monkeypatch):
    reads = []

    def private_api(repo, path="", method="GET", body=None):
        reads.append((path, method))
        if path or method != "GET" or body:
            pytest.fail("private repository received a mutation")
        return {"private": True}

    monkeypatch.setattr(settings, "api", private_api)
    monkeypatch.setattr("sys.argv", ["configure_public_github.py", "--apply"])
    with pytest.raises(SystemExit) as exit_info:
        settings.main()
    assert exit_info.value.code == 1
    assert reads == [("", "GET")]


def test_api_sends_json_body_through_stdin_without_shell(monkeypatch):
    calls = []

    def gh(command, *, input, text):
        calls.append((command, input, text))
        # Mirror gh --input behavior: invalid/missing JSON cannot reach the API.
        body = json.loads(input)
        return json.dumps({"enabled": body["enabled"]})

    monkeypatch.setattr(settings.subprocess, "check_output", gh)
    result = settings.api("example/repository", "/actions/permissions", "PUT", {"enabled": True})
    assert result == {"enabled": True}
    command, stdin, as_text = calls[0]
    assert command[1:] == [
        "api",
        "repos/example/repository/actions/permissions",
        "--method",
        "PUT",
        "--input",
        "-",
    ]
    assert json.loads(stdin) == {"enabled": True}
    assert as_text is True


def _rules():
    spec = importlib.util.spec_from_file_location(
        "github_rules", ROOT / "scripts/configure_github_rules.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readback(rules):
    """What GitHub returns for the rulesets this script writes."""
    repo_state = dict(rules.REPO_SETTINGS)
    return repo_state, rules.branch_ruleset(), rules.tag_ruleset()


def test_rules_require_exactly_the_checks_that_report_on_every_pr_and_allow_no_bypass():
    rules = _rules()
    branch = rules.branch_ruleset()
    assert rules.REQUIRED == ["checks", "pr-metadata"]
    assert branch["bypass_actors"] == [] and rules.tag_ruleset()["bypass_actors"] == []
    contexts = [
        c["context"]
        for rule in branch["rules"]
        if rule["type"] == "required_status_checks"
        for c in rule["parameters"]["required_status_checks"]
    ]
    # CodeQL and dependency-review are skipped while private and the wiki build is
    # path-filtered: requiring any of them would block every PR that never reports one.
    assert contexts == ["checks", "pr-metadata"]


def test_a_matching_readback_passes_and_every_drift_is_named():
    rules = _rules()
    assert rules.problems(*_readback(rules)) == []

    repo_state, branch, tag = _readback(rules)
    branch["bypass_actors"] = [{"actor_id": 1, "actor_type": "RepositoryRole"}]
    assert any("bypass" in p for p in rules.problems(repo_state, branch, tag))

    repo_state, branch, tag = _readback(rules)
    branch["rules"] = [r for r in branch["rules"] if r["type"] != "required_status_checks"]
    assert any("required checks" in p for p in rules.problems(repo_state, branch, tag))

    repo_state, branch, tag = _readback(rules)
    repo_state["allow_squash_merge"] = True
    assert any("allow_squash_merge" in p for p in rules.problems(repo_state, branch, tag))

    repo_state, branch, tag = _readback(rules)
    tag["rules"] = [{"type": "deletion"}]
    assert any("immutable" in p for p in rules.problems(repo_state, branch, tag))

    repo_state, branch, tag = _readback(rules)
    for rule in branch["rules"]:
        if rule["type"] == "pull_request":
            rule["parameters"]["required_review_thread_resolution"] = False
    assert any(
        "required_review_thread_resolution" in p for p in rules.problems(repo_state, branch, tag)
    )

    repo_state, branch, tag = _readback(rules)
    for rule in branch["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"][0]["integration_id"] = 1
    assert any("bound to" in p for p in rules.problems(repo_state, branch, tag))

    repo_state, branch, tag = _readback(rules)
    branch["conditions"] = {"ref_name": {"include": ["refs/heads/other"], "exclude": []}}
    assert any("applies to" in p for p in rules.problems(repo_state, branch, tag))


def test_nothing_is_written_without_apply(monkeypatch):
    rules = _rules()

    def no_api(*args, **kwargs):
        pytest.fail("a dry run called the GitHub API")

    monkeypatch.setattr(rules, "api", no_api)
    assert rules.main(["--repo", "example/repository"]) == 0
