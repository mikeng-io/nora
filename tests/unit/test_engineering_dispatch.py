"""Exercise the authorization/receipt boundary; fake routes reject wrong methods/paths."""

from __future__ import annotations

import copy
import io
import json
import re
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from scripts import engineering_dispatch as dispatch

HEAD = "a" * 40
REPO = dispatch.REPOSITORY
BASE = f"/repos/{REPO}"
ISSUE = f"https://github.com/{REPO}/issues/3"


def admission():
    return {
        "confirmed": True,
        "head": HEAD,
        "issue_updated_at": "2026-01-01T00:00:00Z",
        "project_ref": "https://github.com/users/mikeng-io/projects/8",
        "project_issue_ref": ISSUE,
        "status": "Ready",
        "priority": "P1",
        "execution_tier": "critical",
        "review_tier": "full",
        **{
            key: ISSUE + "#" + key
            for key in (
                "acceptance_ref",
                "scope_ref",
                "prerequisites_ref",
                "ownership_ref",
                "capability_ref",
            )
        },
    }


def event(action="repair"):
    return {
        "repository": {"full_name": REPO},
        "sender": {"login": dispatch.OWNER},
        "inputs": {
            "action": action,
            "number": "7",
            "head": HEAD,
            "check_run_id": "11",
            "admission": json.dumps(admission()),
        },
    }


def environment():
    return {
        "ENGINEERING_REPOSITORY": REPO,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_ACTOR": dispatch.OWNER,
        "GITHUB_RUN_ID": "42",
        "ENGINEERING_CI_ENABLED": "true",
        "DEVIN_SECURITY_PROFILE_ID": "synthetic-profile",
        "ENGINEERING_REVIEW_SELECTOR_ENABLED": "true",
        "DEVIN_API_KEY": "synthetic",
        "DEVIN_ORG_ID": "org-synthetic",
    }


def connected(nodes):
    return {"pageInfo": {"hasNextPage": False}, "nodes": nodes}


