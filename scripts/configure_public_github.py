"""Apply free public-repository settings with the authenticated owner's gh CLI.

No visibility change, paid security license, billing setting or App credential
is created here. Run locally after publication, not from an untrusted PR job.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess

REQUIRED = [
    "PR content",
    "checks",
    "dependency-review",
    "CodeQL (python)",
    "CodeQL (javascript-typescript)",
    "CodeQL (go)",
    "CodeQL (actions)",
]


def api(repo: str, path: str = "", method: str = "GET", body: dict | None = None):
    command = [
        shutil.which("gh") or "/usr/bin/gh",
        "api",
        f"repos/{repo}{path}",
        "--method",
        method,
    ]
    if body is not None:
        command += ["--input", "-"]
    output = subprocess.check_output(  # noqa: S603 -- fixed gh API arguments
        command,
        input=json.dumps(body) if body is not None else None,
        text=True,
    )
    return json.loads(output) if output.strip() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="mikeng-io/nora")
    parser.add_argument("--apply", action="store_true", help="apply after checking visibility")
    args = parser.parse_args()
    repo = api(args.repo)
    if repo["private"]:
        parser.exit(
            1, "Repository is private. No settings changed; publish separately before applying.\n"
        )
    if not args.apply:
        print("Public repository. Use --apply to enable free security and branch/tag protection.")
        return
    api(
        args.repo,
        method="PATCH",
        body={
            "allow_merge_commit": True,
            "allow_squash_merge": False,
            "allow_rebase_merge": False,
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    for path in (
        "/vulnerability-alerts",
        "/automated-security-fixes",
        "/private-vulnerability-reporting",
    ):
        api(args.repo, path, "PUT")
    api(args.repo, "/actions/permissions", "PUT", {"enabled": True, "allowed_actions": "selected"})
    api(
        args.repo,
        "/actions/permissions/selected-actions",
        "PUT",
        {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": ["astral-sh/setup-uv@*", "googleapis/release-please-action@*"],
        },
    )
    api(
        args.repo,
        "/actions/permissions/workflow",
        "PUT",
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": True},
    )
    api(
        args.repo,
        "/branches/master/protection",
        "PUT",
        {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": name, "app_id": 15368} for name in REQUIRED],
            },
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": True,
            },
            "restrictions": None,
            "required_conversation_resolution": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        },
    )
    api(
        args.repo,
        "/actions/permissions/fork-pr-contributor-approval",
        "PUT",
        {"approval_policy": "all_external_contributors"},
    )
    # Idempotent managed ruleset; other owners' rulesets are preserved.
    name = "Release tags are immutable"
    ruleset = {
        "name": name,
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }
    existing = next((row for row in api(args.repo, "/rulesets") if row["name"] == name), None)
    managed = api(
        args.repo,
        f"/rulesets/{existing['id']}" if existing else "/rulesets",
        "PUT" if existing else "POST",
        ruleset,
    )
    tag_policy = api(args.repo, f"/rulesets/{managed['id']}")
    workflow_policy = api(args.repo, "/actions/permissions/workflow")
    fork_policy = api(args.repo, "/actions/permissions/fork-pr-contributor-approval")
    current = api(args.repo)
    protection = api(args.repo, "/branches/master/protection")
    checks = [
        workflow_policy["default_workflow_permissions"] == "read",
        fork_policy["approval_policy"] == "all_external_contributors",
        tag_policy["enforcement"] == "active" and tag_policy["bypass_actors"] == [],
        {rule["type"] for rule in tag_policy["rules"]} == {"update", "deletion"},
        current["allow_auto_merge"] and not current["allow_squash_merge"],
        set(protection["required_status_checks"]["contexts"]) == set(REQUIRED),
        api(args.repo, "/automated-security-fixes")["enabled"],
        api(args.repo, "/private-vulnerability-reporting")["enabled"],
        all(
            current["security_and_analysis"][key]["status"] == "enabled"
            for key in ("secret_scanning", "secret_scanning_push_protection")
        ),
    ]
    if not all(checks):
        raise RuntimeError("GitHub settings read-back did not match the requested policy")
    print("Read back: public security settings, required checks and release tag rules applied.")


if __name__ == "__main__":
    main()
