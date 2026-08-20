"""scripts/affected_tests.py — change-aware selection (fast-test-pass spec §3,
§6 item 4).

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


def test_docs_only_change_selects_nothing():
    assert select(["docs/foo.md", "README.md", "design/09-decision-index.md",
                    "release-fragments/unreleased/x.json"]) == []


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
    assert select(["config.example.toml"]) == [
        "tests/test_config.py", "tests/test_env_template.py",
    ]


def test_tests_only_change_selects_exactly_those_files():
    changed = ["tests/test_whereis.py", "tests/contract/test_record_store.py"]
    assert select(changed) == changed


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


def test_main_prints_nothing_for_a_docs_only_change(tiny_repo, capsys):
    (tiny_repo / "docs" / "readme.md").write_text("more docs\n")
    _run_git(tiny_repo, "add", ".")
    _run_git(tiny_repo, "commit", "-q", "-m", "docs change")

    rc = main(["--base", "base-ref", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_falls_back_to_all_when_the_base_ref_cannot_be_resolved(
    tiny_repo, capsys,
):
    rc = main(["--base", "no-such-ref-anywhere", "--repo", str(tiny_repo)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "ALL"
