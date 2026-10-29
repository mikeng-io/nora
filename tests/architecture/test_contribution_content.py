"""Actual PR/issue text is validated separately from template-shape contracts."""

from __future__ import annotations

import copy

import pytest

from scripts import check_contribution_content as content

BODY = (
    "## What\nFix a documentation typo.\n\n## Gate on the merged tree\n"
    "make gate: exit 0; 50 passed.\n\n"
    "## Authored by\nagent: Claude in Claude Code; reviewed by Kimi.\n"
)


def pr(**overrides):
    result = {
        "number": 104,
        "title": "docs: clarify setup",
        "body": BODY,
        "head": {"sha": "abc", "ref": "docs/setup", "repo": {"full_name": "owner/repo"}},
        "user": {"login": "contributor", "type": "User"},
        "labels": [],
    }
    result.update(overrides)
    return result


def test_valid_human_pr():
    assert content.pr_problems(pr(), "owner/repo") == []


@pytest.mark.parametrize(
    "title,body",
    [
        ("Update docs", BODY),
        ("wip: later", BODY),
        ("docs: clarify setup", "## What\nA real change.\n"),
        (
            "docs: clarify setup",
            "## What\n<!-- instructions -->\n## Gate on the merged tree\nPASS\n",
        ),
        ("docs: clarify setup", BODY.replace("50 passed.", "\nPENDING_FINAL_GATE")),
        ("docs: clarify setup", BODY.replace("Fix a documentation typo.", "<what changed>")),
        ("docs: clarify setup", BODY + "\n## Summary\nAn undeclared section.\n"),
        ("docs: clarify setup", BODY + "\n## What\nA duplicated section.\n"),
    ],
)
def test_incomplete_or_placeholder_pr_fails(title, body):
    assert content.pr_problems(pr(title=title, body=body), "owner/repo")


@pytest.mark.parametrize(
    "branch,ok",
    [
        ("fix/message-delivery", True),
        ("security/aikido-high-triage", True),
        ("docs/openwiki-update", True),
        ("mikeng-io/aikido-high-triage", False),  # owner-named, the form an agent used
        ("fix/Message_Delivery", False),
        ("fix/", False),
        ("fix", False),
        ("wip/thing", False),
        ("fix/a/b", False),
        ("ordinary-branch", False),
    ],
)
def test_branch_name_follows_the_declared_type_slug_convention(branch, ok):
    item = pr()
    item["head"]["ref"] = branch
    assert (content.pr_problems(item, "owner/repo") == []) is ok


def test_code_examples_do_not_invent_sections_or_placeholders():
    body = BODY + "\n```md\n## Summary\nTBD\n```\n<https://example.com>\n"
    assert not content.pr_problems(pr(body=body), "owner/repo")


def test_heading_order_is_checked():
    body = "## Gate on the merged tree\n50 passed.\n## What\nFix a typo.\n"
    assert content.pr_problems(pr(body=body), "owner/repo")


def release_pr():
    item = pr(
        title="chore(master): release 6.2.0",
        body=(
            ":robot: I have created a release *beep* *boop*\n---\n\n"
            "## [6.2.0](https://example.com/compare/v6.1.0...v6.2.0)\n### Features\n* Change.\n"
        ),
    )
    item["user"] = {"login": "github-actions[bot]", "type": "Bot"}
    item["head"]["ref"] = "release-please--branches--master--components--nora"
    item["labels"] = [{"name": "autorelease: pending"}]
    item["base"] = {"sha": "a" * 40, "ref": "master"}
    item["body"] += "\nRelease-Candidate: " + "a" * 40 + "\n"
    return item


def test_release_format_is_supported_but_cannot_be_claimed_by_a_fork_or_human():
    item = release_pr()
    assert not content.pr_problems(item, "owner/repo")
    for field, value in [
        ("user", {"login": "contributor", "type": "User"}),
        ("head", {**item["head"], "repo": {"full_name": "attacker/fork"}}),
    ]:
        altered = copy.deepcopy(item)
        altered[field] = value
        assert content.pr_problems(altered, "owner/repo")


