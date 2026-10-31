"""scripts/affected_tests.py — change-aware selection, backing both the
iteration loop and `make gate`.

`select()` is exercised directly (the real decision function, not a
reimplementation of its rules) with injected changed-path lists for the
per-rule cases, and via a real temporary git repo for the CLI wiring
(`git_changed_paths` + `main`'s stdout contract), so both the decision logic
and the git plumbing around it are covered.
"""

from __future__ import annotations

import os
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

# --- select(): the decision function, per rule ----------------------------


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


META = "tests/architecture"


def test_tests_only_change_selects_those_files_and_the_meta_tests():
    """A test change is not "exactly those files": the architecture
    tests read tests/ itself (a new tests/test_zz.py plus a release fragment
    must not pass the gate with test_tests_root_takes_no_new_files red)."""
    changed = ["tests/test_whereis.py", "tests/unit/test_blocks.py"]
    selected = select(changed)
    assert selected != ALL
    assert set(changed) <= set(selected) and META in selected


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
    """A genuine test-only change is itself plus the meta tests, never the whole
    suite."""
    selected = select(["tests/test_whereis.py"])
    assert selected != ALL and len(selected) <= 4
    assert {"tests/test_whereis.py", META} <= set(selected)


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
    # nothing narrows it
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
# Every `.md` is mapped to the tests that open it, not to a hand-kept table:
# README.md, DESIGN.md, docs/runbook.md, ARCHITECTURE.md, AGENTS.md,
# CONTRIBUTING.md, the decision index, the deploy READMEs and the
# release-fragment README each have a test that opens them. These cases name
# the real readers of the real tree, so a change to how a test opens a doc
# shows up here.


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
    ("README.md", ["tests/test_docs.py"]),
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
])
def test_a_doc_selects_every_test_that_opens_it(path, readers):
    selected = _selected(path)
    missing = [r for r in readers if r not in selected]
    assert not missing, f"{path} did not select {missing}"
    assert UNIVERSAL_DOC_READER in selected or not path.endswith(".md")


def test_mixed_markdown_and_docs_tree_change_unions_every_reader():
    selected = select([
        "docs/foo.md", "README.md", "design/09-decision-index.md",
    ])
    assert selected != ALL
    for reader in ("tests/test_docs.py",
                   "tests/architecture/test_decision_index_tests_exist.py",
                   UNIVERSAL_DOC_READER):
        assert reader in selected
    assert selected == sorted(set(selected))


def test_a_docs_tree_data_file_selects_what_reads_it_not_nothing():
    """design/*.toml is not "docs-only, no reader". The scan finds the tests
    that name the design tree; a file with no reader at all is NONE (next
    test), never silently the same as a failure."""
    assert _selected("design/10-preview-identity.toml") != []


def test_nora_skills_change_selects_all():
    """nora/skills/** goes straight to ALL, not a hand-picked reader list:
    tests that read the real tree (tests/test_factory.py,
    tests/unit/test_generate_image_description.py) would be missed, and a
    hand-kept reader list for this tree rots. Any path under the prefix
    qualifies, not just `.md`."""
    assert select(["nora/skills/navigation/SKILL.md"]) == ALL
    assert select(["nora/skills/navigation/some_asset.txt"]) == ALL


def test_nora_skills_mixed_with_something_else_still_selects_all():
    assert select(["nora/skills/navigation/SKILL.md", "README.md"]) == ALL


