"""Classify an issue's explicit Area/title with the existing repository label vocabulary.

Only the nine known area labels are managed. Form, priority and dispatch labels
are untouched; classification never authorizes work or changes issue state.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scripts.check_contribution_content import api, sections
from scripts.triage_pr import AREA_DESCRIPTIONS, AREA_PREFIX, sync_labels

# Exact aliases, not keyword searches through narrative or code examples.
ALIASES = {
    "runtime": {
        "runtime",
        "agent",
        "participation",
        "notes",
        "note tier",
        "cognition",
        "relevance-gate",
        "discord",
        "signal",
        "whatsapp",
        "signal / whatsapp",
        "media",
        "presence-analytics",
        "search",
        "memory",
        "observability",
        "thread reads",
        "standing read",
        "profile",
        "private conversations",
        "voice",
        "version",
    },
    "doctrine": {"doctrine", "skills"},
    "portal": {"portal"},
    "site": {"site", "website", "wiki"},
    "deploy": {"deploy", "deployment", "container images", "release-and-version"},
    "ci": {
        "ci",
        "repo-hygiene",
        "engineering orchestration",
        "repo-hygiene / engineering orchestration",
    },
    "docs": {"docs", "documentation", "design-only"},
    "tests": {"tests", "architecture tests", "mutation tables", "regression proofs"},
    "deps": {"deps", "dependencies"},
}


def named_areas(value: str) -> set[str]:
    key = value.strip().casefold().partition(" (")[0]
    if key == "repo-hygiene / tests":
        return {"ci", "tests"}
    return {area for area, aliases in ALIASES.items() if key in aliases}


def issue_areas(title: str, body: str) -> set[str]:
    prefix, separator, _ = title.partition(":")
    from_title = named_areas(prefix) if separator else set()
    fields = [value for name, value in sections(body, levels=(2, 3)) if name == "Area"]
    if not fields:
        return from_title
    if len(fields) != 1:
        return set()
    from_form = named_areas(fields[0])
    if from_title and not from_title & from_form:
        return set()
    return from_form


def triage(github, repository: str, number: int) -> dict:
    item = github("GET", f"/repos/{repository}/issues/{number}")
    if "pull_request" in item:
        raise ValueError("Issue triage cannot be used for pull requests")
    found = issue_areas(item["title"], item.get("body") or "")
    want = {AREA_PREFIX + area: AREA_DESCRIPTIONS[area] for area in found}
    managed = {AREA_PREFIX + area for area in AREA_DESCRIPTIONS}
    have = {label["name"] for label in item["labels"]}
    return {"areas": sorted(found), **sync_labels(github, repository, number, have, want, managed)}


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    number = (
        int(event["inputs"]["number"])
        if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
        else event["issue"]["number"]
    )
    if number < 1:
        raise ValueError("Expected a positive issue number")
    print(json.dumps(triage(api, os.environ["GITHUB_REPOSITORY"], number)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