class Fake:
    def __init__(self):
        self.calls, self.comments = [], []
        self.permission = "admin"
        self.pr = {
            "state": "open",
            "draft": False,
            "changed_files": 1,
            "user": {"login": dispatch.OWNER, "type": "User"},
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"sha": "b" * 40, "ref": "master", "repo": {"full_name": REPO}},
        }
        self.files = [{"filename": "nora/agent/main.py"}]
        self.issue = {
            "__typename": "Issue",
            "number": 3,
            "state": "OPEN",
            "body": "Synthetic scope",
            "updatedAt": admission()["issue_updated_at"],
            "url": ISSUE,
            "repository": {"nameWithOwner": REPO},
        }
        self.graph_error = False
        self.more = None
        self.check = {
            "id": 11,
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "failure",
            "app": {"id": 15368, "slug": "github-actions"},
            "name": "checks",
            "check_suite": {"id": 13},
            "details_url": f"https://github.com/{REPO}/actions/runs/12/job/11",
        }
        self.run = {
            "path": ".github/workflows/ci.yml",
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "failure",
            "check_suite_id": 13,
            "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO},
        }
        self.session = {
            "session_id": "synthetic-session",
            "org_id": "org-synthetic",
            "url": "https://app.devin.ai/sessions/synthetic-session",
            "status": "new",
            "child_session_ids": [],
        }
        self.timeout = False
        self.race = False
        self.edit_body_on_claim = False
        self.weaken_profile_on_claim = False
        self.profile = {
            "profile_id": "synthetic-profile",
            "org_id": "org-synthetic",
            "updated_at": 1,
            "enforcement_level": "mandatory",
            "devin_mcp_read_only": True,
            "git_access_level": "read_only",
            "remove_github_cli_token": True,
            "mcp_config": {"servers": []},
            "net_policy": {"allow": [{"hostname": "github.com"}, {"hostname": "api.github.com"}]},
        }

    def __call__(self, service, method, path, body=None):
        self.calls.append((service, method, path, copy.deepcopy(body)))
        return copy.deepcopy(self.route(service, method, path, body))

    def route(self, service, method, path, body):
        if service == "github" and (method, path) == ("POST", "/graphql"):
            assert body["variables"] == {"owner": dispatch.OWNER, "repo": "nora", "number": 7}
            assert "closingIssuesReferences" in body["query"]
            assert "projectItems" not in body["query"] and "fieldValues" not in body["query"]
            linked = connected([copy.deepcopy(self.issue)])
            if self.more:
                linked["pageInfo"]["hasNextPage"] = True
            return (
                {"errors": ["synthetic"]}
                if self.graph_error
                else {"data": {"repository": {"pullRequest": {"closingIssuesReferences": linked}}}}
            )
        if service == "devin":
            if (method, path) == (
                "GET",
                "/v3beta1/organizations/org-synthetic/security-profiles/synthetic-profile",
            ):
                return self.profile
            assert path == "/v3/organizations/org-synthetic/sessions" or (
                method == "GET"
                and path == "/v3/organizations/org-synthetic/sessions/synthetic-session"
            )
            assert method in {"POST", "GET"}
            if method == "POST" and self.timeout:
                raise dispatch.UnavailableError("devin response unavailable; no retry")
            return self.session
        assert service == "github"
        if method == "GET":
            if path == f"{BASE}/pulls/7":
                return self.pr
            if path == f"{BASE}/collaborators/{dispatch.OWNER}/permission":
                return {"permission": self.permission}
            if path == f"{BASE}/pulls/7/files?per_page=100&page=1":
                return self.files
            if path == f"{BASE}/check-runs/11":
                return self.check
            if (
                path == f"{BASE}/commits/{HEAD}/check-runs?filter=latest"
                "&check_name=checks&per_page=100&page=1"
            ):
                return {"total_count": 1, "check_runs": [self.check]}
            if path == f"{BASE}/actions/runs/12":
                return self.run
            if path == "/users/github-actions%5Bbot%5D":
                return {"id": 2, "login": "github-actions[bot]"}
            if path == f"{BASE}/actions/runs/42":
                return {
                    "path": dispatch.WORKFLOW,
                    "event": "workflow_dispatch",
                    "head_branch": "master",
                    "actor": {"login": dispatch.OWNER},
                }
            if match := re.fullmatch(
                re.escape(BASE) + r"/issues/comments\?per_page=100&page=(\d+)", path
            ):
                start = (int(match[1]) - 1) * 100
                return self.comments[start : start + 100]
        if (method, path) == ("POST", f"{BASE}/issues/3/comments"):
            comment = {
                "id": 100 + len(self.comments),
                "body": body["body"],
                "created_at": datetime.now(UTC).isoformat(),
                "user": {"id": 2, "login": "github-actions[bot]"},
            }
            self.comments.append(comment)
            self.issue["updatedAt"] = "2026-01-01T00:01:00Z"
            if self.edit_body_on_claim:
                self.issue["body"] = "Different scope"
            if self.weaken_profile_on_claim:
                self.profile["devin_mcp_read_only"] = False
            if self.race:
                self.pr["head"]["sha"] = "c" * 40
            return comment
        if method == "PATCH" and path.startswith(f"{BASE}/issues/comments/"):
            comment = next(c for c in self.comments if str(c["id"]) == path.rsplit("/", 1)[1])
            comment.update(body)
            return comment
        raise AssertionError(f"wrong API call: {service} {method} {path}")

    def paid(self):
        return [c for c in self.calls if c[:2] == ("devin", "POST")]

    def seed(self, *, state="resolved", head="d" * 40, age=0):
        receipt = {
            "repository": REPO,
            "action": "repair",
            "pr": 7,
            "head": head,
            "state": state,
            "controller_run": "42",
            "session_id": "synthetic-session",
        }
        self.comments.append(
            {
                "id": 100 + len(self.comments),
                "user": {"id": 2, "login": "github-actions[bot]"},
                "created_at": (datetime.now(UTC) - timedelta(hours=age)).isoformat(),
                "body": dispatch.MARKER + json.dumps(receipt) + "\n-->",
            }
        )


def execute(fake, request=None, env=None):
    return dispatch.Controller(fake, env or environment()).run(
        "workflow_dispatch", request or event()
    )


def current_request(api, action="repair"):
    request, data = event(action), admission()
    data["issue_updated_at"] = api.issue["updatedAt"]
    request["inputs"]["admission"] = json.dumps(data)
    return request


