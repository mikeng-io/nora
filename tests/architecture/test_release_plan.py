"""The release PR is planned from git reachability, not commit dates (DEC-314, ADR 0027).

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
import yaml

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

    assert [(e.type, e.scope) for e in found if e.type != "merge"] == [("security", "bridge")]


def test_nothing_unreleased_plans_nothing(repo, plan):
    assert plan.entries("v1.0.0") == []


@pytest.mark.parametrize(
    "subjects,expected",
    [
        (["fix: a"], "1.0.1"),
        (["security(x): a"], "1.0.1"),
        (["fix: a", "feat: b"], "1.0.1"),
        (["perf: a", "doctrine: b"], "1.0.1"),
    ],
)
def test_ordinary_changes_default_to_patch_even_with_feat(repo, plan, subjects, expected):
    for index, subject in enumerate(subjects):
        commit(repo, f"nora/f{index}.py", subject)
    assert plan.bump("1.0.0", plan.entries("v1.0.0")) == expected


def test_a_breaking_change_footer_requires_owner_selected_major(repo, plan):
    (repo / "nora").mkdir(exist_ok=True)
    (repo / "nora/a.py").write_text("x")
    run("add", ".")
    run("commit", "-qm", "change: drop a verb\n\nBREAKING CHANGE: the verb is gone")
    found = plan.entries("v1.0.0")
    assert found[0].breaking
    with pytest.raises(ValueError, match="owner-selected major"):
        plan.bump("1.0.0", found)
    assert plan.bump("1.0.0", found, "major") == "2.0.0"


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
    assert (
        plan.main(
            [
                "--repo",
                REPO,
                "--out",
                str(out),
                "--today",
                "2026-02-03",
            ]
        )
        == 0
    )
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
    commit(repo, "nora/f.py", "feat: add a thing\n\nRelease-Bump: minor")
    assert (
        plan.main(
            [
                "--repo",
                REPO,
                "--out",
                str(tmp_path / "o"),
                "--today",
                "2026-02-03",
            ]
        )
        == 0
    )
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

    remote = [step.index(op) for op in ("git fetch", "git push") if op in step]
    assert remote, "the plan step no longer talks to the remote"
    assert step.index("gh auth setup-git") < min(remote)


def test_bot_commits_are_attributed_to_the_app_not_a_generic_noreply_account():
    """`noreply@users.noreply.github.com` resolves to an unrelated GitHub account, so a
    commit made under it is credited to a stranger. The release App's own identity links."""
    for name in ("release-please.yml", "openwiki-update.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "user.email 'noreply@users.noreply.github.com'" not in text, name
        assert '+${APP_SLUG}[bot]@users.noreply.github.com"' in text, name
        assert "RELEASE_APP_BOT_USER_ID" in text, name


@pytest.mark.parametrize(
    "subject", ["docs: clarify setup", "ci: improve checks", "config: adjust defaults"]
)
def test_internal_only_changes_automatically_write_a_patch(repo, plan, subject):
    commit(repo, "docs/note.md", subject)
    assert plan.main(["--repo", REPO, "--out", str(repo / "out")]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.1"}
    assert (repo / "out/title.txt").exists()


def test_explicit_milestone_footer_automatically_selects_minor(repo, plan):
    commit(repo, "nora/f.py", "feat: add a supported capability\n\nRelease-Bump: minor")
    assert plan.main(["--repo", REPO, "--out", str(repo / "out")]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.1.0"}
    assert "## [1.1.0]" in (repo / "CHANGELOG.md").read_text()


def test_assessed_candidate_must_still_be_the_prepared_head(repo, plan):
    assessed = commit(repo, "nora/f.py", "fix: repair a thing")
    commit(repo, "nora/g.py", "feat: add a later capability\n\nRelease-Bump: minor")
    with pytest.raises(ValueError, match="candidate changed"):
        plan.main(
            [
                "--repo",
                REPO,
                "--candidate",
                assessed,
                "--out",
                str(repo / "out"),
            ]
        )
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.0"}
    assert not (repo / "out").exists()

    current = run("rev-parse", "HEAD").strip()
    assert (
        plan.main(
            [
                "--repo",
                REPO,
                "--candidate",
                current,
                "--out",
                str(repo / "out"),
            ]
        )
        == 0
    )
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.1.0"}


def test_a_compatible_feature_can_ship_as_an_assessed_product_patch(repo, plan):
    commit(repo, "app/existing-feature.ts", "feat(app): improve an existing capability")
    assert plan.main(["--repo", REPO, "--out", str(repo / "out")]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.1"}
    assert "## [1.0.1]" in (repo / "CHANGELOG.md").read_text()


@pytest.mark.parametrize(
    "subject",
    ["change!: remove a verb", "change: remove a verb\n\nBREAKING CHANGE: the verb is gone"],
)
@pytest.mark.parametrize("selected", ["patch", "minor"])
def test_manual_bump_cannot_hide_a_declared_compatibility_change(repo, plan, subject, selected):
    commit(repo, "nora/f.py", subject)
    with pytest.raises(ValueError, match="below the"):
        plan.main(
            [
                "--repo",
                REPO,
                "--bump",
                selected,
                "--candidate",
                run("rev-parse", "HEAD").strip(),
                "--out",
                str(repo / "out"),
            ]
        )
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.0"}
    assert not (repo / "out").exists()


@pytest.mark.parametrize(
    "subject",
    [
        "change!: remove a verb",
        "ci!: remove a supported command",
        "docs: remove a verb\n\nBREAKING CHANGE: the verb is gone",
        "change: remove a verb\n\nBREAKING-CHANGE:the verb is gone",
    ],
)
def test_automatic_major_is_refused_before_any_version_write(repo, plan, subject):
    commit(repo, "docs/note.md", subject)
    before = {p: (repo / p).read_bytes() for p in plan_files()}
    with pytest.raises(ValueError, match="owner-selected major"):
        plan.main(["--repo", REPO, "--out", str(repo / "out")])
    assert {p: (repo / p).read_bytes() for p in plan_files()} == before
    assert not (repo / "out").exists()


def plan_files():
    return (
        ".release-please-manifest.json",
        "CHANGELOG.md",
        "nora/__init__.py",
        "pyproject.toml",
        "uv.lock",
    )


def test_manual_major_requires_full_current_candidate(repo, plan):
    source = commit(repo, "nora/f.py", "change!: remove supported behavior")
    args = ["--repo", REPO, "--bump", "major", "--out", str(repo / "out")]
    for candidate in (None, source[:7], "a" * 40):
        with pytest.raises(ValueError, match="candidate"):
            plan.main(args + (["--candidate", candidate] if candidate else []))
        assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.0"}
    assert plan.main([*args, "--candidate", source]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "2.0.0"}


def test_breaking_merge_message_is_not_hidden(repo, plan):
    run("switch", "-qc", "topic")
    commit(repo, "docs/a.md", "docs: clarify behavior")
    run("switch", "-q", "master")
    run("merge", "--no-ff", "topic", "-m", "merge!: remove supported behavior")
    with pytest.raises(ValueError, match="owner-selected major"):
        plan.main(["--repo", REPO, "--out", str(repo / "out")])


def test_metadata_merge_publication_and_later_internal_change_do_not_loop(repo, plan):
    commit(repo, "docs/ordinary.md", "docs: clarify setup")
    source = run("rev-parse", "HEAD").strip()
    out = repo.parent / "release-output"
    run("switch", "-qc", plan.PR_HEAD)
    assert plan.main(["--repo", REPO, "--out", str(out)]) == 0
    assert f"Release-Candidate: {source}" in (out / "body.md").read_text()
    run("add", ".")
    run("commit", "-qm", "chore(master): release 1.0.1")
    run("switch", "-q", "master")
    run("merge", "--no-ff", plan.PR_HEAD, "-m", "merge: prepare release v1.0.1 (#1)")
    # The generated commit and its merge are excluded even before tagging.
    assert [e.type for e in plan.entries(source)] == []
    # A publication failure must not create another version on top of metadata.
    before = (repo / ".release-please-manifest.json").read_bytes()
    with pytest.raises(ValueError, match="disagrees"):
        plan.main(["--repo", REPO, "--out", str(out)])
    assert (repo / ".release-please-manifest.json").read_bytes() == before
    # release-please publishes this merge; planning resumes without another bump.
    run("tag", "v1.0.1")
    assert plan.main(["--repo", REPO, "--out", str(out)]) == 0
    assert (repo / ".release-please-manifest.json").read_bytes() == before
    commit(repo, "tests/new.py", "test: cover a detail")
    assert plan.main(["--repo", REPO, "--out", str(out)]) == 0
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.2"}


def release_workflow():
    return yaml.safe_load((ROOT / ".github/workflows/release-please.yml").read_text())


def test_master_push_publishes_before_preparation_with_app_and_checked_auto_merge():
    workflow = release_workflow()
    assert set(workflow[True]) == {"push", "workflow_dispatch"}
    assert workflow[True]["push"] == {"branches": ["master"]}
    assert workflow["concurrency"] == {"group": "release-please", "cancel-in-progress": False}
    job = workflow["jobs"]["release-please"]
    assert job["if"] == "github.repository == 'mikeng-io/nora' && github.ref == 'refs/heads/master'"
    steps = job["steps"]
    app = next(s for s in steps if s.get("id") == "app")
    assert app["with"]["repositories"] == "${{ github.event.repository.name }}"
    assert set(app["with"]) == {
        "app-id",
        "private-key",
        "repositories",
        "permission-contents",
        "permission-pull-requests",
    }
    assert any(s.get("if") == "vars.RELEASE_APP_ID == ''" and "exit 1" in s["run"] for s in steps)
    publish = next(s for s in steps if s.get("id") == "release")
    prepare = next(s for s in steps if s.get("id") == "plan")
    assert steps.index(publish) < steps.index(prepare)
    assert publish["if"] == "github.event_name == 'push' || inputs.action == 'publish'"
    assert publish["with"]["skip-github-pull-request"] is True
    assert publish["with"]["token"] == "${{ steps.app.outputs.token }}"  # noqa: S105
    assert prepare["if"] == "github.event_name == 'push' || inputs.action == 'prepare'"
    script = prepare["run"]
    assert script.index("git fetch --tags") < script.index("python3 -m scripts.release_plan")
    assert "git checkout --detach origin/master" in script
    assert 'args=(--candidate "$source")' in script
    assert 'if [ "$EVENT" = workflow_dispatch ]; then' in script
    assert 'test "$source" = "$RELEASE_CANDIDATE"' in script
    assert 'args+=(--bump "$RELEASE_BUMP")' in script
    assert "git push --force-with-lease=" in script
    assert 'test "$source" = "$(git rev-parse origin/master)"' in script
    assert '--auto --merge --match-head-commit "$head"' in script
    assert '--subject "merge: prepare release v$version (#$number)"' in script
    assert "--admin" not in script and "github.token" not in str(job)
    assert script.count("gh pr merge") == 1


@pytest.mark.parametrize(
    "actor,trigger,allowed",
    [
        ("owner", "owner", True),
        ("other", "owner", False),
        ("owner", "other", False),
    ],
)
def test_owner_dispatch_and_rerun_identity_are_both_required(actor, trigger, allowed):
    steps = release_workflow()["jobs"]["release-please"]["steps"]
    guard = next(s for s in steps if s.get("name") == "Admit only owner dispatches and reruns")
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert guard["env"]["TRIGGERING_ACTOR"] == "${{ github.triggering_actor }}"
    result = subprocess.run(  # noqa: S603 -- repository-owned workflow shell
        ["bash", "-c", guard["run"]],
        env={**os.environ, "OWNER": "owner", "ACTOR": actor, "TRIGGERING_ACTOR": trigger},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is allowed


@pytest.fixture
def workflow_repo(repo, monkeypatch):
    """Execute the real plan shell; only gh's API boundary is simulated.

    Git operations go to a local bare remote. The gh executable implements distinct
    list/create/edit/view/merge responses and refuses unknown calls, including any
    merge whose remote source/head differs from the exact queued values.
    """
    import shutil

    harness = repo.parent / (repo.name + "-harness")
    harness.mkdir()
    remote = harness / "origin.git"
    run("init", "--bare", str(remote))
    run("remote", "add", "origin", str(remote))
    run("push", "origin", "master", "--tags")
    # Run the production module in the synthetic repository without committing
    # harness plumbing or making that plumbing another release input.
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("")
    for name in ("release_plan.py", "release_scope.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    shutil.copyfile(ROOT / "release-please-config.json", repo / "release-please-config.json")
    (repo / ".git/info/exclude").write_text("scripts/\nrelease-please-config.json\n")
    state = harness / "gh-state.json"
    state.write_text("{}")
    binary = harness / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
state = pathlib.Path(os.environ['GH_STATE'])
s = json.loads(state.read_text())
a = sys.argv[1:]
def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()
if a == ['auth', 'setup-git']:
    pass
elif a[:2] == ['pr', 'list']:
    if s.get('advance'):
        git('--git-dir', os.environ['LOCAL_REMOTE'], 'update-ref',
            'refs/heads/master', s.pop('advance'))
    print(s.get('number', ''))
elif a[:2] == ['pr', 'create']:
    assert a[a.index('--head') + 1] == os.environ['PR_HEAD']
    assert a[a.index('--base') + 1] == 'master'
    assert a[a.index('--label') + 1] == 'autorelease: pending'
    s['number'] = 1
    s['body'] = pathlib.Path(a[a.index('--body-file') + 1]).read_text()
    print('https://example.invalid/pull/1')
elif a[:3] == ['pr', 'edit', '1']:
    s['body'] = pathlib.Path(a[a.index('--body-file') + 1]).read_text()
elif a[:3] == ['pr', 'view', '1']:
    assert a[3:] == ['--json', 'headRefOid', '--jq', '.headRefOid']
    current = git('ls-remote', 'origin', os.environ['PR_HEAD']).split()[0]
    print('c' * 40 if s.get('head_race') else current)
elif a[:3] == ['pr', 'merge', '1']:
    head = a[a.index('--match-head-commit') + 1]
    assert git('ls-remote', 'origin', os.environ['PR_HEAD']).split()[0] == head
    source = git('rev-parse', head + '^')
    assert git('ls-remote', 'origin', 'master').split()[0] == source
    assert '--auto' in a and '--merge' in a and '--admin' not in a
    assert a[a.index('--subject') + 1].startswith('merge: prepare release v')
    assert 'Release-Candidate: ' + source in s['body']
    s.setdefault('queued', []).append({'head': head, 'source': source})
else:
    raise AssertionError('Unexpected gh call: ' + repr(a))
state.write_text(json.dumps(s))
""")
    gh.chmod(0o755)
    scratch = harness / "runner"
    scratch.mkdir()
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_STATE", str(state))
    monkeypatch.setenv("LOCAL_REMOTE", str(remote))
    monkeypatch.setenv("RUNNER_TEMP", str(scratch))
    monkeypatch.setenv("GITHUB_OUTPUT", str(scratch / "output"))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("PR_HEAD", "release-please--branches--master--components--nora")
    monkeypatch.setenv("APP_SLUG", "synthetic-release")
    monkeypatch.setenv("APP_BOT_ID", "123")
    monkeypatch.setenv("EVENT", "push")
    # Real push events have no manual bump/candidate inputs.
    monkeypatch.setenv("RELEASE_BUMP", "")
    monkeypatch.setenv("RELEASE_CANDIDATE", "")
    return state


def run_plan_shell():
    step = next(
        s for s in release_workflow()["jobs"]["release-please"]["steps"] if s.get("id") == "plan"
    )
    return subprocess.run(  # noqa: S603 -- repository-owned workflow shell
        ["bash", "-c", step["run"]],
        capture_output=True,
        text=True,
        check=False,
    )


def test_concurrent_master_push_reprepares_same_version_and_complete_range(repo, workflow_repo):
    state = workflow_repo
    first = commit(repo, "nora/first.py", "fix: restore an existing behavior")
    run("push", "origin", "master")
    second = commit(repo, "nora/later.py", "fix: include a concurrent change")
    run("push", "origin", "HEAD:refs/heads/later")
    run("reset", "--hard", first)
    state.write_text(json.dumps({"advance": second}))
    result = run_plan_shell()
    assert result.returncode == 0, result.stderr
    assert "master advanced" in result.stdout
    assert not json.loads(state.read_text()).get("queued")
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.1"}
    # Actions can coalesce pushes; the next run picks current remote master,
    # replaces the same generated branch/PR and still plans 1.0.1, never 1.0.2.
    result = run_plan_shell()
    assert result.returncode == 0, result.stderr
    queued = json.loads(state.read_text())["queued"]
    assert len(queued) == 1 and queued[0]["source"] == second
    changelog = (repo / "CHANGELOG.md").read_text()
    assert (
        "restore an existing behavior" in changelog and "include a concurrent change" in changelog
    )
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.1"}
    # Validate the real generated commit/body against the required source check.
    from scripts.check_contribution_content import pr_problems

    item = {
        "title": "chore(master): release 1.0.1",
        "body": json.loads(state.read_text())["body"],
        "user": {"login": "release[bot]", "type": "Bot"},
        "labels": [{"name": "autorelease: pending"}],
        "base": {"sha": second},
        "head": {
            "sha": queued[0]["head"],
            "ref": os.environ["PR_HEAD"],
            "repo": {"full_name": REPO},
        },
    }
    commits = [
        {
            "sha": queued[0]["head"],
            "parents": [{"sha": second}],
            "commit": {"message": run("show", "-s", "--format=%B", "HEAD")},
        }
    ]
    assert pr_problems(item, REPO, release_login="release[bot]", commits=commits) == []


def test_workflow_refuses_replaced_pr_head_before_enabling_auto_merge(repo, workflow_repo):
    commit(repo, "docs/a.md", "docs: clarify setup")
    run("push", "origin", "master")
    workflow_repo.write_text(json.dumps({"head_race": True}))
    result = run_plan_shell()
    assert result.returncode == 1, result.stderr
    assert not json.loads(workflow_repo.read_text()).get("queued")


def test_workflow_refuses_stale_owner_source_before_preparing(repo, workflow_repo, monkeypatch):
    source = commit(repo, "docs/a.md", "docs: clarify setup")
    commit(repo, "docs/b.md", "docs: describe a later change")
    run("push", "origin", "master")
    monkeypatch.setenv("EVENT", "workflow_dispatch")
    monkeypatch.setenv("RELEASE_BUMP", "major")
    monkeypatch.setenv("RELEASE_CANDIDATE", source)
    result = run_plan_shell()
    assert result.returncode == 1, result.stderr
    assert json.loads((repo / ".release-please-manifest.json").read_text()) == {".": "1.0.0"}
    assert json.loads(workflow_repo.read_text()) == {}
