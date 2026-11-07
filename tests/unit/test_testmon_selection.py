"""The recorded-dependency class of scripts/affected_tests.py on a GENERATED
mini package — real pytest-testmon recording under tmp_path, real edits, real
select(). No production rows; the record is built by the test, so a fake never
has to answer for the tool.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.affected_tests import ALL, select


def _mini(root: Path) -> Path:
    """A package + tests, recorded once by the real tool."""
    (root / "pkg").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (root / "conftest.py").write_text("")
    (root / "pkg/__init__.py").write_text("")
    (root / "pkg/core.py").write_text(
        "def helper(x):\n    return x + 1\n\ndef other(y):\n    return y * 2\n"
    )
    (root / "pkg/settings.py").write_text("LIMIT = 10\n")
    (root / "pkg/unused.py").write_text("def never():\n    return 0\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from pkg.core import helper\n\ndef test_helper():\n    assert helper(1) == 2\n"
    )
    (tests / "test_other.py").write_text(
        "from pkg.core import other\n\ndef test_other():\n    assert other(2) == 4\n"
    )
    (tests / "test_settings.py").write_text(
        "from pkg.settings import LIMIT\n\ndef test_limit():\n    assert LIMIT == 10\n"
    )
    (tests / "test_reader.py").write_text(
        "from pathlib import Path\n\n"
        "def test_reads():\n    assert Path('docs/notes.md').read_text()\n"
    )
    (tests / "test_unrelated.py").write_text("def test_unrelated():\n    assert 1\n")
    (root / "docs").mkdir()
    (root / "docs/notes.md").write_text("# notes\n")
    return root


def _pytest(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - this interpreter, generated repo
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=repo, capture_output=True, text=True, check=False, timeout=120,
    )


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    repo = _mini(tmp_path / "repo")
    done = _pytest(repo, "--testmon")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (repo / ".testmondata").is_file()
    return repo


def test_a_function_body_edit_selects_the_modules_tests(recorded):
    (recorded / "pkg/core.py").write_text(
        "def helper(x):\n    return x + 2\n\ndef other(y):\n    return y * 2\n"
    )
    selected = select(["pkg/core.py"], recorded)
    assert selected != ALL
    assert "tests/test_core.py" in selected
    # pkg.core's other importer is selected too — the union answers at module
    # granularity; only modules nothing touches stay out
    assert "tests/test_other.py" in selected
    assert "tests/test_settings.py" not in selected
    assert "tests/test_unrelated.py" not in selected


def test_a_module_level_constant_edit_selects_the_importing_test(recorded):
    (recorded / "pkg/settings.py").write_text("LIMIT = 11\n")
    selected = select(["pkg/settings.py"], recorded)
    assert selected != ALL
    assert "tests/test_settings.py" in selected
    assert "tests/test_core.py" not in selected


def test_a_data_file_a_test_reads_selects_that_reader(recorded):
    selected = select(["docs/notes.md"], recorded)
    assert selected != ALL
    assert "tests/test_reader.py" in selected
    # control: a data file nothing reads selects nothing
    (recorded / "docs/orphan.md").write_text("# orphan\n")
    assert "tests/test_reader.py" not in select(["docs/orphan.md"], recorded)


def test_an_unread_path_selects_nothing(recorded):
    (recorded / "data").mkdir()
    (recorded / "data/blob.bin").write_bytes(b"\x00")
    assert select(["data/blob.bin"], recorded) == []


def test_a_py_change_nothing_depends_on_is_none_only_with_a_valid_record(recorded):
    assert select(["pkg/unused.py"], recorded) == []
    # the same diff without a usable record is ALL, never NONE
    (recorded / ".testmondata").write_bytes(b"junk")
    assert select(["pkg/unused.py"], recorded) == ALL


def test_a_missing_record_fails_closed_to_all(tmp_path):
    repo = _mini(tmp_path / "repo")
    assert not (repo / ".testmondata").exists()
    assert select(["pkg/core.py"], repo) == ALL


def test_a_corrupt_record_fails_closed_to_all(recorded):
    (recorded / ".testmondata").write_bytes(b"not a database")
    assert select(["pkg/core.py"], recorded) == ALL


def test_an_environment_mismatched_record_fails_closed_to_all(recorded):
    """The record keys (packages, python_version); a record from another
    environment selects everything, which select() reports as ALL."""
    db = sqlite3.connect(recorded / ".testmondata")
    db.execute("UPDATE environment SET python_version='0.0.0'")
    db.commit()
    db.close()
    assert select(["pkg/core.py"], recorded) == ALL


def test_a_nora_data_file_stays_all_even_with_a_valid_record(recorded):
    (recorded / "nora").mkdir()
    (recorded / "nora/doctrine.md").write_text("# doctrine\n")
    assert select(["nora/doctrine.md"], recorded) == ALL
