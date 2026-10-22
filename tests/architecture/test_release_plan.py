"""The release PR is planned from git reachability, not commit dates (DEC-314, ADR 0025).

release-please orders history by committer date and stops at the last release
commit, so commits written before a release and merged after it never reach its
changelog (five `security` commits merged after a release, and no release PR). These
tests run `scripts/release_plan.py` against real git history for that case, and
check that what it writes is what release-please writes, so the release-please
action can still tag the PR it did not build.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO = "owner/repo"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolves its own module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plan():
    sys.path.insert(0, str(ROOT))
    return load("release_plan")


def run(*args: str, when: str | None = None) -> str:
    env = {**os.environ, **({"GIT_COMMITTER_DATE": when} if when else {})}
    return subprocess.check_output(["git", *args], text=True, env=env)  # noqa: S603


@pytest.fixture
def repo(tmp_path, monkeypatch, plan):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    run("init", "-q", "-b", "master")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "Test")
    run("config", "core.hooksPath", "/dev/null")
    (tmp_path / ".release-please-manifest.json").write_text('{\n  ".": "1.0.0"\n}\n')
    (tmp_path / "nora").mkdir()
    (tmp_path / "nora/__init__.py").write_text('__version__ = "1.0.0"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "nora"\nversion = "1.0.0"\n')
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "other"\nversion = "1.0.0"\n\n'
        '[[package]]\nname = "nora"\nversion = "1.0.0"\nsource = { editable = "." }\n'
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0](x) (2026-01-01)\n")
    run("add", ".")
    run("commit", "-qm", "chore: initial")
    run("tag", "v1.0.0")
    return tmp_path


def commit(repo: Path, path: str, subject: str, when: str | None = None) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target.read_text() + "x" if target.exists() else "x")
    run("add", ".")
    run("commit", "-qm", subject, when=when)
    return run("rev-parse", "HEAD").strip()


def test_commits_dated_before_the_last_release_but_merged_after_it_are_planned(repo, plan):
    """A branch written before a release and merged after it, the shape release-please drops."""
    run("switch", "-qc", "branch")
    commit(
        repo, "nora/bridge.py", "security(bridge): require a bearer token", "2020-01-01T00:00:00"
    )
    run("switch", "-q", "master")
    commit(repo, "nora/other.py", "feat: an unrelated feature", "2021-01-01T00:00:00")
    run("tag", "-f", "v1.0.0")  # the release commit, dated after the branch's commits
    run("merge", "-q", "--no-ff", "branch", "-m", "merge: the branch")

    found = plan.entries("v1.0.0")

    assert [(e.type, e.scope) for e in found] == [("security", "bridge")]


def test_nothing_unreleased_plans_nothing(repo, plan):
    commit(repo, "docs/note.md", "docs: describe a thing")
    commit(repo, "design/new.md", "feat: describe future work")
    assert plan.entries("v1.0.0") == []


@pytest.mark.parametrize(
    "subjects,expected",
    [
        (["fix: a"], "1.0.1"),
        (["security(x): a"], "1.0.1"),
        (["fix: a", "feat: b"], "1.1.0"),
        (["feat: a", "fix(x)!: b"], "2.0.0"),
        (["perf: a", "doctrine: b"], "1.0.1"),
    ],
)
def test_the_bump_follows_conventional_commits(repo, plan, subjects, expected):
    for index, subject in enumerate(subjects):
        commit(repo, f"nora/f{index}.py", subject)
    assert plan.bump("1.0.0", plan.entries("v1.0.0")) == expected


def test_a_breaking_change_footer_bumps_major(repo, plan):
    (repo / "nora").mkdir(exist_ok=True)
    (repo / "nora/a.py").write_text("x")
    run("add", ".")
    run("commit", "-qm", "change: drop a verb\n\nBREAKING CHANGE: the verb is gone")
    found = plan.entries("v1.0.0")
    assert found[0].breaking
    assert plan.bump("1.0.0", found) == "2.0.0"


def test_rendered_section_matches_the_one_release_please_wrote(plan):
    """6.6.0's section, parsed back to entries and rendered, is the same text."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [6.6.0]")
    expected = changelog[start : changelog.index("## [6.5.1]")].rstrip("\n") + "\n"
    kinds = {title: kind for kind, title in plan.sections()}
    entries, kind = [], ""
    for row in expected.splitlines():
        if row.startswith("### "):
            kind = kinds[row[4:]]
        elif row.startswith("* "):
            match = re.fullmatch(
                r"\* (?:\*\*(?P<scope>[^*]+):\*\* )?(?P<text>.+) \(\[[0-9a-f]{7}\]"
                r"\(https://github.com/mikeng-io/nora/commit/(?P<sha>[0-9a-f]{40})\)\)",
                row,
            )
            assert match, row
            entries.append(
                plan.Entry(match["sha"], kind, match["scope"] or "", match["text"], False)
            )

    assert plan.render("6.5.1", "6.6.0", entries, "mikeng-io/nora", "2026-10-22") == expected