@pytest.mark.parametrize("action", ["investigate", "repair"])
def test_admitted_ci_claim_precedes_one_capped_official_session_and_records_real_run(action):
    api = Fake()
    result = execute(api, event(action))
    assert result["state"] == "ok" and result["run_ref"] == api.session["url"]
    (call,) = api.paid()
    assert call[2] == "/v3/organizations/org-synthetic/sessions"
    assert call[3]["max_acu_limit"] == 1 and call[3]["devin_mode"] == "normal"
    assert call[3]["bypass_approval"] is False and call[3]["resumable"] is False
    assert call[3]["secret_ids"] == []
    assert call[3]["security_profile"] == {"profile_id": "synthetic-profile"}
    assert "Synthetic scope" in call[3]["prompt"] and "nora/agent/main.py" in call[3]["prompt"]
    profile_reads = [c for c in api.calls if "/security-profiles/" in c[2]]
    assert len(profile_reads) == 2
    assert "child" not in set(call[3]) and "Do not spawn child sessions" in call[3]["prompt"]
    assert ("Investigate only; do not edit or push" in call[3]["prompt"]) == (
        action == "investigate"
    )
    claim = next(c for c in api.calls if c[1:3] == ("POST", f"{BASE}/issues/3/comments"))
    assert '"state": "pending"' in claim[3]["body"]
    assert api.calls.index(claim) < api.calls.index(call)
    assert api.calls.index(profile_reads[0]) < api.calls.index(claim)
    assert api.calls[api.calls.index(call) - 1] == profile_reads[-1]
    assert '"state": "running"' in api.comments[0]["body"]
    with pytest.raises(dispatch.UnavailableError, match="duplicate"):
        execute(api, current_request(api, action))
    assert len(api.paid()) == 1


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("pr", "state", "closed"),
        ("pr", "draft", True),
        ("pr", "changed_files", 2),
        ("issue", "state", "CLOSED"),
        ("issue", "__typename", "PullRequest"),
        ("issue", "body", ""),
        ("issue", "updatedAt", "changed"),
        ("check", "head_sha", "e" * 40),
        ("check", "status", "in_progress"),
        ("check", "conclusion", "timed_out"),
        ("check", "details_url", "https://example.invalid/"),
        ("run", "path", ".github/workflows/other.yml"),
        ("run", "head_sha", "e" * 40),
    ],
)
def test_missing_unknown_stale_or_owned_work_never_spends(target, key, value):
    api = Fake()
    getattr(api, target)[key] = value
    with pytest.raises(dispatch.UnavailableError):
        execute(api)
    assert not api.paid()


@pytest.mark.parametrize(
    "change", ["fork", "author", "permission", "foreign_issue", "app", "graph_error"]
)
def test_foreign_untrusted_or_failed_repository_evidence_never_spends(change):
    api = Fake()
    if change == "fork":
        api.pr["head"]["repo"]["full_name"] = "example/nora-synthetic"
    elif change == "author":
        api.pr["user"]["login"] = "untrusted"
    elif change == "permission":
        api.permission = "read"
    elif change == "foreign_issue":
        api.issue["repository"]["nameWithOwner"] = "example/nora-synthetic"
    elif change == "app":
        api.check["app"]["id"] = 1
    else:
        api.graph_error = True
    with pytest.raises(dispatch.UnavailableError):
        execute(api)
    assert not api.paid()


def test_truncated_linked_issue_read_cannot_authorize():
    api = Fake()
    api.more = True
    with pytest.raises(dispatch.UnavailableError, match="incomplete"):
        execute(api)
    assert not api.paid()


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTOR", "untrusted"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("ENGINEERING_REPOSITORY", "example/nora-synthetic"),
        ("ENGINEERING_CI_ENABLED", ""),
        ("DEVIN_API_KEY", ""),
        ("DEVIN_SECURITY_PROFILE_ID", ""),
    ],
)
def test_manual_request_does_not_bypass_permissions_flags_credentials_or_confinement(key, value):
    api, env = Fake(), environment()
    env[key] = value
    with pytest.raises(dispatch.UnavailableError):
        execute(api, env=env)
    assert not api.paid()


@pytest.mark.parametrize(
    "key,value",
    [
        ("confirmed", False),
        ("head", "f" * 40),
        ("project_ref", "https://github.com/users/mikeng-io/projects/9"),
        ("project_ref", None),
        ("project_issue_ref", ISSUE + "0"),
        ("status", "Running"),
        ("status", "Inbox"),
        ("status", "Review"),
        ("priority", None),
        ("execution_tier", "unknown"),
        ("review_tier", "unknown"),
        ("scope_ref", ""),
        ("prerequisites_ref", ""),
        ("acceptance_ref", "https://example.invalid/"),
        ("ownership_ref", ""),
        ("capability_ref", ""),
    ],
)
def test_owner_admission_requires_bound_scheduling_scope_prerequisites_and_acceptance(key, value):
    api, request, data = Fake(), event(), admission()
    data[key] = value
    request["inputs"]["admission"] = json.dumps(data)
    with pytest.raises(dispatch.UnavailableError):
        execute(api, request)
    assert not api.paid()


