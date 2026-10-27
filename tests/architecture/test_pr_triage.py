"""Labels and the assignee come from the title and the changed paths, not from memory.

The triage job runs with write access on `pull_request_target`, so it must read data
only: the title and the file list select labels from a fixed list and never reach a
shell. People and bots share classification; assignment is person-only. Authors
with their own labeling are selected by trusted configuration, not bot-specific code.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from scripts import triage_pr as triage
from scripts.check_commit_subject import TYPES


def user_pr(
    title="fix(bridge): require a token", labels=(), assignees=(), kind="User", login="owner"
):
    return {
        "title": title,
        "user": {"login": login, "type": kind},
        "labels": [{"name": n} for n in labels],
        "assignees": [{"login": n} for n in assignees],
    }


class Fake:
    def __init__(self, item, files=("nora/app/x.py",), existing=("type: fix",)):
        self.item, self.files, self.existing = item, files, set(existing)
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/repos/o/r/pulls/7"):
            return self.item
        if method == "GET" and "/pulls/7/files" in path:
            return [{"filename": f} for f in self.files]
        if method == "GET" and "/labels/" in path:
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if name in self.existing:
                return {"name": name}
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        if method == "POST" and path == "/repos/o/r/labels":
            self.existing.add(body["name"])
            return body
        if method in {"POST", "DELETE"} and "/issues/7/" in path:
            return []
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_the_type_label_is_the_title_type_and_a_bang_adds_breaking():
    assert set(triage.wanted("fix: a thing", [])) == {"type: fix"}
    assert set(triage.wanted("feat(api)!: drop a verb", [])) == {"type: feat", "breaking"}
    assert triage.wanted("did stuff", []) == {}, "an undeclared title gets no label"
    assert triage.wanted("merge: the arc (#1)", []) == {}, "merge commits are not labelled"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("nora/engagement/x.py", {"runtime"}),
        ("nora/doctrine/rooms/voice.md", {"doctrine"}),
        ("nora/skills/a/SKILL.md", {"doctrine"}),
        ("nora/web/portal.py", {"portal"}),
        ("app/src/a.ts", {"portal"}),
        ("site/src/a.astro", {"site"}),
        ("deploy/komodo/README.md", {"deploy"}),
        ("Dockerfile", {"deploy"}),
        (".github/workflows/ci.yml", {"ci"}),
        ("scripts/check_commit_subject.py", {"ci"}),
        ("docs/runbook.md", {"docs"}),
        ("README.md", {"docs"}),
        ("tests/test_a.py", {"tests"}),
        ("uv.lock", {"deps"}),
        ("app/package-lock.json", {"portal", "deps"}),
        ("random.txt", set()),
    ],
)
def test_paths_map_to_areas(path, expected):
    assert triage.areas(path) == expected


def test_triage_adds_missing_labels_creates_absent_ones_and_assigns_the_author():
    github = Fake(
        user_pr(assignees=["maintainer"]),
        files=["nora/app/x.py", "tests/test_x.py"],
        existing=["type: fix"],
    )
    result = triage.triage(github, "o/r", 7)
    assert result["added"] == ["area: runtime", "area: tests", "type: fix"]
    posted = [c for c in github.calls if c[:2] == ("POST", "/repos/o/r/issues/7/labels")]
    assert posted and posted[0][2]["labels"] == result["added"]
    created = [c[2]["name"] for c in github.calls if c[:2] == ("POST", "/repos/o/r/labels")]
    assert created == ["area: runtime", "area: tests"], "only the labels that did not exist"
    assert ("POST", "/repos/o/r/issues/7/assignees", {"assignees": ["owner"]}) in github.calls
    assert not any(c[0] in {"DELETE", "PATCH", "PUT"} for c in github.calls)


def test_stale_managed_labels_are_removed_and_foreign_labels_are_left_alone():
    item = user_pr(
        title="docs: a thing",
        labels=["type: fix", "area: runtime", "dependencies", "autorelease: pending", "breaking"],
        assignees=["owner"],
    )
    github = Fake(item, files=["docs/a.md"], existing=["type: docs", "area: docs"])
    result = triage.triage(github, "o/r", 7)
    assert result["removed"] == ["area: runtime", "breaking", "type: fix"]
    deleted = {c[1].rsplit("/", 1)[1] for c in github.calls if c[0] == "DELETE"}
    assert deleted == {"area%3A%20runtime", "breaking", "type%3A%20fix"}
    assert not any(c[0] == "POST" and c[1].endswith("/assignees") for c in github.calls), (
        "already assigned"
    )


@pytest.mark.parametrize("login", sorted(triage.SELF_MANAGED_AUTHORS))
def test_self_managed_authors_are_left_to_their_own_configuration(login):
    github = Fake(user_pr(kind="Bot", login=login))
    assert triage.triage(github, "o/r", 7) == {"skipped": "author manages its own labels"}
    assert [c[0] for c in github.calls] == ["GET"], "no write for a bot author"


def test_unsupported_author_type_never_writes():
    github = Fake(user_pr(kind="Organization"))
    assert triage.triage(github, "o/r", 7) == {"skipped": "unsupported author type"}
    assert [c[0] for c in github.calls] == ["GET"]


@pytest.mark.parametrize("login", ["devin-ai-integration[bot]", "future-agent[bot]"])
@pytest.mark.parametrize("assignees", [(), ("maintainer",)])
def test_bots_share_classification_without_assignment_or_registration(login, assignees):
    github = Fake(
        user_pr(
            title="security(portal): protect a request",
            labels=["type: fix", "area: docs", "review: pending"],
            assignees=assignees,
            kind="Bot",
            login=login,
        ),
        files=["app/src/a.ts", "nora/app/x.py", "tests/unit/test_x.py"],
    )
    result = triage.triage(github, "o/r", 7)
    expected = ["area: portal", "area: runtime", "area: tests", "type: security"]
    assert result == {
        "added": expected,
        "removed": ["area: docs", "type: fix"],
        "assignee": None,
    }
    assert ("POST", "/repos/o/r/issues/7/labels", {"labels": expected}) in github.calls
    assert {c[1] for c in github.calls if c[0] == "DELETE"} == {
        "/repos/o/r/issues/7/labels/area%3A%20docs",
        "/repos/o/r/issues/7/labels/type%3A%20fix",
    }
    writes = [c for c in github.calls if c[0] != "GET"]
    assert all("/labels" in c[1] for c in writes), "only label writes, never assignee or PR edits"


def test_configuring_an_author_as_self_managed_needs_no_classifier_change(monkeypatch):
    monkeypatch.setattr(triage, "SELF_MANAGED_AUTHORS", {"future-agent[bot]"})
    github = Fake(user_pr(kind="Bot", login="future-agent[bot]"))
    assert triage.triage(github, "o/r", 7) == {"skipped": "author manages its own labels"}
    assert [c[0] for c in github.calls] == ["GET"]


def test_an_author_who_cannot_be_assigned_is_not_an_error():
    class Unassignable(Fake):
        def __call__(self, method, path, body=None):
            if method == "POST" and path.endswith("/assignees"):
                raise urllib.error.HTTPError(path, 422, "Unprocessable", {}, None)
            return super().__call__(method, path, body)

    result = triage.triage(
        Unassignable(user_pr(), existing=["type: fix", "area: runtime"]), "o/r", 7
    )
    assert result["assignee"] == "owner"


def test_every_declared_visible_commit_type_has_a_label_colour():
    missing = set(TYPES) - set(triage.TYPE_COLORS) - {"merge"}
    assert not missing, f"types without a label: {sorted(missing)}"


def test_the_workflow_runs_trusted_code_and_never_interpolates_pr_data():
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/pr-triage.yml").read_text())
    for step in workflow["jobs"]["triage"]["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["ref"] == "master" and step["with"]["persist-credentials"] is False
        assert "${{" not in step.get("run", "")
    assert workflow["jobs"]["triage"]["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }


def test_dependabot_assigns_the_maintainer_and_uses_only_labels_the_repo_defines():
    """Dependabot silently drops a label that does not exist, and the triage job cannot
    label its PRs, so dependabot.yml is the only place their labels come from."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / ".github/dependabot.yml").read_text())
    defined = set(triage.all_labels()) | {"dependencies"}
    for update in config["updates"]:
        where = update["package-ecosystem"]
        assert update.get("assignees") == ["mikeng-io"], where
        assert set(update["labels"]) <= defined, (where, set(update["labels"]) - defined)


def test_security_workflow_has_no_codeql_job():
    """CodeQL was skipped on every PR while private and showed an unexpanded matrix name."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/security.yml").read_text()
    assert "codeql" not in text.lower()
    assert set(yaml.safe_load(text)["jobs"]) == {"dependency-review"}