def test_the_pr_body_is_what_the_release_action_parses(repo, plan, tmp_path):
    commit(repo, "nora/f.py", "fix(x): repair a thing")
    out = tmp_path / "out"
    assert plan.main(["--repo", REPO, "--out", str(out), "--today", "2026-02-03"]) == 0
    body = (out / "body.md").read_text()
    assert body.startswith(":robot: I have created a release *beep* *boop*\n---\n\n\n## [1.0.1](")
    assert re.search(
        r"^## \[1\.0\.1\]\(https://github.com/owner/repo/compare/v1\.0\.0\.\.\.v1\.0\.1\) "
        r"\(2026-02-03\)$",
        body,
        re.M,
    )
    assert (out / "title.txt").read_text() == "chore(master): release 1.0.1\n"


def test_apply_moves_every_versioned_file_together(repo, plan, tmp_path):
    commit(repo, "nora/f.py", "feat: add a thing")
    assert plan.main(["--repo", REPO, "--out", str(tmp_path / "o"), "--today", "2026-02-03"]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.1.0"}
    assert (repo / "nora/__init__.py").read_text() == '__version__ = "1.1.0"\n'
    assert 'version = "1.1.0"' in (repo / "pyproject.toml").read_text()
    lock = (repo / "uv.lock").read_text()
    assert 'name = "nora"\nversion = "1.1.0"' in lock
    assert 'name = "other"\nversion = "1.0.0"' in lock, "another package's version must not move"
    assert (repo / "CHANGELOG.md").read_text().startswith("# Changelog\n\n## [1.1.0](")


def test_a_tag_that_disagrees_with_the_manifest_is_an_error_not_a_release(repo, plan):
    (repo / ".release-please-manifest.json").write_text('{\n  ".": "1.0.5"\n}\n')
    with pytest.raises(ValueError, match="disagrees"):
        plan.previous_release(repo)


def test_a_file_that_does_not_hold_the_old_version_is_an_error(repo, plan):
    (repo / "nora/__init__.py").write_text('__version__ = "0.9.0"\n')
    with pytest.raises(ValueError, match="expected exactly one match"):
        plan.apply(repo, "1.0.0", "1.0.1", "## [1.0.1](x) (2026-01-01)\n")


def test_git_credentials_are_set_before_the_first_remote_operation_in_the_plan_step():
    """checkout drops its token and the repository is private, so `git fetch` without the
    helper fails on every push to master and no release PR is ever planned."""
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
    step = workflow[workflow.index("Open or update the release PR") :]
    step = step[: step.index("- name: Queue the generated release PR")]
    remote = [step.index(op) for op in ("git fetch", "git push") if op in step]
    assert remote, "the plan step no longer talks to the remote"
    assert step.index("gh auth setup-git") < min(remote)