def test_custom_release_app_requires_verified_identity():
    item = release_pr()
    item["user"]["login"] = "nora-release[bot]"
    assert content.pr_problems(item, "owner/repo")
    assert not content.pr_problems(item, "owner/repo", release_login="nora-release[bot]")


def test_release_format_still_requires_the_expected_body():
    item = release_pr()
    item["body"] = "TBD"
    assert content.pr_problems(item, "owner/repo")


def test_dependabot_format_uses_the_same_trust_boundary():
    item = pr(title="chore(deps): bump a dependency", body="Bumps a dependency from 1 to 2.")
    item["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    item["head"]["ref"] = "dependabot/npm_and_yarn/package-2"
    assert not content.pr_problems(item, "owner/repo")
    item["head"]["repo"]["full_name"] = "attacker/fork"
    assert content.pr_problems(item, "owner/repo")


def issue():
    return {
        "title": "Memory: recall loses an anchor",
        "labels": [{"name": "freeform"}],
        "body": "\n".join(f"### {name}\nA concrete explanation.\n" for name in content.FREEFORM),
    }


@pytest.mark.parametrize(
    "label,headings",
    [
        (
            "bug",
            [
                "Description",
                "Steps to reproduce",
                "Expected behavior",
                "Actual behavior",
                "Version and environment",
            ],
        ),
        ("enhancement", ["Problem", "Proposed solution"]),
    ],
)
def test_public_issue_forms_accept_reports_without_internal_design_fields(label, headings):
    item = {
        "title": "A clear report from a new contributor",
        "labels": [{"name": label}],
        "body": "\n".join(f"### {name}\nConcrete reproduction details.\n" for name in headings),
    }
    assert content.issue_problems(item) == []
    item["body"] = "\n".join(
        f"### {name}\nConcrete reproduction details.\n" for name in headings[1:]
    )
    assert content.issue_problems(item)
    item["labels"].append({"name": "freeform"})
    assert content.issue_problems(item)


def test_bug_reports_accept_code_and_logs_without_treating_fenced_headings_as_fields():
    item = {
        "title": "Sidecar cannot connect",
        "labels": [{"name": "bug"}],
        "body": "### Description\nThe sidecar exits immediately.\n"
        "### Steps to reproduce\n```sh\nnode src/service.mjs\n```\n"
        "### Expected behavior\nThe service remains available.\n"
        "### Actual behavior\n```text\nError: connect ECONNREFUSED 127.0.0.1:4600\n```\n"
        "### Version and environment\nNora 6.2.0, Linux, Node 22.\n",
    }
    assert content.issue_problems(item) == []
    item["body"] = (
        item["body"]
        .replace("### Expected behavior\nThe service remains available.\n", "")
        .replace("node src/service.mjs", "node src/service.mjs\n### Expected behavior\nSpoofed")
    )
    assert any("Expected behavior" in error for error in content.issue_problems(item))


def test_issue_web_and_cli_headings_are_supported():
    item = issue()
    assert not content.issue_problems(item)
    item["body"] = item["body"].replace("### ", "## ")
    assert not content.issue_problems(item)


def test_incomplete_issue_is_reported_without_closing_it():
    item = issue()
    item["body"] = "## What\n_No response_\n"
    assert content.issue_problems(item)


def test_uncertainty_is_a_valid_issue_answer():
    item = issue()
    item["body"] = item["body"].replace(
        "### Decisions to record\nA concrete explanation.", "### Decisions to record\n#TBD"
    )
    assert not content.issue_problems(item)


def test_design_issue_accepts_both_form_fields_and_the_documented_prose_anchor():
    item = {
        "title": "Memory: build the specified recall",
        "labels": [{"name": "design-anchored"}],
        "body": "**Design:** design/recall.md §2 (recall behavior).\n"
        + "\n".join(f"## {name}\nA concrete explanation.\n" for name in content.DESIGN),
    }
    assert not content.issue_problems(item)
    item["body"] = item["body"].replace(
        "**Design:** design/recall.md §2 (recall behavior).",
        "### Design anchor\ndesign/recall.md §2\n### What it covers\nRecall behavior.",
    )
    assert not content.issue_problems(item)


def commit(message, parents=1, sha="abcdef1234"):
    return {"sha": sha, "commit": {"message": message}, "parents": [{}] * parents}


GOOD_COMMITS = [commit("docs: clarify setup\n")]


class GitHub:
    """Only the real endpoints this flow may call exist; wrong calls fail."""

    def __init__(self, item):
        self.item = item
        self.calls = []
        self.comments = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/repos/owner/repo/pulls/104"):
            return self.item
        if method == "GET" and path == "/repos/owner/repo/pulls/104/commits?per_page=100&page=1":
            return getattr(self, "commits", GOOD_COMMITS)
        if method == "POST" and path == "/repos/owner/repo/check-runs":
            assert body["head_sha"] == self.item["head"]["sha"]
            return {"id": 7}
        if method == "PATCH" and path == "/repos/owner/repo/check-runs/7":
            return body
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_result_attaches_to_current_api_head_not_stale_event_head():
    item = pr()
    item["head"]["sha"] = "new-head"
    github = GitHub(item)
    assert content.check_pr(github, "owner/repo", 104) == 0
    assert github.calls[-1][2]["conclusion"] == "success"
    assert github.calls[1][2]["name"] == "PR content"


def test_invalid_live_body_fails_the_published_check():
    github = GitHub(pr(body="TBD"))
    assert content.check_pr(github, "owner/repo", 104) == 1
    assert github.calls[-1][2]["conclusion"] == "failure"


def test_inline_title_and_body_prompts_are_unfilled_but_quoted_examples_are_not():
    assert content.pr_problems(pr(title="docs: <what changed>"), "owner/repo")
    assert content.pr_problems(pr(title="docs: TBD"), "owner/repo")
    assert content.pr_problems(pr(body=BODY + "Description: <describe the change>"), "owner/repo")
    assert not content.pr_problems(pr(body=BODY + "The example is `<what changed>`."), "owner/repo")


def test_a_shorter_backtick_fence_cannot_forge_a_rendered_gate_section():
    body = "## What\nFix a typo.\n````md\n```\n## Gate on the merged tree\n50 passed.\n````"
    assert content.pr_problems(pr(body=body), "owner/repo")


class IssueGitHub:
    def __init__(self):
        self.item = issue()
        self.item["body"] = "TBD"
        self.comments = [
            {
                "id": 1,
                "body": content.MARKER + "user text",
                "user": {"login": "contributor", "type": "User"},
            }
        ]
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("POST", "/repos/owner/repo/issues/103/labels"):
            assert body == {"labels": [content.NEEDS_INFO]}
            self.item["labels"].append({"name": content.NEEDS_INFO})
            return copy.deepcopy(self.item["labels"])
        if (method, path) == ("DELETE", "/repos/owner/repo/issues/103/labels/needs-info"):
            assert any(label["name"] == content.NEEDS_INFO for label in self.item["labels"])
            self.item["labels"] = [
                label for label in self.item["labels"] if label["name"] != content.NEEDS_INFO
            ]
            return None
        if (method, path) == ("GET", "/repos/owner/repo/issues/103"):
            return self.item
        if method == "GET" and path.endswith("/comments?per_page=100&page=1"):
            return copy.deepcopy(self.comments)
        if (method, path) == ("POST", "/repos/owner/repo/issues/103/comments"):
            self.comments.append(
                {
                    "id": 2,
                    "body": body["body"],
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                }
            )
            return self.comments[-1]
        if (method, path) == ("PATCH", "/repos/owner/repo/issues/comments/2"):
            self.comments[-1]["body"] = body["body"]
            return self.comments[-1]
        if (method, path) == ("DELETE", "/repos/owner/repo/issues/comments/2"):
            self.comments = [x for x in self.comments if x["id"] != 2]
            return None
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_issue_feedback_updates_once_and_clears_only_its_own_comment_after_correction():
    github = IssueGitHub()
    github.item["labels"].append({"name": "unrelated"})
    assert content.check_issue(github, "owner/repo", 103) == 0
    assert len(github.comments) == 2
    assert {label["name"] for label in github.item["labels"]} == {
        "freeform",
        "unrelated",
        "needs-info",
    }
    assert content.check_issue(github, "owner/repo", 103) == 0
    assert len(github.comments) == 2
    github.item["body"] = "## What\nAn actual problem.\n"
    content.check_issue(github, "owner/repo", 103)
    assert sum(call[0] == "PATCH" for call in github.calls) == 1
    github.item["body"] = issue()["body"]
    assert content.check_issue(github, "owner/repo", 103) == 0
    assert [x["id"] for x in github.comments] == [1]
    assert {label["name"] for label in github.item["labels"]} == {"freeform", "unrelated"}
    label_calls = [call for call in github.calls if "/labels" in call[1]]
    assert [call[0] for call in label_calls] == ["POST", "DELETE"]


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "renamed",
        "unconfigured",
        "wrong-id",
        "missing-id",
        "human",
        "fork",
        "branch",
        "label",
        "body",
    ],
)
def test_private_release_bot_uses_the_pinned_user_identity(monkeypatch, case):
    item = release_pr()
    item["user"] = {"login": "release-app[bot]", "type": "Bot", "id": 456}
    monkeypatch.setenv("RELEASE_APP_BOT_USER_ID", "456")
    monkeypatch.delenv("RELEASE_APP_ID", raising=False)
    if case == "renamed":
        item["user"]["login"] = "renamed-app[bot]"
    elif case == "unconfigured":
        monkeypatch.delenv("RELEASE_APP_BOT_USER_ID")
    elif case == "wrong-id":
        item["user"]["id"] = 789
    elif case == "missing-id":
        del item["user"]["id"]
    elif case == "human":
        item["user"]["type"] = "User"
    elif case == "fork":
        item["head"]["repo"]["full_name"] = "attacker/fork"
    elif case == "branch":
        item["head"]["ref"] = "ordinary-branch"
    elif case == "label":
        item["labels"] = []
    elif case == "body":
        item["body"] = "TBD"
    github = GitHub(item)
    github.commits = [
        {
            "sha": item["head"]["sha"],
            "parents": [{"sha": "a" * 40}],
            "commit": {"message": item["title"]},
        }
    ]
    valid = case in {"valid", "renamed"}
    assert content.check_pr(github, "owner/repo", 104) == (0 if valid else 1)
    assert github.calls[-1][2]["conclusion"] == ("success" if valid else "failure")


