"""scripts/affected_tests.py — change-aware selection (fast-test-pass spec §3,
§6 item 4; PR #521 extended it to back `make gate` too, and the fixes below
are the coverage that PR's review found missing).

`select()` is exercised directly (the real decision function, not a
reimplementation of its rules) with injected changed-path lists for the
per-rule cases, and via a real temporary git repo for the CLI wiring
(`git_changed_paths` + `main`'s stdout contract), so both the decision logic
and the git plumbing around it are covered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.affected_tests import ALL, git_changed_paths, main, select

# --- select(): the decision function, per spec §3's table -----------------


def test_doctrine_markdown_change_selects_all():
    """The rule that matters most: nora/doctrine/** is markdown, but the
    application loads and hashes it — it must NOT be treated as docs-only."""
    assert select(["nora/doctrine/conduct.md"]) == ALL


def test_dreamer_markdown_change_selects_all():
    assert select(["nora/dreamer/doctrine/dreamer.md"]) == ALL


def test_doctrine_change_outranks_docs_only_even_when_mixed_with_real_docs():
    # All paths "look like" markdown; only one of them is runtime-loaded.
    # The doctrine rule must win over the docs-only rule for the whole diff.
    assert select(["docs/README.md", "nora/doctrine/voice.md"]) == ALL


def test_config_only_change_selects_config_tests():
    assert select(["config.toml"]) == [
        "tests/test_config.py", "tests/test_env_template.py",
    ]


def test_tests_only_change_selects_exactly_those_files():
    changed = ["tests/test_whereis.py", "tests/contract/test_record_store.py"]
    assert select(changed) == changed


def test_a_non_collectible_helper_alone_selects_all():
    """conftest.py is imported by every test module but contains no tests of
    its own — running it verbatim would collect and run nothing, and exit
    green. That is worse than the doctrine miss: silent, not just wrong."""
    assert select(["tests/conftest.py"]) == ALL


def test_a_shared_test_helper_alone_selects_all():
    assert select(["tests/builders.py"]) == ALL


def test_a_real_test_file_mixed_with_a_helper_selects_all():
    """The helper must dominate: one non-collectible path anywhere in an
    otherwise-tests-only diff sends the whole selection to ALL."""
    changed = ["tests/test_whereis.py", "tests/builders.py"]
    assert select(changed) == ALL


def test_a_real_test_file_alone_still_selects_narrowly():
    """The fix above must not blunt the feature: a genuine test-only change
    still gets the narrow selection."""
    assert select(["tests/test_whereis.py"]) == ["tests/test_whereis.py"]


def test_unrecognised_path_selects_all():
    assert select(["some/new/top-level-thing.txt"]) == ALL


def test_nora_source_change_selects_all():
    assert select(["nora/language/router.py"]) == ALL


def test_pyproject_change_selects_all():
    assert select(["pyproject.toml"]) == ALL


def test_mixed_docs_and_python_change_selects_all():
    """A change touching both a doc and a source file must not be narrowed to
    the doc-only subset — the .py file makes the whole diff unrecognised."""
    assert select(["docs/README.md", "nora/language/router.py"]) == ALL


def test_empty_diff_selects_nothing():
    assert select([]) == []


# --- markdown readers (review finding #1: every .md outside doctrine/dreamer
# used to map to "no tests"; tests read plenty of it) ----------------------


def test_generic_docs_markdown_still_selects_the_universal_doc_reader():
    """A `.md` this script names no specific reader for (a plain docs/ page,
    README.md, ...) is NOT "no tests": test_doc_references_resolve.py scans
    every tracked `.md` file for dangling path citations, so it is a real
    reader of any of them."""
    assert select(["docs/foo.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
    ]
    assert select(["README.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
    ]


def test_non_markdown_docs_tree_file_still_selects_nothing():
    """A non-`.md` file under design/ with no known reader (a *_index.yaml, a
    preview .toml) is genuinely untested — this is the one case that still
    selects the empty list, unlike a `.md` under the same tree."""
    assert select(["design/10-preview-identity.toml"]) == []
    assert select(["design/ambient-loop/_index.yaml"]) == []


def test_nora_skills_skill_md_selects_its_readers():
    """tests/test_skill_content.py loads every shipped nora/skills/*/SKILL.md
    whole (SkillStore(SKILLS_DIR).available()); the image-generation skill's
    SKILL.md is additionally hashed by name in
    tests/unit/test_canonical_self_reference.py (skill_self_handle(SKILL))."""
    assert select(["nora/skills/navigation/SKILL.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
        "tests/test_skill_content.py",
        "tests/unit/test_canonical_self_reference.py",
    ]


def test_compute_smith_markdown_selects_only_the_universal_doc_reader():
    """nora/compute/smith/**/*.md (doctrine.md, the smith skills' own
    SKILL.md files) has its own test suite under nora/compute/smith/tests/,
    which is not on `testpaths` and is never collected here — no module under
    tests/ was found (by grep) to read a nora/compute/smith path by name, so
    only the universal .md reader applies."""
    assert select(["nora/compute/smith/doctrine.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
    ]
    assert select(["nora/compute/smith/skills/maps/SKILL.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
    ]


def test_agents_md_selects_its_readers():
    """AGENTS_MD = ROOT / "AGENTS.md" in test_agent_skills.py (every skill +
    the operational constraints table); test_architecture_doc.py separately
    asserts specific phrases are present in it."""
    assert select(["AGENTS.md"]) == [
        "tests/architecture/test_agent_skills.py",
        "tests/architecture/test_doc_references_resolve.py",
        "tests/test_architecture_doc.py",
    ]


def test_agent_skills_directory_selects_its_reader():
    assert select([".agents/skills/nora-land/SKILL.md"]) == [
        "tests/architecture/test_agent_skills.py",
        "tests/architecture/test_doc_references_resolve.py",
    ]


def test_contribution_convention_docs_select_their_reader():
    assert select([".github/pull_request_template.md"]) == [
        "tests/architecture/test_contribution_conventions.py",
        "tests/architecture/test_doc_references_resolve.py",
    ]
    assert select(["CONTRIBUTING.md"]) == [
        "tests/architecture/test_contribution_conventions.py",
        "tests/architecture/test_doc_references_resolve.py",
    ]


def test_decision_index_selects_its_readers():
    """ids-are-unique + tests-exist parse the table rows; test_architecture_
    doc.py separately asserts specific decision ids are present in the text."""
    assert select(["design/09-decision-index.md"]) == [
        "tests/architecture/test_decision_index_ids_are_unique.py",
        "tests/architecture/test_decision_index_tests_exist.py",
        "tests/architecture/test_doc_references_resolve.py",
        "tests/test_architecture_doc.py",
    ]


def test_release_fragment_json_selects_release_notes():
    assert select(["release-fragments/unreleased/x.json"]) == [
        "tests/test_release_notes.py",
    ]


def test_release_fragment_markdown_selects_both_readers():
    """release-fragments/README.md is both a release-fragments/** path (so
    tests/test_release_notes.py's `load_fragments` neighbourhood applies) and
    a tracked .md (so the universal doc-reference scan applies too)."""
    assert select(["release-fragments/README.md"]) == [
        "tests/architecture/test_doc_references_resolve.py",
        "tests/test_release_notes.py",
    ]


def test_mixed_markdown_and_docs_tree_change_unions_every_reader():
    changed = [
        "docs/foo.md", "README.md", "design/09-decision-index.md",
        "release-fragments/unreleased/x.json",
    ]
    assert select(changed) == [
        "tests/architecture/test_decision_index_ids_are_unique.py",
        "tests/architecture/test_decision_index_tests_exist.py",
        "tests/architecture/test_doc_references_resolve.py",
        "tests/test_architecture_doc.py",
        "tests/test_release_notes.py",
    ]


def test_markdown_reader_regression_is_caught_by_reverting_the_mapping():
    """Revert-proof: if nora/skills/*/SKILL.md silently went back to mapping
    to nothing (the pre-#521 defect), this assertion is what turns red."""
    assert select(["nora/skills/navigation/SKILL.md"]) != []
    assert select(["nora/skills/navigation/SKILL.md"]) != ALL


# --- config.example.toml (review finding #2: tests/builders.py copies it for
# ~29 test modules' worth of config fixtures, so on its own it must be ALL,
# not the two narrow config tests) ------------------------------------------


def test_config_example_toml_alone_selects_all():
    assert select(["config.example.toml"]) == ALL


def test_config_example_toml_mixed_with_config_toml_selects_all():
    assert select(["config.toml", "config.example.toml"]) == ALL


# --- git plumbing + CLI, against a real temporary git repo -----------------


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, tmp-path-local git only
        ["git", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("hello\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "branch", "base-ref")
    return repo


def test_git_changed_paths_reads_the_real_diff(tiny_repo: Path):
    (tiny_repo / "docs" / "readme.md").write_text("hello again\n")
    (tiny_repo / "new.txt").write_text("x\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "change")

    changed = git_changed_paths(tiny_repo, "base-ref")

    assert sorted(changed) == ["docs/readme.md", "new.txt"]


def test_main_prints_all_for_an_unmapped_change(tiny_repo, capsys):
    (tiny_repo / "new.txt").write_text("x\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "change")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "ALL"


def test_main_prints_the_universal_doc_reader_for_a_docs_only_change(tiny_repo, capsys):
    (tiny_repo / "docs" / "readme.md").write_text("more docs\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "docs change")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == \
        "tests/architecture/test_doc_references_resolve.py"


def test_main_falls_back_to_all_when_the_base_ref_cannot_be_resolved(
    tiny_repo, capsys,
):
    rc = main(["--base", "no-such-ref-anywhere", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "ALL"


# --- default --base: incremental, not branch-lifetime ----------------------
#
# A branch that touched nora/dreamer/** or nora/doctrine/** once must not stay
# pinned to ALL for every later commit on that branch — only the commit that
# actually touches those paths should. Confirmed independently by three
# sessions on 2026-08-21 hitting `make test-affected` reporting "unmapped or
# broad change" on a trivial config.toml-only commit, because the default base
# (origin/master) diffed the WHOLE branch, not the latest commit.


def test_default_base_does_not_re_flag_an_earlier_dreamer_commit(
    tiny_repo, capsys,
):
    (tiny_repo / "nora" / "dreamer").mkdir(parents=True)
    (tiny_repo / "nora" / "dreamer" / "doctrine.md").write_text("x\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "touch dreamer doctrine")

    (tiny_repo / "config.toml").write_text("a = 1\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "trivial config change")

    rc = main(["--repo", str(tiny_repo)])  # no --base: exercises the default

    assert rc == 0
    assert capsys.readouterr().out.strip() == \
        "tests/test_config.py tests/test_env_template.py"


def test_default_base_still_selects_all_when_the_latest_commit_is_dreamer(
    tiny_repo, capsys,
):
    (tiny_repo / "nora" / "dreamer").mkdir(parents=True)
    (tiny_repo / "nora" / "dreamer" / "doctrine.md").write_text("x\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "touch dreamer doctrine")

    rc = main(["--repo", str(tiny_repo)])  # no --base: exercises the default

    assert rc == 0
    assert capsys.readouterr().out.strip() == "ALL"


# --- --include-worktree (review finding #3: `make gate` must judge the tree
# as it stands, including uncommitted edits and untracked files, not just the
# last commit) ---------------------------------------------------------------


def test_default_behaviour_ignores_uncommitted_and_untracked_changes(tiny_repo):
    """Without --include-worktree (the loop's default), an uncommitted edit
    and an untracked file are invisible — unchanged from before this flag."""
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")
    (tiny_repo / "untracked.txt").write_text("x\n")

    changed = git_changed_paths(tiny_repo, "base-ref")

    assert changed == []


def test_include_worktree_sees_an_uncommitted_edit(tiny_repo):
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")

    changed = git_changed_paths(tiny_repo, "base-ref", include_worktree=True)

    assert changed == ["docs/readme.md"]


def test_include_worktree_sees_an_untracked_file(tiny_repo):
    (tiny_repo / "new_untracked.py").write_text("x\n")

    changed = git_changed_paths(tiny_repo, "base-ref", include_worktree=True)

    assert changed == ["new_untracked.py"]


def test_include_worktree_combines_committed_uncommitted_and_untracked(tiny_repo):
    (tiny_repo / "config.toml").write_text("a = 1\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "committed change")

    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")
    (tiny_repo / "new_untracked.py").write_text("x\n")

    changed = git_changed_paths(tiny_repo, "base-ref", include_worktree=True)

    assert sorted(changed) == ["config.toml", "docs/readme.md", "new_untracked.py"]


def test_main_include_worktree_flag_reaches_an_uncommitted_edit(tiny_repo, capsys):
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo), "--include-worktree"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == \
        "tests/architecture/test_doc_references_resolve.py"


def test_main_without_include_worktree_flag_misses_the_same_uncommitted_edit(
    tiny_repo, capsys,
):
    """The negative control for the test above: the same edit, no flag."""
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
