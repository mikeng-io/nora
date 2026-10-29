"""Explicit issue metadata selects labels; the fake rejects every other write."""

from __future__ import annotations

import copy
import urllib.parse

import pytest

from scripts import triage_issue as triage


@pytest.mark.parametrize(
    "title,body,expected",
    [
        ("Deployment: missing onboarding", "", {"deploy"}),
        ("Dependencies: blocked upgrade", "", {"deps"}),
        ("Note tier: discharge pipeline", "", {"runtime"}),
        ("Tests: an unstable assertion", "", {"tests"}),
        ("A descriptive public report", "### Area\nportal (nora-app web)\n", {"portal"}),
        ("Deployment: stale image", "## Area\nrelease-and-version (build / deploy)\n", {"deploy"}),
        ("Tests: unstable scanner", "## Area\nrepo-hygiene / tests\n", {"ci", "tests"}),
        ("Portal: failed rendering", "### Area\ndeployment\n", set()),
        ("Tests: unstable scanner", "## Area\nrepo-hygiene\n", set()),
        ("Portal: failed rendering", "### Area\nother\n", set()),
        ("Deployment: stale image", "## Area\nportal\n## Area\ndeployment\n", set()),
        ("Please help", "The body mentions deployment, dependencies and tests.", set()),
        ("Please help", "```\n### Area\nportal\n```\n", set()),
        ("Please help", "<!--\n## Area\ndeployment\n-->\n", set()),
        ("Demo: a showcase", "", set()),
    ],
)
def test_only_explicit_unambiguous_area_metadata_classifies(title, body, expected):
    assert triage.issue_areas(title, body) == expected


class IssueGitHub:
    def __init__(self):
        self.item = {
            "title": "Deployment: missing onboarding",
            "body": "",
            "labels": [
                {"name": name}
                for name in (
                    "freeform",
                    "needs-info",
                    "help wanted",
                    "ready",
                    "type: docs",
                    "area: custom",
                )
            ],
        }
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/repos/o/r/issues/7"):
            return copy.deepcopy(self.item)
        if method == "GET" and path.startswith("/repos/o/r/labels/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            assert name in {"area: deploy", "area: tests"}
            return {"name": name}
        if (method, path) == ("POST", "/repos/o/r/issues/7/labels"):
            assert set(body) == {"labels"}
            assert set(body["labels"]) <= {"area: deploy", "area: tests"}
            have = {label["name"] for label in self.item["labels"]}
            self.item["labels"].extend(
                {"name": name} for name in body["labels"] if name not in have
            )
            return copy.deepcopy(self.item["labels"])
        if method == "DELETE" and path.startswith("/repos/o/r/issues/7/labels/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            assert name in {"area: deploy", "area: tests"}
            assert name in {label["name"] for label in self.item["labels"]}
            self.item["labels"] = [label for label in self.item["labels"] if label["name"] != name]
            return None
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_edit_replaces_stale_area_and_unknown_withdraws_it_without_touching_other_labels():
    github = IssueGitHub()
    original = copy.deepcopy(github.item["labels"])
    assert triage.triage(github, "o/r", 7) == {
        "areas": ["deploy"],
        "added": ["area: deploy"],
        "removed": [],
    }
    assert triage.triage(github, "o/r", 7)["added"] == []
    github.item["title"] = "Tests: unstable assertion"
    assert triage.triage(github, "o/r", 7) == {
        "areas": ["tests"],
        "added": ["area: tests"],
        "removed": ["area: deploy"],
    }
    github.item["body"] = "## Area\nportal\n"
    assert triage.triage(github, "o/r", 7) == {
        "areas": [],
        "added": [],
        "removed": ["area: tests"],
    }
    assert github.item["labels"] == original
    assert [method for method, _, _ in github.calls if method != "GET"] == [
        "POST",
        "DELETE",
        "POST",
        "DELETE",
    ]


def test_a_pull_request_is_rejected_before_any_label_write():
    github = IssueGitHub()
    github.item["pull_request"] = {}
    with pytest.raises(ValueError, match="pull requests"):
        triage.triage(github, "o/r", 7)
    assert [method for method, _, _ in github.calls] == ["GET"]