def test_markdown_reader_regression_is_caught_by_reverting_the_mapping():
    """Revert-proof: if a generic docs/ .md silently went back to mapping to
    nothing, this assertion is what turns red."""
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
    """The check a hand-kept table cannot make: add a test that opens a doc and
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
    assert select(["tests/test_new.py"], repo) == [META, "tests/test_new.py"]


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


# --- config.example.toml (tests/builders.py copies it into many modules' config
# fixtures, so on its own it must be ALL, not the two narrow config tests) ---


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
# actually touches those paths should. The default base is HEAD~1, not
# origin/master, which would diff the WHOLE branch.


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
    assert capsys.readouterr().out.split() == [META, "tests/test_small.py"]


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


# --- --include-worktree (`make gate` must judge the tree as it stands,
# including uncommitted edits and untracked files, not just the last commit) --


def test_default_behaviour_ignores_uncommitted_and_untracked_changes(tiny_repo):
    """Without --include-worktree (the loop's default), an uncommitted edit
    and an untracked file are invisible."""
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
# `main` must turn any failure (git missing, a bug in a rule), not just
# RuntimeError and IndexError, into ALL. An empty stdout would be read by the
# Makefile as "docs-only change, nothing to run" and return 0 with zero tests
# run.


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
    PATH makes `git` unfindable, and the output is still ALL, never a traceback
    with empty stdout."""
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
        capture_output=True, text=True, check=False, env=_clean_env(),
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
        capture_output=True, text=True, check=False, env=_clean_env(),
    )
    assert done.returncode != 0


# --- tests-only diffs, imports, helpers, tables ------------------------------


def _tree(root: Path, files: dict[str, str]) -> Path:
    """A repo-shaped tree from {relative path: source}, any depth."""
    for rel, source in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return root


_TRIVIAL = "def test_x():\n    assert 1\n"


def test_a_new_root_test_is_judged_by_the_test_that_forbids_new_root_tests(tmp_path):
    """Evasion (a): a new tests/test_zz.py + a release fragment must not select
    a few files and pass while `test_tests_root_takes_no_new_files` is red."""
    repo = _tree(tmp_path, {
        "tests/test_zz.py": _TRIVIAL,
        "tests/architecture/test_tests_root_takes_no_new_files.py": _TRIVIAL,
        "design/zz.yaml": "{}",
    })
    selected = select(["tests/test_zz.py", "design/zz.yaml"], repo)
    assert selected != ALL
    assert META in selected
    # the target is the directory, so the matching test is inside what runs
    assert (repo / META / "test_tests_root_takes_no_new_files.py").is_file()
    assert not any(t.startswith(META + "/") for t in selected)   # no double listing


def test_a_new_test_that_reads_the_config_template_reaches_its_architecture_test(tmp_path):
    """Evasion (b): a new tests/unit/test_zz.py reading config.example.toml +
    a fragment must not pass while `test_config_comes_from_the_template` is red."""
    repo = _tree(tmp_path, {
        "tests/unit/test_zz.py":
            "from pathlib import Path\n"
            "def test_x():\n    Path('config.example.toml').read_text()\n",
        "tests/architecture/test_config_comes_from_the_template.py": _TRIVIAL,
        "design/zz.yaml": "{}",
    })
    selected = select(["tests/unit/test_zz.py", "design/zz.yaml"], repo)
    assert META in selected and "tests/unit/test_zz.py" in selected


def test_the_two_evasion_diffs_end_to_end_through_the_cli(tiny_repo, capsys):
    (tiny_repo / "tests" / "architecture").mkdir(parents=True)
    (tiny_repo / "tests" / "architecture" / "test_meta.py").write_text(_TRIVIAL)
    (tiny_repo / "tests" / "test_zz.py").write_text(_TRIVIAL)
    (tiny_repo / "design").mkdir(parents=True, exist_ok=True)
    (tiny_repo / "design" / "zz.yaml").write_text("{}")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "a test and a design file")

    assert main(["--base", "base-ref", "--repo", str(tiny_repo)]) == 0

    out = capsys.readouterr().out.split()
    assert META in out and "tests/test_zz.py" in out


def test_an_edit_to_an_existing_test_also_selects_the_meta_tests(tmp_path):
    repo = _tree(tmp_path, {"tests/test_old.py": _TRIVIAL})
    assert META in select(["tests/test_old.py"], repo)


