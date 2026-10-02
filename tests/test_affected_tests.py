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

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.affected_tests as affected
from scripts.affected_tests import ALL, NONE, git_changed_paths, main, select

REPO = Path(__file__).resolve().parents[1]
UNIVERSAL_DOC_READER = "tests/architecture/test_doc_references_resolve.py"


@pytest.fixture(autouse=True, scope="module")
def _scan_the_real_tests_once():
    """Scanning ~650 test modules takes ~2s and the real-tree cases below each
    ask. Memoise it for REPO only: the synthetic trees are scanned for real."""
    real = affected._scan_tests
    cache: dict[str, dict[str, affected._TestFacts]] = {}

    def memoised(repo: Path) -> dict[str, affected._TestFacts]:
        if repo != REPO:
            return real(repo)
        if "real" not in cache:
            cache["real"] = real(repo)
        return cache["real"]

    mp = pytest.MonkeyPatch()
    mp.setattr(affected, "_scan_tests", memoised)
    yield
    mp.undo()

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


def test_tests_only_change_selects_exactly_those_files():
    changed = ["tests/test_whereis.py", "tests/unit/test_blocks.py"]
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


@pytest.mark.parametrize("path", [
    # a path no rule names (the default is ALL, not "nothing")
    "some/new/top-level-thing.txt",
    "some_new_top_level_file",
    # application source and everything under nora/, any extension
    "nora/language/router.py",
    "nora/documents/catalog-prompt.md",     # loaded at RUNTIME (nora/documents/__init__.py)
    "nora/alembic/script.py.mako",
    "nora/anything/data.json",
    "nora/anything/data.txt",
    "nora/anything/data.yaml",
    # build, dependency and image definitions
    "pyproject.toml", "uv.lock", "Makefile", "Dockerfile", "docker-compose.yml",
    "alembic.ini", "requirements.txt",
    # the templates tests/builders.py and the loaders copy
    "config.example.toml", "policy.example.toml", "identity.example.toml",
    # the gitignored live config can never be in a diff; if it ever were,
    # nothing narrows it (the old config-only rule for it was dead code)
    "config.toml",
    # code and data outside tests/ that tests import or read
    "scripts/affected_tests.py", "scripts/mutations/context_assembly.json",
    "scripts/mutations/README.md", "evals/corpora/near_miss.yaml",
    "evals/harness.py", "app/src/main.tsx", "app/package.json",
    "deploy/computer/nora-computer", "deploy/computer/requirements.txt",
    "deploy/instagram-bridge/compose.fragment.yml",
    "deploy/prometheus/rules/nora.yml",
    ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/config.yml",
    ".githooks/commit-msg", ".mcp.json", ".gitignore",
    # an executable or script that merely lives in a docs tree
    "docs/tool.py", "design/render.sh",
])
def test_every_unmapped_path_class_selects_all(path):
    assert select([path]) == ALL


def test_mixed_docs_and_python_change_selects_all():
    """A change touching both a doc and a source file must not be narrowed to
    the doc-only subset — the .py file makes the whole diff unrecognised."""
    assert select(["docs/README.md", "nora/language/router.py"]) == ALL


def test_empty_diff_selects_nothing():
    assert select([]) == []


# --- documentation and data readers: found by scanning tests/, not listed ---
#
# Until 2026-10-02 a hand-kept table named the readers of AGENTS.md,
# CONTRIBUTING.md and the decision index, and every other `.md` got only the
# universal doc scan. README.md, DESIGN.md, docs/runbook.md, ARCHITECTURE.md,
# the deploy READMEs and release-fragments/README.md all have a test that
# opens them and none was selected. These cases name the real readers of the
# real tree, so a change to how a test opens a doc shows up here.


def _selected(path: str) -> list[str]:
    result = select([path])
    assert result != ALL, f"{path} unexpectedly selects ALL"
    assert isinstance(result, list)
    return result


def test_generic_docs_markdown_selects_the_universal_doc_reader():
    """test_doc_references_resolve.py scans every tracked `.md` for dangling
    path citations, so it reads any of them."""
    assert UNIVERSAL_DOC_READER in _selected("docs/foo.md")


