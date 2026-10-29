"""Label and assign a pull request from facts the contribution check already validates.

Nobody should have to remember a label. The type comes from the Conventional Commit
title (a closed list), the areas from the paths the PR changes, `breaking` from `!`,
and the author is assigned when the author is a person. Runs from trusted default-branch
code with API data only: the title and the file list are strings in an allow-list lookup
and never reach a shell or a request path unquoted. People and bots use the same
data-only classification; labels do not authorize work. Authors that manage their
own labels are configured in .github/pr-triage.json. Bots are never assigned.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path

from scripts.check_commit_subject import SUBJECT_RE, TYPES
from scripts.check_contribution_content import api

TYPE_PREFIX, AREA_PREFIX, BREAKING = "type: ", "area: ", "breaking"
MANAGED = (TYPE_PREFIX, AREA_PREFIX)

TYPE_COLORS = {
    "feat": "1D76DB",
    "fix": "D73A4A",
    "security": "B60205",
    "perf": "FBCA04",
    "change": "5319E7",
    "doctrine": "C5DEF5",
    "docs": "0075CA",
    "test": "BFD4F2",
    "refactor": "D4C5F9",
    "chore": "EDEDED",
    "design": "F9D0C4",
    "config": "FEF2C0",
    "build": "E4E669",
    "style": "EDEDED",
    "ci": "0E8A16",
    "ops": "006B75",
}
# area label -> (colour, description). Matching lives in `areas`.
AREA_DESCRIPTIONS = {
    "runtime": ("0366D6", "Nora's application code"),
    "doctrine": ("C5DEF5", "Voice, conduct, skills and Dreamer instructions"),
    "portal": ("5319E7", "The portal frontend and its API"),
    "site": ("BFDADC", "The public site and the wiki renderer"),
    "deploy": ("006B75", "Containers, Compose and Komodo"),
    "ci": ("0E8A16", "Workflows, hooks, scripts and the Makefile"),
    "docs": ("0075CA", "Documentation, ADRs and design"),
    "tests": ("BFD4F2", "Tests and evals"),
    "deps": ("E4E669", "Dependency manifests and lockfiles"),
}
DOCTRINE = ("nora/doctrine/", "nora/skills/", "nora/dreamer/")
DEPENDENCY_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "go.mod",
    "go.sum",
}
MAX_FILE_PAGES = 30
SELF_MANAGED_AUTHORS = frozenset(
    json.loads((Path(__file__).resolve().parents[1] / ".github/pr-triage.json").read_text())[
        "self_managed_authors"
    ]
)


def areas(path: str) -> set[str]:
    found = set()
    if path.startswith(DOCTRINE):
        found.add("doctrine")
    elif path.startswith("nora/web/"):
        found.add("portal")
    elif path.startswith("nora/"):
        found.add("runtime")
    if path.startswith("app/"):
        found.add("portal")
    if path.startswith(("site/", "docs-site/")):
        found.add("site")
    if path.startswith(("deploy/",)) or path in {"Dockerfile", "docker-compose.yml", "compose.yml"}:
        found.add("deploy")
    if path.startswith((".github/", "scripts/", ".githooks/")) or path == "Makefile":
        found.add("ci")
    if path.startswith(("docs/", "design/", "assets/", "openwiki/")) or (
        "/" not in path and path.endswith(".md")
    ):
        found.add("docs")
    if path.startswith(("tests/", "evals/")):
        found.add("tests")
    if path.rsplit("/", 1)[-1] in DEPENDENCY_FILES or path.rsplit("/", 1)[-1].startswith(
        "requirements"
    ):
        found.add("deps")
    return found


def wanted(title: str, paths: list[str]) -> dict[str, tuple[str, str]]:
    """The managed labels this PR should carry: name -> (colour, description)."""
    labels: dict[str, tuple[str, str]] = {}
    match = SUBJECT_RE.match(title.strip())
    if match and match.group(1) in TYPE_COLORS:
        kind = match.group(1)
        labels[TYPE_PREFIX + kind] = (TYPE_COLORS[kind], TYPES[kind])
        if title.strip().split(":", 1)[0].endswith("!"):
            labels[BREAKING] = ("B60205", "A breaking change")
    found: set[str] = set()
    for path in paths:
        found |= areas(path)
    for name in sorted(found):
        colour, description = AREA_DESCRIPTIONS[name]
        labels[AREA_PREFIX + name] = (colour, description)
    return labels


def all_labels() -> dict[str, tuple[str, str]]:
    labels = {TYPE_PREFIX + k: (c, TYPES[k]) for k, c in TYPE_COLORS.items()}
    labels |= {AREA_PREFIX + n: v for n, v in AREA_DESCRIPTIONS.items()}
    labels[BREAKING] = ("B60205", "A breaking change")
    return labels


def ensure_label(github, repository: str, name: str, colour: str, description: str) -> None:
    quoted = urllib.parse.quote(name, safe="")
    try:
        github("GET", f"/repos/{repository}/labels/{quoted}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        github(
            "POST",
            f"/repos/{repository}/labels",
            {"name": name, "color": colour, "description": description},
        )


def changed_paths(github, repository: str, number: int) -> list[str]:
    paths, page = [], 1
    while page <= MAX_FILE_PAGES:
        batch = github("GET", f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
        paths.extend(f["filename"] for f in batch)
        if len(batch) < 100:
            break
        page += 1
    return paths


def sync_labels(
    github, repository: str, number: int, have: set[str], want: dict, managed: set[str]
) -> dict:
    """Reconcile only the labels this classifier owns, preserving other metadata."""
    removed = sorted((have & managed) - set(want))
    for name in removed:
        quoted = urllib.parse.quote(name, safe="")
        github("DELETE", f"/repos/{repository}/issues/{number}/labels/{quoted}")
    added = sorted(set(want) - have)
    for name in added:
        ensure_label(github, repository, name, *want[name])
    if added:
        github("POST", f"/repos/{repository}/issues/{number}/labels", {"labels": added})
    return {"added": added, "removed": removed}


def triage(github, repository: str, number: int) -> dict:
    item = github("GET", f"/repos/{repository}/pulls/{number}")
    login = item["user"]["login"]
    is_person = item["user"]["type"] == "User"
    if login in SELF_MANAGED_AUTHORS:
        return {"skipped": "author manages its own labels"}
    if item["user"]["type"] not in {"User", "Bot"}:
        return {"skipped": "unsupported author type"}
    want = wanted(item["title"], changed_paths(github, repository, number))
    have = {label["name"] for label in item["labels"]}
    managed_have = {n for n in have if n.startswith(MANAGED) or n == BREAKING}
    result = sync_labels(github, repository, number, have, want, managed_have)
    assigned = {a["login"] for a in item.get("assignees", [])}
    if is_person and login not in assigned:
        try:
            github("POST", f"/repos/{repository}/issues/{number}/assignees", {"assignees": [login]})
        except urllib.error.HTTPError as error:
            if error.code != 422:  # an author who cannot be assigned is not an error
                raise
    return {**result, "assignee": login if is_person else None}


def main(argv: list[str] | None = None) -> int:
    repository = os.environ["GITHUB_REPOSITORY"]
    if argv and argv[0] == "--ensure-labels":
        for name, (colour, description) in all_labels().items():
            ensure_label(api, repository, name, colour, description)
        print(f"{len(all_labels())} labels exist.")
        return 0
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        number = int(event["inputs"]["number"])
    else:
        number = event["pull_request"]["number"]
    if number < 1:
        raise ValueError("Expected a positive pull request number")
    print(json.dumps(triage(api, repository, number)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
