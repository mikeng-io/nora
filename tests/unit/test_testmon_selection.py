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

from scripts.affected_tests import ALL, _import_graph, select


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
    (root / "pkg/api.py").write_text("from pkg.settings import LIMIT\n")
    (root / "pkg/unused.py").write_text("def never():\n    return 0\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text(
        "from pkg.api import LIMIT\n\ndef test_api():\n    assert LIMIT == 10\n"
    )
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
    assert "tests/test_api.py" in selected  # reaches it through pkg.api
    assert "tests/test_core.py" not in selected


def test_a_constant_edit_follows_imports_through_production_modules(recorded):
    """`from pkg.api import LIMIT` never names pkg.settings — only the
    transitive production import graph selects this test (the record cannot
    see a collection-time import)."""
    (recorded / "pkg/settings.py").write_text("LIMIT = 111\n")
    selected = select(["pkg/settings.py"], recorded)
    assert selected != ALL
    assert "tests/test_api.py" in selected
    # and the selection really is the transitive one, not everything
    assert "tests/test_unrelated.py" not in selected


def test_unresolvable_importers_fail_closed_to_all(recorded, monkeypatch):
    monkeypatch.setattr(
        "scripts.affected_tests._import_graph",
        lambda repo: 1 / 0,
    )
    assert select(["pkg/settings.py"], recorded) == ALL


def test_an_invalid_python_file_is_all_not_a_subset(recorded):
    (recorded / "pkg/broken.py").write_text("def broken(:\n")
    assert select(["pkg/settings.py"], recorded) == ALL


def _shape_repo(
    root: Path, init: str, test_src: str, api: str = "from pkg import LIMIT\n",
) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (root / "conftest.py").write_text("")
    (root / "pkg/__init__.py").write_text(init)
    (root / "pkg/settings.py").write_text("LIMIT = 10\n")
    (root / "pkg/api.py").write_text(api)
    (root / "pkg/mod.py").write_text("Y = 1\n")
    (root / "pkg/unused.py").write_text("X = 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_target.py").write_text(test_src)
    (root / "tests/test_unrelated.py").write_text("def test_u():\n    assert 1\n")
    done = _pytest(root, "--testmon")
    assert done.returncode == 0, done.stdout + done.stderr
    return root


_INIT_REEXPORT = "from .settings import LIMIT\n"
_INIT_ABSOLUTE = "from pkg.settings import LIMIT\n"
_TARGET_DIRECT = "from pkg.api import LIMIT\n\ndef test_target():\n    assert LIMIT == 10\n"
_TARGET_IMPORT_MOD = "import pkg.api\n\ndef test_target():\n    assert pkg.api.LIMIT == 10\n"
_TARGET_FROM_PKG_MOD = "from pkg import api\n\ndef test_target():\n    assert api.LIMIT == 10\n"
_TARGET_FROM_PKG = "from pkg import LIMIT\n\ndef test_target():\n    assert LIMIT == 10\n"
_TARGET_BODY = "def test_target():\n    from pkg.api import LIMIT\n    assert LIMIT == 10\n"
_TARGET_TYPE_CHECKING = (
    "from typing import TYPE_CHECKING\n"
    "if TYPE_CHECKING:\n    from pkg.api import LIMIT\n"
    "else:\n    LIMIT = 10\n"
    "def test_target():\n    assert LIMIT == 10\n"
)
_TARGET_TRY = (
    "try:\n    from pkg.api import LIMIT\n"
    "except ImportError:\n    LIMIT = 10\n"
    "def test_target():\n    assert LIMIT == 10\n"
)
_TARGET_RELATIVE_API = "from pkg.api import LIMIT\n\ndef test_target():\n    assert LIMIT == 10\n"


@pytest.mark.parametrize(("init", "test_src", "edit_path"), [
    (_INIT_REEXPORT, _TARGET_DIRECT, "pkg/settings.py"),
    (_INIT_ABSOLUTE, _TARGET_DIRECT, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_IMPORT_MOD, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_FROM_PKG_MOD, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_FROM_PKG, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_BODY, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_TYPE_CHECKING, "pkg/settings.py"),
    (_INIT_REEXPORT, _TARGET_TRY, "pkg/settings.py"),
    # the reviewer case: __init__ re-exports, api binds from the package
    ("from .settings import LIMIT\n", _TARGET_FROM_PKG, "pkg/settings.py"),
    # a constant rebound in __init__ itself
    ("LIMIT = 10\n", _TARGET_FROM_PKG, "pkg/__init__.py"),
], ids=[
    "direct", "absolute_init", "import_mod", "from_pkg_mod",
    "from_pkg_reexport", "body_import", "type_checking", "try_except",
    "reviewer_reexport", "init_constant",
])
def test_import_shapes_select_the_reader(tmp_path, init, test_src, edit_path):
    repo = _shape_repo(tmp_path / "repo", init, test_src)
    target = repo / edit_path
    target.write_text(target.read_text().replace("LIMIT = 10", "LIMIT = 111"))
    selected = select([edit_path], repo)
    assert selected != ALL
    assert "tests/test_target.py" in selected
    assert "tests/test_unrelated.py" not in selected


def test_a_submodule_import_reaches_the_parent_init(tmp_path):
    """`import pkg.settings` binds `pkg` too — editing `pkg/__init__.py`
    selects a test whose chain reaches it only through the parent edge."""
    repo = _shape_repo(
        tmp_path / "repo", "LIMIT = 10\n", _TARGET_IMPORT_MOD,
        api="import pkg.settings\nLIMIT = pkg.settings.LIMIT\n",
    )
    (repo / "pkg/__init__.py").write_text("LIMIT = 111\n")
    selected = select(["pkg/__init__.py"], repo)
    assert selected != ALL
    assert "tests/test_target.py" in selected