@pytest.mark.parametrize(("path", "readers"), [
    ("README.md", ["tests/test_docs.py", "tests/test_release_commands.py"]),
    ("DESIGN.md", ["tests/test_docs.py"]),
    ("docs/runbook.md", ["tests/test_docs.py"]),
    ("ARCHITECTURE.md", ["tests/test_architecture_doc.py"]),
    ("AGENTS.md", ["tests/architecture/test_agent_skills.py",
                   "tests/test_architecture_doc.py"]),
    (".agents/skills/nora-land/SKILL.md", ["tests/architecture/test_agent_skills.py"]),
    ("CONTRIBUTING.md", ["tests/architecture/test_contribution_conventions.py"]),
    (".github/pull_request_template.md",
     ["tests/architecture/test_contribution_conventions.py"]),
    ("design/09-decision-index.md",
     ["tests/architecture/test_decision_index_ids_are_unique.py",
      "tests/architecture/test_decision_index_tests_exist.py",
      "tests/test_architecture_doc.py"]),
    ("deploy/computer/README.md",
     ["tests/test_deploy_assets.py", "tests/unit/test_nora_computer_readme.py",
      "tests/unit/test_nora_computer_script.py",
      "tests/unit/test_nora_computer_backup.py"]),
    ("release-fragments/README.md",
     ["tests/test_release_commands.py", "tests/test_release_notes.py"]),
    ("release-fragments/unreleased/x.json", ["tests/test_release_notes.py"]),
])
def test_a_doc_selects_every_test_that_opens_it(path, readers):
    selected = _selected(path)
    missing = [r for r in readers if r not in selected]
    assert not missing, f"{path} did not select {missing}"
    assert UNIVERSAL_DOC_READER in selected or not path.endswith(".md")


def test_mixed_markdown_and_docs_tree_change_unions_every_reader():
    selected = select([
        "docs/foo.md", "README.md", "design/09-decision-index.md",
        "release-fragments/unreleased/x.json",
    ])
    assert selected != ALL
    for reader in ("tests/test_docs.py", "tests/test_release_notes.py",
                   "tests/architecture/test_decision_index_tests_exist.py",
                   UNIVERSAL_DOC_READER):
        assert reader in selected
    assert selected == sorted(set(selected))


def test_a_docs_tree_data_file_selects_what_reads_it_not_nothing():
    """design/*.toml used to be "docs-only, no reader" and select []. The scan
    finds the tests that name the design tree; a file with no reader at all
    is NONE (next test), never silently the same as a failure."""
    assert _selected("design/10-preview-identity.toml") != []


def test_nora_skills_change_selects_all():
    """nora/skills/** goes straight to ALL, not a hand-picked reader list.

    Re-review on 2026-09-25 found tests/test_factory.py and
    tests/unit/test_generate_image_description.py read the real tree and were
    missing from the first cut's two-item list: a hand-kept reader list for
    this tree rots. Any path under the prefix qualifies, not just `.md`."""
    assert select(["nora/skills/navigation/SKILL.md"]) == ALL
    assert select(["nora/skills/navigation/some_asset.txt"]) == ALL


def test_nora_skills_mixed_with_something_else_still_selects_all():
    assert select(["nora/skills/navigation/SKILL.md", "README.md"]) == ALL


def test_markdown_reader_regression_is_caught_by_reverting_the_mapping():
    """Revert-proof: if a generic docs/ .md silently went back to mapping to
    nothing (the pre-#521 defect), this assertion is what turns red."""
    assert select(["docs/foo.md"]) != []
    assert select(["docs/foo.md"]) != ALL


# --- the scan itself, on a synthetic tree (so the real tests/ cannot hide a
# regression behind a reader that happens to exist) ---------------------------


def _synthetic(tmp_path: Path, **files: str) -> Path:
    """A repo-shaped tree whose tests/ holds exactly `files` (name -> source)."""
    (tmp_path / "tests").mkdir()
    for name, source in files.items():
        (tmp_path / "tests" / name).write_text(source)
    return tmp_path