def test_issue_field_constants_follow_the_existing_form_contract():
    import yaml

    from scripts.triage_issue import named_areas

    for filename, expected in [
        ("freeform-gap.yml", content.FREEFORM),
        ("design-anchored.yml", ["Design anchor", "What it covers", *content.DESIGN]),
    ]:
        form = yaml.safe_load((content.ROOT / ".github/ISSUE_TEMPLATE" / filename).read_text())
        actual = [
            field["attributes"]["label"]
            for field in form["body"]
            if field["type"] in {"input", "textarea"}
        ]
        assert actual == expected
        options = next(field for field in form["body"] if field.get("id") == "area")["attributes"][
            "options"
        ]
        for option in options:
            assert bool(named_areas(option)) == (not option.startswith("other ("))


def test_privileged_metadata_jobs_use_only_default_branch_code():
    import yaml

    workflow = yaml.safe_load(
        (content.ROOT / ".github/workflows/contribution-content.yml").read_text()
    )
    # A run cancelled by a newer event leaves a cancelled `pr-metadata` check on the same
    # commit, and the merge rules treat that as unsatisfied even when a newer run is green.
    assert "concurrency" not in workflow
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["ref"] == "master"
                assert step["with"]["persist-credentials"] is False
            assert "${{" not in step.get("run", "")
    assert workflow["jobs"]["pr-metadata"]["permissions"] == {
        "contents": "read",
        "checks": "write",
        "pull-requests": "read",
    }
    metadata = workflow["jobs"]["pr-metadata"]["steps"][1]
    assert metadata["env"]["RELEASE_APP_BOT_USER_ID"] == "${{ vars.RELEASE_APP_BOT_USER_ID }}"
    assert "RELEASE_APP_ID" not in metadata["env"]
    assert workflow["jobs"]["issue-feedback"]["permissions"] == {
        "contents": "read",
        "issues": "write",
    }
    area_steps = [
        step
        for step in workflow["jobs"]["issue-feedback"]["steps"]
        if step.get("run") == "python3 -m scripts.triage_issue"
    ]
    assert len(area_steps) == 1
    assert area_steps[0]["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105 -- workflow expression


def test_unclosed_html_comment_cannot_hide_required_sections():
    assert content.pr_problems(pr(body="<!-- unfinished comment\n" + BODY), "owner/repo")


@pytest.mark.parametrize(
    "placeholder",
    [
        "- TODO",
        "**TODO**",
        "`PENDING_FINAL_GATE`",
        "- PENDING_FINAL_GATE",
        "- [ ] **TBD**",
        "### TODO",
    ],
)
def test_markdown_wrappers_do_not_turn_placeholders_into_gate_results(placeholder):
    body = "## What\nFix a typo.\n## Gate on the merged tree\n" + placeholder
    assert content.pr_problems(pr(body=body), "owner/repo")


def test_metadata_read_failure_replaces_old_success_on_event_head():
    calls = []

    def github(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            raise RuntimeError("metadata unavailable")
        assert method == "POST" and path == "/repos/owner/repo/check-runs"
        return {"id": 7}

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        content.check_pr(github, "owner/repo", 104, event_head="event-head")
    assert calls[-1][2]["head_sha"] == "event-head"
    assert calls[-1][2]["conclusion"] == "failure"


def test_authored_by_is_required_for_a_human_pr():
    body = BODY.split("## Authored by")[0]
    assert any("Authored by" in e for e in content.pr_problems(pr(body=body), "owner/repo"))


def test_every_commit_of_a_pr_is_judged_by_the_hooks_own_rules():
    """`--no-verify` skips the local hooks; the PR check does not."""
    bad = [commit("fix: do a thing\n", sha="1111111"), commit("did stuff\n", sha="2222222")]
    problems = content.pr_problems(pr(), "owner/repo", commits=bad)
    assert any(p.startswith("Commit 1111111") and "body" in p for p in problems)
    assert any(p.startswith("Commit 2222222") and "type" in p for p in problems)
    assert content.pr_problems(pr(), "owner/repo", commits=GOOD_COMMITS) == []


def test_githubs_update_branch_merge_is_not_held_to_the_authored_merge_rule():
    update = commit("Merge branch 'master' into docs/setup\n", parents=2)
    assert content.pr_problems(pr(), "owner/repo", commits=[update]) == []
    plain = commit("Merge branch 'master' into docs/setup\n", parents=1)
    assert content.pr_problems(pr(), "owner/repo", commits=[plain])


def test_commit_errors_are_capped_so_a_long_branch_does_not_flood_the_summary():
    many = [commit("did stuff\n", sha=f"{n:07d}") for n in range(12)]
    errors = content.commit_problems(many)
    assert len(errors) == content.MAX_COMMIT_ERRORS + 1 and errors[-1].startswith("...and 7")


def test_a_bad_commit_fails_the_published_check():
    github = GitHub(pr())
    github.commits = [commit("fix: do a thing\n")]
    assert content.check_pr(github, "owner/repo", 104) == 1
    assert github.calls[-1][2]["conclusion"] == "failure"


@pytest.mark.parametrize("label", ["autorelease: pending", "autorelease: tagged"])
def test_a_release_pr_stays_a_release_pr_after_release_please_tags_it(label):
    """Tagging swaps the label and that re-runs the check on the merged PR; it must not
    be judged as a human PR and fail."""
    item = release_pr()
    item["labels"] = [{"name": label}]
    assert content.pr_problems(item, "owner/repo", release_login="github-actions[bot]") == []


def test_a_human_cannot_claim_the_release_shape_with_the_tagged_label():
    item = release_pr()
    item["labels"] = [{"name": "autorelease: tagged"}]
    item["user"] = {"login": "contributor", "type": "User"}
    assert content.pr_problems(item, "owner/repo")


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "base-moved",
        "parent-moved",
        "update-branch",
        "head-moved",
        "source-missing",
        "source-duplicate",
        "subject",
    ],
)
def test_generated_metadata_requires_single_commit_on_recorded_current_source(damage):
    item = release_pr()
    commits = [
        {
            "sha": item["head"]["sha"],
            "parents": [{"sha": "a" * 40}],
            "commit": {"message": item["title"]},
        }
    ]
    if damage == "base-moved":
        item["base"]["sha"] = "b" * 40
    elif damage == "parent-moved":
        commits[0]["parents"] = [{"sha": "b" * 40}]
    elif damage == "update-branch":
        commits.append(
            {
                "sha": "updated",
                "parents": [{"sha": "a" * 40}, {"sha": "b" * 40}],
                "commit": {"message": "Merge branch 'master' into release"},
            }
        )
    elif damage == "head-moved":
        item["head"]["sha"] = "changed"
    elif damage == "source-missing":
        item["body"] = item["body"].split("Release-Candidate:")[0]
    elif damage == "source-duplicate":
        item["body"] += "Release-Candidate: " + "a" * 40 + "\n"
    elif damage == "subject":
        commits[0]["commit"]["message"] = "chore: unrelated metadata"
    errors = content.pr_problems(item, "owner/repo", commits=commits)
    assert bool(errors) is (damage != "none")
    if errors:
        assert "Re-prepare" in errors[0]