def test_a_test_that_imports_another_test_module_is_selected_with_it(tmp_path):
    """tests/unit/test_cognition_failure_backoff.py imports
    test_cognition_session_lifecycle, so it opens that file even though no
    literal in it names the path."""
    repo = _tree(tmp_path, {
        "tests/unit/test_base.py": _TRIVIAL,
        "tests/unit/test_absolute.py": "from tests.unit.test_base import test_x\n",
        "tests/unit/test_package.py": "from tests.unit import test_base\n",
        "tests/unit/test_relative.py": "from .test_base import test_x\n",
        "tests/unit/test_plain.py": "import tests.unit.test_base\n",
        "tests/unit/test_chain.py": "from tests.unit.test_absolute import test_x\n",
        "tests/unit/test_unrelated.py": "import os\n" + _TRIVIAL,
    })
    selected = select(["tests/unit/test_base.py"], repo)
    assert {"tests/unit/test_absolute.py", "tests/unit/test_package.py",
            "tests/unit/test_relative.py", "tests/unit/test_plain.py",
            "tests/unit/test_chain.py"} <= set(selected)
    assert "tests/unit/test_unrelated.py" not in selected


def test_the_real_importer_of_a_test_module_is_selected():
    selected = select(["tests/unit/test_cognition_session_lifecycle.py"])
    assert "tests/unit/test_cognition_failure_backoff.py" in selected


def test_a_helper_that_imports_a_test_module_selects_all(tmp_path):
    repo = _tree(tmp_path, {
        "tests/test_base.py": _TRIVIAL,
        "tests/helpers.py": "from tests.test_base import test_x\n",
    })
    assert select(["tests/test_base.py"], repo) == ALL


def test_a_test_that_names_another_test_file_reads_it(tmp_path):
    repo = _tree(tmp_path, {
        "tests/test_target.py": _TRIVIAL,
        "tests/test_reader.py": "NAME = 'test_target.py'\n" + _TRIVIAL,
        "tests/test_scanner.py": "import glob\nX = glob.glob('*.py')\n" + _TRIVIAL,
    })
    selected = select(["tests/test_target.py"], repo)
    assert "tests/test_reader.py" in selected
    # a bare `*.py` glob sits in a hundred modules that scan nora/: not a reader
    assert "tests/test_scanner.py" not in selected


# --- scripts/mutations: a data-driven path the scan cannot see ---------------


def test_a_file_named_in_a_mutation_table_selects_the_anchor_test(tmp_path):
    repo = _tree(tmp_path, {
        "scripts/mutations/t.json":
            '[{"name": "n", "file": ".github/pull_request_template.md",'
            ' "old": "a", "new": "b", "target": "t"}]',
    })
    selected = select([".github/pull_request_template.md"], repo)
    assert "tests/architecture/test_mutation_tables_anchor_to_their_source.py" in selected
    # a doc no table names does not
    assert "tests/architecture/test_mutation_tables_anchor_to_their_source.py" \
        not in select(["docs/other.md"], repo)


def test_the_real_pull_request_template_selects_the_anchor_test():
    assert "tests/architecture/test_mutation_tables_anchor_to_their_source.py" in \
        select([".github/pull_request_template.md"])