def test_a_test_importing_the_package_reaches_its_init(tmp_path):
    """Round-3 case: `import pkg.mod` binds `pkg` — editing pkg/__init__.py
    must select a test that only reads pkg.X through the parent."""
    repo = _shape_repo(
        tmp_path / "repo", "X = 10\n",
        "import pkg.mod\n\ndef test_target():\n    assert pkg.X == 10\n",
        api="import pkg\n",
    )
    (repo / "pkg/__init__.py").write_text("X = 111\n")
    selected = select(["pkg/__init__.py"], repo)
    assert selected != ALL
    assert "tests/test_target.py" in selected


_EXTRA_SHAPES = [
    # (init, api, test_src, edit_path)
    ("X = 10\n", "import pkg\n",
     "import pkg.mod\n\ndef t():\n    assert pkg.X == 10\n", "pkg/__init__.py"),
    ("X = 10\n", "Y = 1\n",
     "import pkg as p\nimport pkg.mod\n\ndef t():\n    assert p.X == 10\n",
     "pkg/__init__.py"),
    ("from .settings import LIMIT\n", "from pkg import LIMIT\n",
     "from pkg.api import LIMIT\n\ndef t():\n    assert LIMIT == 10\n",
     "pkg/settings.py"),
    ("from .settings import LIMIT\n", "from pkg import LIMIT\n",
     "from pkg import *\n\ndef t():\n    assert LIMIT == 10\n",
     "pkg/settings.py"),
    ("LIMIT = 10\n", "import pkg.settings\nLIMIT = pkg.settings.LIMIT\n",
     "from pkg.api import LIMIT\n\ndef t():\n    assert LIMIT == 10\n",
     "pkg/__init__.py"),
]


@pytest.mark.parametrize(("init", "api", "test_src", "edit_path"), _EXTRA_SHAPES,
                         ids=["alias_pkg", "import_as", "direct2", "star", "via_init_chain"])
def test_more_import_shapes_select_the_reader(
    tmp_path, init, api, test_src, edit_path,
):
    """Each shape the reviewers ran selects the reader — one resolver, one
    ancestor rule, no per-call-site patching."""
    repo = _shape_repo(tmp_path / "repo", init, test_src, api=api)
    target = repo / edit_path
    target.write_text(target.read_text().replace("= 10", "= 111"))
    selected = select([edit_path], repo)
    assert selected != ALL
    assert "tests/test_target.py" in selected


def test_a_file_the_graph_cannot_name_is_all(recorded):
    """A bare-root module (no package can import it) has unresolvable
    importers — ALL, never an empty subset."""
    (recorded / "helper.py").write_text("X = 1\n")
    assert select(["helper.py"], recorded) == ALL


def test_every_repo_package_root_resolves():
    """Every importable top-level package in THIS repo resolves in the one
    graph — a src/ or unknown layout would raise (ALL), never a subset."""
    root = Path(__file__).resolve().parents[2]
    graph = _import_graph(root)
    for top in ("nora", "evals", "scripts", "tests", "app", "deploy",
                "showreel"):
        assert top in graph
    # `site/` is stdlib-shadowed — no interpreter can import it, so it is
    # correctly excluded rather than resolved to the wrong package
    assert "site" not in graph


def test_a_computed_dynamic_import_is_a_documented_blind_spot(tmp_path):
    """importlib.import_module() with a COMPUTED name carries no literal the
    scan can match and no static edge the graph can follow — selection does
    NOT cover it. This test pins the limitation; it does not pretend."""
    repo = _shape_repo(
        tmp_path / "repo", _INIT_REEXPORT,
        "import importlib\n"
        "LIMIT = importlib.import_module('pkg.' + 'settings').LIMIT\n"
        "def test_target():\n    assert LIMIT == 10\n",
    )
    (repo / "pkg/settings.py").write_text("LIMIT = 111\n")
    assert "tests/test_target.py" not in select(["pkg/settings.py"], repo)


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


def test_a_test_in_a_namespace_subdir_is_all_not_silently_dropped(tmp_path):
    """Under a REGULAR tests package grimp drops a dir without __init__.py —
    the completeness guard turns a scanned module the resolver missed into
    ALL, never a silent []."""
    repo = _shape_repo(tmp_path / "repo", _INIT_REEXPORT, _TARGET_DIRECT)
    (repo / "tests/__init__.py").write_text("")  # tests is a real package
    (repo / "tests/sub").mkdir()
    (repo / "tests/sub/test_target.py").write_text(
        "from pkg.settings import LIMIT\n\ndef test_sub():\n    assert LIMIT == 10\n"
    )
    done = _pytest(repo, "--testmon")
    assert done.returncode == 0
    (repo / "pkg/settings.py").write_text("LIMIT = 111\n")
    assert select(["pkg/settings.py"], repo) == ALL
    # positive control: the SAME layout with __init__.py narrows correctly
    (repo / "tests/sub/__init__.py").write_text("")
    done = _pytest(repo, "--testmon")
    assert done.returncode == 0
    selected = select(["pkg/settings.py"], repo)
    assert selected != ALL
    assert "tests/sub/test_target.py" in selected


def test_every_collectible_test_dir_is_a_package():
    """The real repo can never slide into namespace test dirs: every tests/
    dir holding a collectible test file has __init__.py, so the completeness
    guard does not turn every production edit into ALL."""
    root = Path(__file__).resolve().parents[2]
    offenders = [
        d for d in (root / "tests").rglob("*") if d.is_dir()
        and any(f.name.startswith("test_") and f.suffix == ".py"
                for f in d.glob("*.py"))
        and not (d / "__init__.py").is_file()
    ]
    assert offenders == []
