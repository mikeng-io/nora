"""Apply free public-repository settings with the authenticated owner's gh CLI.

No visibility change, paid security license, billing setting or App credential
is created here. Run locally after publication, not from an untrusted PR job.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess


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
        print("Public repository. Use --apply to enable free security settings.")
        return
    api(
        args.repo,
        method="PATCH",
        body={
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
        "/actions/permissions/fork-pr-contributor-approval",
        "PUT",
        {"approval_policy": "all_external_contributors"},
    )
    workflow_policy = api(args.repo, "/actions/permissions/workflow")
    fork_policy = api(args.repo, "/actions/permissions/fork-pr-contributor-approval")
    current = api(args.repo)
    checks = [
        workflow_policy["default_workflow_permissions"] == "read",
        fork_policy["approval_policy"] == "all_external_contributors",
        api(args.repo, "/automated-security-fixes")["enabled"],
        api(args.repo, "/private-vulnerability-reporting")["enabled"],
        all(
            current["security_and_analysis"][key]["status"] == "enabled"
            for key in ("secret_scanning", "secret_scanning_push_protection")
        ),
    ]
    if not all(checks):
        raise RuntimeError("GitHub settings read-back did not match the requested policy")
    print("Read back: public security settings applied. Merge rules: configure_github_rules.py.")


if __name__ == "__main__":
    main()