def test_an_unreadable_mutation_table_fails_closed_to_all(tmp_path, capsys):
    repo = _tree(tmp_path, {"scripts/mutations/broken.json": "{ not json"})
    with pytest.raises(ValueError):
        select(["docs/x.md"], repo)
    # and through main, which turns any failure into ALL
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "T")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "branch", "b")
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("x\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-q", "-m", "doc")
    assert main(["--base", "b", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == ALL


# --- helpers and conftests: any shape of read forces ALL ---------------------


@pytest.mark.parametrize(("helper_source", "why"), [
    ("def read(root, n):\n    return (root / f'{n}.md').read_text()\n",
     "builds a name from a suffix"),
    ("def read(root):\n    return list((root / 'docs').iterdir())\n",
     "iterates a directory"),
    ("from pathlib import Path\n"
     "def read():\n    return list(Path('.').rglob('*.md'))\n",
     "globs by extension"),
    ("NAME = 'runbook.md'\n", "names the file"),
])
def test_a_helper_or_conftest_that_could_read_a_doc_selects_all(
    tmp_path, helper_source, why,
):
    for helper in ("helpers.py", "conftest.py"):
        repo = _tree(tmp_path / helper, {
            f"tests/{helper}": helper_source,
            "tests/test_a.py": _TRIVIAL,
        })
        assert select(["docs/runbook.md"], repo) == ALL, f"{helper} {why}"


def test_a_helper_that_reads_nothing_near_a_doc_does_not_force_all(tmp_path):
    repo = _tree(tmp_path, {"tests/helpers.py": "X = 1\n", "tests/test_a.py": _TRIVIAL})
    assert select(["docs/runbook.md"], repo) == [UNIVERSAL_DOC_READER]


def test_a_deploy_readme_is_all_because_a_helper_walks_deploy_computer():
    """tests/unit/nora_computer_harness.py names deploy/computer and walks
    directories; a shared helper that might read the README makes it ALL."""
    assert select(["deploy/computer/README.md"]) == ALL


# --- traversal, path-matching and merge-base edge cases ----------------------


def test_a_path_with_a_space_in_its_name_is_found(tmp_path):
    repo = _tree(tmp_path, {
        "tests/test_spaces.py": "NAME = 'meeting notes.md'\n" + _TRIVIAL,
    })
    assert "tests/test_spaces.py" in select(["docs/meeting notes.md"], repo)


def test_git_ls_files_makes_a_module_a_traverser(tmp_path):
    repo = _tree(tmp_path, {
        "tests/test_lists.py":
            "import subprocess\n"
            "def test_x():\n"
            "    subprocess.run(['git', 'ls-files', 'design'])\n",
    })
    assert "tests/test_lists.py" in select(["design/x/y.yaml"], repo)


@pytest.mark.parametrize("call", [
    "root.iterdir()", "root.glob('x')", "root.rglob('x')", "os.walk(root)",
    "os.listdir(root)", "os.scandir(root)", "glob.iglob(root)",
])
def test_every_traversal_call_makes_a_module_a_traverser(tmp_path, call):
    repo = _tree(tmp_path, {
        "tests/test_walks.py":
            "import glob, os\n"
            "def test_x(root):\n"
            f"    list({call})\n"
            "    DIR = 'design'\n",
    })
    assert "tests/test_walks.py" in select(["design/x/y.yaml"], repo)


def test_a_bare_name_import_of_a_traversal_call_counts(tmp_path):
    repo = _tree(tmp_path, {
        "tests/test_walks.py":
            "from os import walk\n"
            "def test_x(root):\n    list(walk(root))\n    DIR = 'design'\n",
    })
    assert "tests/test_walks.py" in select(["design/x/y.yaml"], repo)


def test_a_directory_literal_without_traversal_is_not_a_reader(tmp_path):
    repo = _tree(tmp_path, {"tests/test_names.py": "DIR = 'design'\n" + _TRIVIAL})
    assert select(["design/x/y.yaml"], repo) == []


def test_a_trailing_part_of_the_path_names_the_file(tmp_path):
    """`'computer/README.md'` is neither the path, nor the basename."""
    repo = _tree(tmp_path, {
        "tests/test_trailing.py": "REL = 'guides/intro.md'\n" + _TRIVIAL,
    })
    assert "tests/test_trailing.py" in select(["docs/guides/intro.md"], repo)
    assert "tests/test_trailing.py" not in select(["docs/other/intro.md"], repo)


@pytest.mark.parametrize("path", ["evals/README.md", "scripts/NOTES.md",
                                  "evals/corpora/notes.md"])
def test_markdown_under_the_code_trees_is_all_not_docs(tmp_path, path):
    repo = _tree(tmp_path, {"tests/test_a.py": _TRIVIAL})
    assert select([path], repo) == ALL


def test_a_directory_named_like_a_test_is_not_a_test_file(tmp_path):
    """`exists()` would take a directory called test_x.py for a test target."""
    (tmp_path / "tests" / "test_dir.py").mkdir(parents=True)
    assert select(["tests/test_dir.py"], tmp_path) == ALL


def test_the_diff_is_against_the_merge_base_not_the_base_tip(tiny_repo):
    """The base moved on after the branch point: its new commit is not this
    branch's change, and must not appear in either mode."""
    _run_git(tiny_repo, "checkout", "-q", "-b", "feature")
    (tiny_repo / "docs" / "mine.md").write_text("mine\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "mine")
    _run_git(tiny_repo, "checkout", "-q", "base-ref")
    (tiny_repo / "docs" / "theirs.md").write_text("theirs\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "theirs")
    _run_git(tiny_repo, "checkout", "-q", "feature")

    assert git_changed_paths(tiny_repo, "base-ref") == ["docs/mine.md"]
    assert git_changed_paths(
        tiny_repo, "base-ref", include_worktree=True,
    ) == ["docs/mine.md"]


# --- `make gate` itself, dry-run ---------------------------------------------


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment minus what an enclosing `make gate` leaks into it: make
    puts its command-line variables in the environment of every recipe (here
    BASE=origin/master and INCLUDE_WORKTREE=--include-worktree, which a nested
    `make test-affected` reads through `BASE ?=`) and in MAKEFLAGS."""
    dropped = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES",
               "BASE", "INCLUDE_WORKTREE", "SELECTOR", "PYTEST"}
    return {**{k: v for k, v in os.environ.items() if k not in dropped}, **(extra or {})}


def _dry_run(target: str, env: dict[str, str] | None = None) -> list[str]:
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["make", "-n", "--no-print-directory", "-C", str(REPO), target],
        capture_output=True, text=True, check=True, env=_clean_env(env),
    )
    return done.stdout.splitlines()


def _first(lines: list[str], needle: str) -> int:
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert hits, f"{needle!r} not in the dry run:\n" + "\n".join(lines)
    return hits[0]


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_gate_runs_its_static_steps_then_the_selector_against_origin_master():
    lines = _dry_run("gate")
    ruff = _first(lines, "uv run ruff check")
    mypy = _first(lines, "uv run mypy")
    imports = _first(lines, "uv run lint-imports")
    selector = _first(lines, "scripts/affected_tests.py")
    assert max(ruff, mypy, imports) < selector
    assert "--base origin/master" in lines[selector]
    assert "--include-worktree" in lines[selector]
    assert "--base HEAD~1" not in "\n".join(lines)


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_gate_cannot_be_redirected_from_the_environment():
    """An exported SELECTOR / PYTEST / BASE / INCLUDE_WORKTREE in a shell must
    not change what the gate runs (only `make X=...` may)."""
    lines = _dry_run("gate", env={
        "SELECTOR": "echo NONE", "PYTEST": "true",
        "BASE": "HEAD~1", "INCLUDE_WORKTREE": "",
    })
    selector = _first(lines, "affected_tests.py")
    assert "uv run python scripts/affected_tests.py" in lines[selector]
    assert "--base origin/master --include-worktree" in lines[selector]
    assert "echo NONE" not in "\n".join(lines)


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_full_gate_and_the_loop_default_are_unchanged():
    full = _dry_run("full-gate")
    assert [line.split()[:3] for line in full[:3]] == [
        ["uv", "run", "ruff"], ["uv", "run", "mypy"], ["uv", "run", "lint-imports"],
    ]
    assert full[3] == "cd deploy/instagram-bridge && go vet ./... && go test ./..."
    assert full[4] == "uv run python -m scripts.pytest_gate --workers 0"
    assert not any("affected_tests" in line for line in full)

    loop = _dry_run("test-affected")
    selector = loop[_first(loop, "affected_tests.py")]
    assert "--base HEAD~1" in selector and "--include-worktree" not in selector