@pytest.mark.parametrize(
    "paths,exempt",
    [
        (["docs/guide.md", "design/plan.md"], True),
        (["README.md"], True),
        *[
            ([path], True)
            for path in (
                ".mcp.json",
                ".cursor/mcp.json",
                ".vscode/mcp.json",
                ".codex/config.toml",
                ".claude/.mcp.json",
                ".claude/mcp.json",
            )
        ],
        (["nora/skills/a/SKILL.md"], False),
        (["nora/doctrine/voice.md"], False),
        (["docs/guide.md", "nora/main.py"], False),
        (["tests/test_x.md"], False),
        ([".github/workflows/ci.yml"], False),
        ([".claude/settings.json"], False),
        ([".codex/hooks.py"], False),
        (["SECURITY.md"], False),
        (["docs/security.md"], False),
        (["AGENTS.md"], False),
        (["unknown.md", "unknown.toml"], False),
    ],
)
def test_selector_excludes_only_narrow_authored_paths_not_semantic_tiers(paths, exempt):
    api = Fake()
    api.files = [{"filename": path} for path in paths]
    api.pr["changed_files"] = len(paths)
    result = execute(api, event("review"))
    assert (result["state"] == "nothing_found") == exempt
    if not exempt:
        assert result["candidate"]["head"] == HEAD
    assert not api.paid()


def test_rename_out_of_runtime_still_requires_review_and_never_triggers_manual_api():
    api = Fake()
    api.files = [{"filename": "docs/a.md", "previous_filename": "nora/skills/a.md"}]
    assert execute(api, event("review"))["candidate"]["review_tier"] == "full"
    assert not any(c[0] == "devin" for c in api.calls)
    env = environment()
    del env["ENGINEERING_REVIEW_SELECTOR_ENABLED"]
    with pytest.raises(dispatch.UnavailableError, match="selector disabled"):
        execute(api, event("review"), env)
    assert not api.paid()


def test_review_tier_none_cannot_make_a_candidate_or_spend():
    api, request, data = Fake(), event("review"), admission()
    data["review_tier"] = "none"
    request["inputs"]["admission"] = json.dumps(data)
    with pytest.raises(dispatch.UnavailableError, match="no authoritative"):
        execute(api, request)
    assert not api.paid()


def test_review_status_accepts_owner_scheduling_snapshot_without_reading_private_project():
    api, request, data = Fake(), event("review"), admission()
    data["status"] = "Review"
    request["inputs"]["admission"] = json.dumps(data)
    result = execute(api, request)
    assert result["candidate"]["review_tier"] == "full"
    assert all(service == "github" for service, *_ in api.calls)
    assert not api.paid()


def test_timeout_retains_pending_claim_and_blocks_every_later_launch():
    api = Fake()
    api.timeout = True
    with pytest.raises(dispatch.UnavailableError, match="no retry"):
        execute(api)
    assert '"state": "pending"' in api.comments[0]["body"]
    with pytest.raises(dispatch.UnavailableError, match="duplicate"):
        execute(api, current_request(api))
    with pytest.raises(dispatch.UnavailableError, match="unresolved"):
        execute(api, current_request(api, "investigate"))
    assert len(api.paid()) == 1


def test_exact_head_is_read_again_after_claim_before_spending():
    api = Fake()
    api.race = True
    with pytest.raises(dispatch.UnavailableError, match="stale PR head"):
        execute(api)
    assert api.comments and not api.paid()


def test_issue_body_edit_after_claim_blocks_even_though_receipt_also_changes_timestamp():
    api = Fake()
    api.edit_body_on_claim = True
    with pytest.raises(dispatch.UnavailableError, match="admission changed after claim"):
        execute(api)
    assert api.comments and not api.paid()


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile_id", "different-profile"),
        ("org_id", "org-foreign"),
        ("enforcement_level", "recommended"),
        ("devin_mcp_read_only", False),
        ("git_access_level", "read_write"),
        ("remove_github_cli_token", False),
        ("mcp_config", None),
        ("mcp_config", {"servers": ["github"]}),
        ("net_policy", None),
        ("net_policy", {"allow": []}),
        ("net_policy", {"allow": [{"hostname": "*.github.com"}, {"hostname": "api.github.com"}]}),
        (
            "net_policy",
            {
                "allow": [
                    {"hostname": "github.com"},
                    {"hostname": "api.github.com"},
                    {"hostname": "api.devin.ai"},
                ]
            },
        ),
    ],
)
def test_wrong_missing_or_weaker_profile_never_claims_or_spends(key, value):
    api = Fake()
    api.profile[key] = value
    with pytest.raises(dispatch.UnavailableError, match="security profile"):
        execute(api)
    assert not api.comments and not api.paid()