def test_a_new_test_that_reads_a_new_doc_is_selected_for_that_doc(tmp_path):
    """The check the old table could not make: add a test that opens a doc and
    the doc selects that test, with no edit to the selector."""
    repo = _synthetic(tmp_path, **{
        "test_reads_notes.py":
            "from pathlib import Path\n"
            "ROOT = Path(__file__).parents[1]\n"
            "def test_it():\n"
            "    assert (ROOT / 'docs' / 'brand-new-notes.md').read_text()\n",
        "test_unrelated.py": "def test_other():\n    assert 1\n",
    })
    assert select(["docs/brand-new-notes.md"], repo) == [
        UNIVERSAL_DOC_READER, "tests/test_reads_notes.py",
    ]
    # the control: a doc nothing opens gets the universal reader only
    assert select(["docs/other.md"], repo) == [UNIVERSAL_DOC_READER]


def test_a_dynamically_built_name_is_caught_by_its_extension(tmp_path):
    repo = _synthetic(tmp_path, **{
        "test_builds_name.py":
            "def test_it(root):\n"
            "    for name in ('a', 'b'):\n"
            "        (root / f'{name}.md').read_text()\n",
    })
    assert select(["docs/a.md"], repo) == [
        UNIVERSAL_DOC_READER, "tests/test_builds_name.py",
    ]


def test_a_test_that_globs_a_directory_reads_every_file_in_it(tmp_path):
    repo = _synthetic(tmp_path, **{
        "test_walks_design.py":
            "from pathlib import Path\n"
            "def test_it(root):\n"
            "    assert list((root / 'design').rglob('*.toml'))\n",
    })
    assert select(["design/x/y.toml"], repo) == ["tests/test_walks_design.py"]
    # the glob alone (`*.toml`) also names a file in another docs tree
    assert select(["docs/y.toml"], repo) == ["tests/test_walks_design.py"]


def test_a_shared_helper_that_names_the_file_selects_all(tmp_path):
    """A helper is imported by an unknown set of tests; whoever imports it may
    read the file. Only ALL is safe."""
    repo = _synthetic(tmp_path, **{
        "helpers.py": "PATH = 'docs/shared-fixture.md'\n",
        "test_a.py": "def test_a():\n    assert 1\n",
    })
    assert select(["docs/shared-fixture.md"], repo) == ALL


def test_a_docstring_that_mentions_a_doc_does_not_read_it(tmp_path):
    repo = _synthetic(tmp_path, **{
        "test_prose.py":
            '"""See docs/prose-only.md for the reasoning."""\n'
            "def test_it():\n"
            '    """Also docs/prose-only.md."""\n'
            "    assert 1\n",
    })
    assert select(["docs/prose-only.md"], repo) == [UNIVERSAL_DOC_READER]


def test_a_docs_tree_data_file_nothing_reads_selects_nothing_explicitly(tmp_path):
    repo = _synthetic(tmp_path, **{"test_a.py": "def test_a():\n    assert 1\n"})
    assert select(["design/orphan.yaml"], repo) == []
    assert select(["release-fragments/unreleased/x.json"], repo) == [
        "tests/test_release_notes.py",
    ]


def test_a_test_module_that_cannot_be_parsed_is_assumed_to_read_every_doc(tmp_path):
    repo = _synthetic(tmp_path, **{"test_broken.py": "def (:\n"})
    selected = select(["docs/anything.md"], repo)
    assert selected != ALL
    assert "tests/test_broken.py" in selected


# --- tests/**: the existence check, renames and deletes ---------------------


def test_a_deleted_test_file_selects_all(tmp_path):
    """Its target would not exist (pytest exits 4 on it) and the tests that
    enumerate tests may now be wrong: ALL, not a target that cannot run."""
    repo = _synthetic(tmp_path)
    assert select(["tests/test_gone.py"], repo) == ALL


def test_a_new_test_file_selects_itself(tmp_path):
    repo = _synthetic(tmp_path, **{"test_new.py": "def test_x():\n    assert 1\n"})
    assert select(["tests/test_new.py"], repo) == ["tests/test_new.py"]


