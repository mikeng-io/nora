"""Make the merge rules a GitHub rule, not a convention: required checks, PR-only, no bypass.

Works on a private repository (GitHub Pro or Team) and on a public one. Rulesets, not
legacy branch protection, because only a ruleset can say "no bypass actors" and be read
back. It changes no visibility, billing or security-product setting. Run locally with an
owner `gh` login, never from a PR job:

    python3 scripts/configure_github_rules.py           # show what would change
    python3 scripts/configure_github_rules.py --apply   # apply, then read it all back

`pr-metadata` is the job that runs the PR content check; it fails when the title, body or
branch break the rules. The `PR content` check run it also publishes is NOT required: GitHub's
merge evaluation answered "Required status check 'PR content' is expected" for it although it
was green, because it is created through the Checks API rather than by a job, and that blocked
every merge.

Selective CodeQL, dependency review and site jobs feed the unconditional `checks`
result rather than being required individually. A required check must report on
EVERY pull request; requiring a path-filtered workflow would block unrelated PRs.
"""

from __future__ import annotations

import argparse
import sys

from scripts.configure_public_github import api

GITHUB_ACTIONS_APP_ID = 15368
REQUIRED = ["checks", "pr-metadata"]
BRANCH_RULESET = "master requires a conforming pull request"
TAG_RULESET = "Release tags are immutable"
REPO_SETTINGS = {
    "allow_merge_commit": True,
    "allow_squash_merge": False,
    "allow_rebase_merge": False,
    "allow_auto_merge": True,
    "allow_update_branch": True,
    "delete_branch_on_merge": True,
}


def checks_wanted() -> list[dict]:
    return [{"context": name, "integration_id": GITHUB_ACTIONS_APP_ID} for name in REQUIRED]


def branch_ruleset() -> dict:
    return {
        "name": BRANCH_RULESET,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["merge"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": checks_wanted(),
                },
            },
        ],
    }


def tag_ruleset() -> dict:
    return {
        "name": TAG_RULESET,
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }


def upsert(repo: str, ruleset: dict) -> dict:
    """Create the ruleset, or replace the one of that name; other owners' rulesets stay."""
    existing = next((row for row in api(repo, "/rulesets") if row["name"] == ruleset["name"]), None)
    path = f"/rulesets/{existing['id']}" if existing else "/rulesets"
    saved = api(repo, path, "PUT" if existing else "POST", ruleset)
    return api(repo, f"/rulesets/{saved['id']}")


def problems(repo_state: dict, branch: dict, tag: dict) -> list[str]:
    """Everything the read-back must show; an empty list is the only PASS."""
    found = []
    for key, want in REPO_SETTINGS.items():
        if repo_state.get(key) != want:
            found.append(f"repository {key} is {repo_state.get(key)!r}, wanted {want!r}")
    for ruleset, name in ((branch, BRANCH_RULESET), (tag, TAG_RULESET)):
        if ruleset.get("name") != name or ruleset.get("enforcement") != "active":
            found.append(f"{name}: not active")
        if ruleset.get("bypass_actors") != []:
            found.append(f"{name}: has bypass actors")
    by_type = {rule["type"]: rule for rule in branch.get("rules", [])}
    checks = by_type.get("required_status_checks", {}).get("parameters", {})
    contexts = [c["context"] for c in checks.get("required_status_checks", [])]
    if sorted(contexts) != sorted(REQUIRED):
        found.append(f"required checks are {contexts}, wanted {REQUIRED}")
    if not checks.get("strict_required_status_checks_policy"):
        found.append("required checks do not need an up-to-date branch")
    wanted = {c["context"]: c["integration_id"] for c in checks_wanted()}
    seen = {c["context"]: c.get("integration_id") for c in checks.get("required_status_checks", [])}
    if seen != wanted:
        found.append(f"required checks are bound to {seen}, wanted {wanted}")
    pull = by_type.get("pull_request", {}).get("parameters", {})
    if pull.get("allowed_merge_methods") != ["merge"]:
        found.append(f"merge methods are {pull.get('allowed_merge_methods')}, wanted ['merge']")
    for key in ("dismiss_stale_reviews_on_push", "required_review_thread_resolution"):
        if not pull.get(key):
            found.append(f"pull request rule {key} is off")
    for ruleset, want in ((branch, branch_ruleset()), (tag, tag_ruleset())):
        if ruleset.get("conditions") != want["conditions"]:
            found.append(f"{want['name']}: applies to {ruleset.get('conditions')}")
    if not {"deletion", "non_fast_forward"} <= by_type.keys():
        found.append("force-push or deletion of the default branch is not blocked")
    if {rule["type"] for rule in tag.get("rules", [])} != {"update", "deletion"}:
        found.append("release tags are not immutable")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="mikeng-io/nora")
    parser.add_argument("--apply", action="store_true", help="write the settings, then read back")
    args = parser.parse_args(argv)
    if not args.apply:
        print(f"Would require {REQUIRED} on the default branch of {args.repo}, with no bypass.")
        print("Run with --apply to write it.")
        return 0
    api(args.repo, method="PATCH", body=REPO_SETTINGS)
    branch = upsert(args.repo, branch_ruleset())
    tag = upsert(args.repo, tag_ruleset())
    bad = problems(api(args.repo), branch, tag)
    if bad:
        print("Read-back did not match the requested policy:", *bad, sep="\n  ", file=sys.stderr)
        return 1
    print(f"Read back: {BRANCH_RULESET!r} and {TAG_RULESET!r} are active, no bypass actors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