def test_tagged_label_cannot_disable_source_guard_on_an_open_release_pr():
    item = release_pr()
    item["state"] = "open"
    item["labels"] = [{"name": "autorelease: tagged"}]
    assert content.pr_problems(item, "owner/repo", commits=[])
    item["state"] = "closed"  # Historical publication label updates remain supported.
    assert content.pr_problems(item, "owner/repo", commits=[]) == []


@pytest.mark.parametrize("label", ["freeform", "design-anchored", "bug", "enhancement"])
def test_issue_required_sections_reject_embedded_unfilled_prompts(label):
    item = issue()
    item["labels"] = [{"name": label}]
    headings = content.PUBLIC_ISSUES.get(
        label, content.DESIGN if label == "design-anchored" else content.FREEFORM
    )
    item["body"] = "\n".join(f"## {name}\nConcrete evidence.\n" for name in headings)
    if label == "design-anchored":
        item["body"] = "**Design:** design/recall.md §2\n" + item["body"]
    assert content.issue_problems(item) == []
    item["body"] += "\n- **TODO**\n"
    assert content.issue_problems(item)


@pytest.mark.parametrize("title", ["Memory: TBD", "Memory: <describe the gap>", "Memory: "])
def test_internal_issue_title_requires_a_populated_area_and_gap(title):
    item = issue()
    item["title"] = title
    assert content.issue_problems(item)