def test_profile_absence_and_weakening_after_claim_fail_closed():
    api = Fake()
    api.profile = None
    with pytest.raises(dispatch.UnavailableError, match="profile unavailable"):
        execute(api)
    assert not api.comments and not api.paid()
    api = Fake()
    api.weaken_profile_on_claim = True
    with pytest.raises(dispatch.UnavailableError, match="security profile"):
        execute(api)
    assert api.comments and not api.paid()


def test_global_daily_cap_counts_other_heads_and_actions_but_not_forged_receipts():
    api = Fake()
    api.seed()
    api.seed(head="e" * 40)
    with pytest.raises(dispatch.UnavailableError, match="rolling 24h"):
        execute(api)
    assert not api.paid()
    api.comments[1]["user"] = {"id": 99, "login": "untrusted"}
    assert execute(api)["state"] == "ok"


def test_old_resolved_reservation_expires_but_old_unresolved_claim_does_not():
    api = Fake()
    api.seed(age=25, state="pending")
    with pytest.raises(dispatch.UnavailableError, match="unresolved"):
        execute(api)
    api.comments.clear()
    api.seed(age=25)
    assert execute(api)["state"] == "ok"


def test_receipt_scan_pagination_ceiling_fails_closed(monkeypatch):
    api = Fake()
    api.comments = [{"user": {"id": 99, "login": "untrusted"}, "body": "untrusted"}] * 100
    monkeypatch.setattr(dispatch, "MAX_PAGES", 1)
    with pytest.raises(dispatch.UnavailableError, match="pagination ceiling"):
        execute(api)
    assert not api.paid()


def test_current_terminal_session_readback_releases_lock_without_claiming_done():
    api = Fake()
    execute(api)
    request = event("reconcile")
    request["inputs"]["admission"] = json.dumps({"receipt_id": 100})
    with pytest.raises(dispatch.UnavailableError, match="still active"):
        execute(api, request)
    api.session["status"] = "exit"
    assert "acceptance remains unverified" in execute(api, request)["reason"]
    assert '"state": "resolved"' in api.comments[0]["body"]
    assert len(api.paid()) == 1


def test_automatic_and_comment_only_events_never_spend():
    api = Fake()
    pr_event = {
        "repository": {"full_name": REPO},
        "action": "synchronize",
        "pull_request": {"number": 7, "head": {"sha": HEAD}},
    }
    controller = dispatch.Controller(api, environment())
    assert controller.run("pull_request_target", pr_event)["state"] == "unavailable"
    with pytest.raises(dispatch.UnavailableError, match="unsupported"):
        controller.run("issue_comment", pr_event)
    ci_event = {
        "repository": {"full_name": REPO},
        "workflow_run": {
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "pull_requests": [{"number": 7}],
            "head_sha": HEAD,
        },
    }
    assert controller.run("workflow_run", ci_event)["state"] == "unavailable"
    assert not api.paid()


def test_transport_fixed_origins_no_auth_redirects_and_sanitized_errors(capsys):
    requests = []

    class RedirectingHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            headers = {"Location": "https://example.invalid/credential-trap"}
            response = urllib.response.addinfourl(
                io.BytesIO(b"secret-error-body"), headers, request.full_url, 302
            )
            response.msg = "Found"
            return response

    api = dispatch.API(
        {
            "GH_TOKEN": "synthetic-token",
            "DEVIN_API_KEY": "synthetic-devin",
            "GITHUB_API_URL": "https://example.invalid",
        }
    )
    api.opener = urllib.request.build_opener(dispatch.NoRedirect(), RedirectingHandler())
    with pytest.raises(dispatch.UnavailableError, match="HTTP 302"):
        api("devin", "POST", "/v3/organizations/org-synthetic/sessions", {"prompt": "synthetic"})
    assert len(requests) == 1 and requests[0].full_url.startswith("https://api.devin.ai/")
    assert requests[0].get_header("Authorization") == "Bearer synthetic-devin"
    with pytest.raises(dispatch.UnavailableError, match="HTTP 302"):
        api("github", "POST", "/graphql", {})
    assert requests[-1].full_url == "https://api.github.com/graphql"
    assert requests[-1].get_header("Authorization") == "Bearer synthetic-token"
    api.tokens["github"] = None
    with pytest.raises(dispatch.UnavailableError, match="missing github"):
        api("github", "POST", "/graphql", {})
    assert "secret-error-body" not in capsys.readouterr().out
