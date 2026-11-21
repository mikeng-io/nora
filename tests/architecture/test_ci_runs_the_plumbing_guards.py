"""CI executes every guard on the repo's own plumbing, not only `make gate` on a laptop.

The CI job runs an explicit list of architecture tests. A guard that is absent from that list
passes locally and is never run where merges are decided, so a later change can break the
release, triage, commit-rule or base-image contracts and still merge. Aikido's review of the
base-image guard found exactly that gap: this test closes it for every plumbing guard.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PLUMBING_GUARDS = (
    "tests/architecture/test_contribution_conventions.py",
    "tests/architecture/test_contribution_content.py",
    "tests/architecture/test_commit_body_rule.py",
    "tests/architecture/test_pr_triage.py",
    "tests/architecture/test_public_repository_settings.py",
    "tests/architecture/test_release_scope.py",
    "tests/architecture/test_release_plan.py",
    "tests/architecture/test_release_config_matches_the_repo.py",
    "tests/architecture/test_dockerfile_bases_are_pinned.py",
    "tests/architecture/test_doc_references_resolve.py",
)


def test_ci_runs_every_plumbing_guard():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    missing = [guard for guard in PLUMBING_GUARDS if guard not in ci]
    assert not missing, f"ci.yml does not run: {missing}"


def test_every_listed_guard_exists():
    absent = [guard for guard in PLUMBING_GUARDS if not (ROOT / guard).is_file()]
    assert not absent, f"listed guards that do not exist: {absent}"