def test_a_test_fixture_selects_all():
    assert select(["tests/fixtures/context_assembly_step1_goldens.json"]) == ALL
    assert select(["tests/README.md"]) == ALL


def test_a_rename_reports_both_sides_so_the_old_path_is_judged(tiny_repo):
    (tiny_repo / "tests").mkdir()
    (tiny_repo / "tests" / "test_old.py").write_text("def test_x():\n    assert 1\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "add")
    _run_git(tiny_repo, "branch", "renamed-base")
    _run_git(tiny_repo, "mv", "tests/test_old.py", "tests/test_new.py")
    _run_git(tiny_repo, "commit", "-q", "-m", "rename")

    changed = git_changed_paths(tiny_repo, "renamed-base")

    assert sorted(changed) == ["tests/test_new.py", "tests/test_old.py"]
    assert select(changed, tiny_repo) == ALL      # the old side no longer exists


def test_a_non_ascii_path_is_reported_unquoted(tiny_repo):
    (tiny_repo / "docs" / "筆記.md").write_text("x\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "non-ascii")

    assert git_changed_paths(tiny_repo, "base-ref") == ["docs/筆記.md"]


# --- config.example.toml (review finding #2: tests/builders.py copies it for
# ~29 test modules' worth of config fixtures, so on its own it must be ALL,
# not the two narrow config tests) ------------------------------------------


def test_config_example_toml_alone_selects_all():
    assert select(["config.example.toml"]) == ALL


def test_config_example_toml_mixed_with_a_doc_selects_all():
    assert select(["README.md", "config.example.toml"]) == ALL


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
    assert capsys.readouterr().out.strip() == UNIVERSAL_DOC_READER


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

    (tiny_repo / "tests").mkdir()
    (tiny_repo / "tests" / "test_small.py").write_text("def test_x():\n    assert 1\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "a small test")

    rc = main(["--repo", str(tiny_repo)])  # no --base: exercises the default

    assert rc == 0
    assert capsys.readouterr().out.strip() == "tests/test_small.py"


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
    (tiny_repo / "committed.toml").write_text("a = 1\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "committed change")

    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")
    (tiny_repo / "new_untracked.py").write_text("x\n")

    changed = git_changed_paths(tiny_repo, "base-ref", include_worktree=True)

    assert sorted(changed) == ["committed.toml", "docs/readme.md", "new_untracked.py"]


def test_main_include_worktree_flag_reaches_an_uncommitted_edit(tiny_repo, capsys):
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo), "--include-worktree"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == UNIVERSAL_DOC_READER


def test_main_without_include_worktree_flag_misses_the_same_uncommitted_edit(
    tiny_repo, capsys,
):
    """The negative control for the test above: the same edit, no flag. An
    empty diff prints the explicit NONE, never an empty line."""
    (tiny_repo / "docs" / "readme.md").write_text("uncommitted edit\n")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == NONE


# --- the output contract: ANY failure is ALL, empty is never an answer ------
#
# 2026-10-02 audit: `main` caught only RuntimeError and IndexError. Anything
# else (git missing, a bug in a rule) escaped as a traceback with an empty
# stdout, and the Makefile read that empty line as "docs-only change, nothing
# to run" and returned 0 with zero tests run.


@pytest.mark.parametrize("failure", [
    OSError("git is not installed"),
    ValueError("a rule has a bug"),
    KeyError("surprise"),
    RuntimeError("git command failed"),
    IndexError("no merge base"),
])
def test_main_prints_all_and_says_why_for_any_failure_to_read_the_diff(
    monkeypatch, capsys, failure,
):
    def boom(*_a, **_k):
        raise failure

    monkeypatch.setattr(affected, "git_changed_paths", boom)

    rc = main(["--base", "origin/master"])

    out, err = capsys.readouterr()
    assert out.strip() == ALL
    assert rc == 0
    assert type(failure).__name__ in err and "falling back to ALL" in err


def test_main_prints_all_when_the_selection_rules_themselves_raise(
    monkeypatch, capsys,
):
    monkeypatch.setattr(affected, "git_changed_paths", lambda *a, **k: ["x"])

    def boom(*_a, **_k):
        raise TypeError("a rule has a bug")

    monkeypatch.setattr(affected, "select", boom)

    rc = main(["--base", "origin/master"])

    out, err = capsys.readouterr()
    assert (out.strip(), rc) == (ALL, 0)
    assert "TypeError" in err


def test_main_prints_all_when_git_cannot_be_run_at_all(monkeypatch, capsys):
    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(affected.subprocess, "run", no_git)

    assert main(["--base", "origin/master"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == ALL and "FileNotFoundError" in err


def test_the_script_run_with_no_git_on_the_path_prints_all(tiny_repo):
    """End to end through the real entry point, as `make` runs it: an empty
    PATH makes `git` unfindable. Before the fix this was a traceback, rc 1,
    empty stdout."""
    done = subprocess.run(  # noqa: S603 - this interpreter, this repo's script
        [sys.executable, str(REPO / "scripts" / "affected_tests.py"),
         "--base", "base-ref", "--repo", str(tiny_repo)],
        capture_output=True, text=True, env={"PATH": ""}, check=False,
    )
    assert done.stdout.strip() == ALL
    assert done.returncode == 0
    assert "falling back to ALL" in done.stderr


def test_an_empty_diff_prints_none_never_an_empty_line(tiny_repo, capsys):
    assert main(["--base", "base-ref", "--repo", str(tiny_repo)]) == 0
    assert capsys.readouterr().out == NONE + "\n"


# --- the Makefile recipe, run for real against a stub selector --------------
#
# `make test-affected` is what `make gate` runs. SELECTOR and PYTEST are the
# recipe's own variables, so the stub stands in for the script and `echo` for
# pytest while the recipe's shell logic is the production text.

_STUBS = {
    "exits-nonzero": "echo 'boom' >&2\nexit 3\n",
    "nonzero-with-none": "echo NONE\nexit 1\n",
    "prints-nothing": "exit 0\n",
    "prints-all": "echo ALL\n",
    "prints-none": "echo NONE\n",
    "prints-targets": "echo tests/a.py tests/b.py\n",
}


def _make_test_affected(tmp_path: Path, stub: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / f"{stub}.sh"
    script.write_text(_STUBS[stub])
    return subprocess.run(  # noqa: S603 - fixed argv, a stub script under tmp_path
        ["make", "-C", str(REPO), "--no-print-directory", "test-affected",
         f"SELECTOR=sh {script}", "PYTEST=echo PYTEST-RAN"],
        capture_output=True, text=True, check=False,
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
@pytest.mark.parametrize(("stub", "runs_all", "runs_targets", "says"), [
    ("exits-nonzero", True, False, "selector failed (exit 3)"),
    ("nonzero-with-none", True, False, "selector failed (exit 1)"),
    ("prints-nothing", True, False, "printed nothing"),
    ("prints-all", True, False, "running the full suite"),
    ("prints-none", False, False, "nothing to run"),
    ("prints-targets", False, True, "tests/a.py tests/b.py"),
])
def test_the_gate_recipe_runs_nothing_only_for_an_explicit_none(
    tmp_path, stub, runs_all, runs_targets, says,
):
    done = _make_test_affected(tmp_path, stub)

    assert done.returncode == 0, done.stderr
    assert says in done.stdout
    ran = [line for line in done.stdout.splitlines() if line.startswith("PYTEST-RAN")]
    if runs_all:
        assert ran == ["PYTEST-RAN"]
    elif runs_targets:
        assert ran == ["PYTEST-RAN tests/a.py tests/b.py"]
    else:
        assert ran == []


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_the_gate_recipe_fails_when_the_tests_it_runs_fail(tmp_path):
    """The recipe's exit status is pytest's: a red run is a red gate."""
    script = tmp_path / "all.sh"
    script.write_text("echo ALL\n")
    done = subprocess.run(  # noqa: S603 - fixed argv, a stub script under tmp_path
        ["make", "-C", str(REPO), "--no-print-directory", "test-affected",
         f"SELECTOR=sh {script}", "PYTEST=false"],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode != 0
