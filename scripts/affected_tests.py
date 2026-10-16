"""Change-aware test selection.

Takes a base ref, reads the changed paths from git, and decides which pytest
targets are worth running for *that diff*. This backs BOTH `make test-affected`
(the iteration loop, default base `HEAD~1`, committed history only) AND
`make gate` (one run per PR, base `origin/master`, `--include-worktree` so an
uncommitted edit is judged too — see `git_changed_paths` below). `make
full-gate` is the one command this script never touches: it always runs the
whole serial suite, unconditionally, regardless of what changed.

The loop's default base is `HEAD~1` — the latest commit only, not `origin/master`. Diffing the
whole branch has a real cost: a branch that touches `nora/dreamer/**` or `nora/doctrine/**` once
stays pinned to `ALL` for every commit for the rest of its life, because that path never leaves
the cumulative diff. `make gate` runs on `origin/master` deliberately (it must judge everything
the branch changed, once, before it lands); the loop's `HEAD~1` default is what avoids re-flagging
an already-judged commit on every later diff.

THE OUTPUT CONTRACT (one line on stdout, three shapes). `ALL` means run the
whole suite; `NONE` means a narrow rule found that nothing reads the diff, so
nothing runs; anything else is a space-separated list of pytest targets. The
EMPTY string is none of these: it means the script died before it could say,
and the Makefile treats it, and any nonzero exit status, as `ALL`; reading
empty as "docs-only, run nothing" would let a crash (git missing, an import
error, a bug in a rule) pass `make gate` with zero tests run. `main` turns ANY
exception into `ALL` on stdout and the reason on stderr.

Fails closed by construction: `select` classifies EACH changed path, the
default for a path no rule names is `ALL`, and one `ALL` anywhere in the diff
makes the whole answer `ALL`. A confident wrong subset is the one failure mode
this script must never have — a slow correct answer beats a fast
wrong one. The classes, in order:

* `nora/**`, any extension: `ALL`. The application loads `nora/doctrine`,
  `nora/dreamer`, `nora/skills` and `nora/documents/catalog-prompt.md` at
  runtime, and every `.py` is imported by dozens of tests. A hand-kept reader
  list for these goes stale.
* `tests/**`: a collectible `test_*.py` that still exists selects itself, the
  whole of `tests/architecture/` (the meta tests: they read `tests/` itself, so
  an added, edited or deleted test can redden them — a new `tests/test_zz.py`
  reddens `test_tests_root_takes_no_new_files`, a new test that reads
  `config.example.toml` reddens `test_config_comes_from_the_template`), every
  test module that IMPORTS it (`tests/unit/test_cognition_failure_backoff.py`
  imports `test_cognition_session_lifecycle`), and the tests the reader scan
  below finds reading it. Any other path (`conftest.py`, `builders.py`,
  fixtures, a deleted or renamed-away test) is `ALL`: a helper is imported by
  many modules and collects nothing itself, and a deleted test can break the
  tests that enumerate tests.
* documentation and data files outside the code trees (`.md` anywhere but
  `nora/`, `evals/`, `scripts/`; any `.md/.txt/.json/.toml/.yaml/.yml` under
  `docs/`, `design/`): the tests that READ them, found by scanning `tests/` at
  select time (`_readers`), plus the universal doc-reference scan for a `.md`.
  A file read by a shared test helper is `ALL`.
* ANY path named in the `file` field of a `scripts/mutations/*.json` entry also
  selects `tests/architecture/test_mutation_tables_anchor_to_their_source.py`
  (read from the tables at select time, so a new table is covered with no edit
  here): the anchor test reads those files by a name held in data, which no
  literal in a test module shows the scan.
* everything else (`scripts/`, `evals/`, `app/` code, `deploy/` non-markdown,
  `pyproject.toml`, `uv.lock`, `Makefile`, `Dockerfile`, `docker-compose.yml`,
  `config.example.toml`, `policy.example.toml`, `identity.example.toml`,
  `.github/workflows/**`, `scripts/mutations/*.json`): `ALL`.

WHY A SCAN, NOT A TABLE. A table of which tests read which `.md` is updated
by whoever remembers, and a test added later that reads a doc leaves it
silently stale. A cross-check test for the table would only run when selected,
and a new test file selects only itself. The scan recomputes the answer from
the tests as they stand, so a new reader is found without anyone editing this
file.

What the scan reads (`_TestFacts`): every string literal in a test module
(docstrings excluded; literals over 200 characters excluded), what it imports,
and whether the module traverses a directory (`glob`/`rglob`/`iglob`/`iterdir`/
`walk`/`listdir`/`scandir` or `git ls-files`). A module reads path P if a
literal is P, ends P (`computer/README.md`), equals P's basename, is a glob P
matches (`*.md`), is P's extension (`.md`, always: it catches
`f"{name}.md"`), or, only when the module traverses, is a directory containing
P. A shared helper or conftest (collects nothing, imported by an unknown set of
modules) that matches by ANY of those makes the answer `ALL`; so a deploy
README is `ALL` while `tests/unit/nora_computer_harness.py` walks
`deploy/computer`. It over-selects on purpose.

What the scan CANNOT see, stated because the omission is the whole risk:
- a path built with no literal naming the file, directory, glob or extension
  (`ROOT / name` with `name` read from a data file or computed). The mutation
  tables are the one such case found and have their own rule above;
- a read made by code a test calls (a `nora/` function that opens a file): the
  file must then sit under `nora/`, which is `ALL`, or be named by the test;
- an import of a test module through `importlib` or a string.
That residual is why the doc classes only ever NARROW within docs trees and the
code trees are `ALL`.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The output contract (module docstring): ALL = run everything, NONE = a rule
# found nothing reads this diff, otherwise a list of targets. EMPTY is none of
# them and means failure.
ALL = "ALL"
NONE = "NONE"

# Code trees whose `.md` is not documentation for selection purposes. `nora/`
# is loaded or imported by the application, so every file in it, any extension,
# falls through every class below to the ALL default; naming it here is what
# keeps its `.md` (doctrine, dreamer, skills, documents/catalog-prompt.md) out
# of the doc classes.
_CODE_TREE_PREFIXES = ("nora/", "evals/", "scripts/")

# Whole-directory docs trees: a data file under them is looked up in the scan.
_DOCS_TREE_PREFIXES = ("docs/", "design/")
_DATA_EXTENSIONS = frozenset({".md", ".txt", ".json", ".toml", ".yaml", ".yml"})

# tests/architecture/test_doc_references_resolve.py scans every tracked `.md`
# (`git ls-files "*.md"`), so it reads any doc. The scan finds it too; naming
# it keeps the floor if that test ever changes how it enumerates.
_ANY_MD_READER = "tests/architecture/test_doc_references_resolve.py"

_TRAVERSAL_CALLS = frozenset(
    {"glob", "rglob", "iglob", "iterdir", "walk", "listdir", "scandir"},
)
_MAX_LITERAL = 200

# scripts/mutations/*.json entries name the source they mutate in a `file`
# field; tests/architecture/test_mutation_tables_anchor_to_their_source.py
# opens each of those paths by that data. A change to any of them selects it.
_MUTATION_TABLES = "scripts/mutations"
_MUTATION_ANCHOR_TEST = (
    "tests/architecture/test_mutation_tables_anchor_to_their_source.py"
)

# Every test under here reads `tests/` itself or the tree around it (the
# fitness functions); a change to any test selects the whole directory.
_META_TESTS = "tests/architecture"


def _git(repo: Path, *args: str, nul: bool = False) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local git only
        ["git", *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    sep = "\0" if nul else "\n"
    return [part for part in result.stdout.split(sep) if part]


def git_changed_paths(
    repo: Path, base: str, *, include_worktree: bool = False,
) -> list[str]:
    """The paths that differ between HEAD and `base`'s merge-base with HEAD.

    With `include_worktree=True`, the diff also covers uncommitted edits
    (staged and unstaged) and untracked files — the tree as it actually
    stands, not just the tree at HEAD. `make gate` uses this: it is the final
    check on a PR, and an edit that hasn't been committed yet is still
    untested if the gate silently skips it (it must judge the working tree,
    not a stale commit). The loop's `make test-affected` (default base
    `HEAD~1`) does NOT set this — it keeps judging committed history only.

    `--no-renames` lists BOTH sides of a rename: by default git reports only
    the new path, and the old one (a deleted test, a moved doc) is a change
    too. `-z` keeps a non-ASCII name unquoted and so recognisable.
    """
    merge_base = _git(repo, "merge-base", base, "HEAD")[0]
    if include_worktree:
        # One ref vs none compares that commit to the working tree, which
        # covers the index (staged) and the unstaged edits on top of it, as
        # well as every commit between merge_base and HEAD.
        tracked = _git(repo, "diff", "-z", "--name-only", "--no-renames",
                       merge_base, nul=True)
        untracked = _git(repo, "ls-files", "-z", "--others",
                         "--exclude-standard", nul=True)
        return sorted(set(tracked) | set(untracked))
    return _git(repo, "diff", "-z", "--name-only", "--no-renames",
                f"{merge_base}...HEAD", nul=True)


# --- the reader scan --------------------------------------------------------


@dataclass(frozen=True)
class _TestFacts:
    literals: frozenset[str]
    traverses: bool
    imports: frozenset[str] = frozenset()   # dotted module names, relative ones resolved


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _imported_names(node: ast.AST, package: list[str]) -> set[str]:
    """Dotted module names an import statement can bind, relative ones resolved
    against `package` (the importing file's directory as dotted parts)."""
    names: set[str] = set()
    if isinstance(node, ast.Import):
        names.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
            prefix = ".".join([*base, node.module] if node.module else base)
        else:
            prefix = node.module or ""
        if prefix:
            names.add(prefix)
            # `from tests.unit import test_x` imports a submodule
            names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def _facts_of(source: str, rel: str = "") -> _TestFacts:
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    package = rel.split("/")[:-1]
    literals: set[str] = set()
    imports: set[str] = set()
    traverses = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports |= _imported_names(node, package)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            value = node.value
            if "ls-files" in value:
                traverses = True
            if len(value) <= _MAX_LITERAL:
                literals.add(value)
        elif (isinstance(node, ast.Attribute) and node.attr in _TRAVERSAL_CALLS) or (
            isinstance(node, ast.Name) and node.id in _TRAVERSAL_CALLS
        ):
            traverses = True
    return _TestFacts(frozenset(literals), traverses, frozenset(imports))


def _scan_tests(repo: Path) -> dict[str, _TestFacts]:
    """Every `.py` under `tests/`, keyed by its repo-relative path.

    A module that cannot be parsed is treated as reading everything (the
    marker facts below): a file this scan cannot see into is a reader this
    scan cannot rule out.
    """
    facts: dict[str, _TestFacts] = {}
    for file in sorted((repo / "tests").rglob("*.py")):
        rel = file.relative_to(repo).as_posix()
        try:
            facts[rel] = _facts_of(file.read_text(encoding="utf-8"), rel)
        except (SyntaxError, UnicodeDecodeError, OSError):
            facts[rel] = _TestFacts(frozenset({"*.md", "*.json", "*.toml"}), True)
    return facts


def _normalise_literal(value: str) -> str:
    value = value.replace("\\", "/")
    while value.startswith(("./", "../")):
        value = value.split("/", 1)[1]
    return value.strip("/")


def _reads(facts: _TestFacts, path: str, *, loose: bool = True) -> bool:
    """Could a module with these facts open `path`? Over-approximate.

    `loose=False` keeps only the rules that NAME the file (the literal path,
    its basename, a trailing part of it) and drops the glob, traversed-directory
    and bare-extension rules. Used for a path under `tests/`: `*.py` and `.py`
    sit in a hundred modules that scan `nora/`, and counting them would select a
    dozen unrelated tests for every test edit; the tests that really scan
    `tests/` are `tests/architecture/`, selected whole.
    """
    name = path.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    for raw in facts.literals:
        lit = _normalise_literal(raw)
        if not lit:
            continue
        if lit in (path, name) or path.endswith("/" + lit):
            return True
        if not loose:
            continue
        if any(c in lit for c in "*?["):
            # a bare `*` or `**` names nothing: it matches every path
            if not re.sub(r"[*?\[\]/]", "", lit):
                continue
            if (fnmatch.fnmatch(name, lit) or fnmatch.fnmatch(path, lit)
                    or fnmatch.fnmatch(path, lit.removeprefix("**/"))):
                return True
        elif (facts.traverses and path.startswith(lit + "/")) or lit == suffix:
            return True
    return False


def _readers(
    path: str, scan: dict[str, _TestFacts], *, loose: bool = True,
) -> tuple[set[str], bool]:
    """(collectible test files that read `path`, a shared helper reads it)."""
    tests: set[str] = set()
    helper = False
    for rel, facts in scan.items():
        if rel == path:
            continue
        if not _reads(facts, path, loose=loose):
            continue
        if _is_collectible_test_file(rel):
            tests.add(rel)
        else:
            helper = True
    return tests, helper


def _dotted(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def _importers(path: str, scan: dict[str, _TestFacts]) -> tuple[set[str], bool]:
    """(collectible test modules that import `path`, directly or through other
    test modules; a shared helper imports one of them). A test that imports
    another test module runs it as part of itself."""
    importers: set[str] = set()
    helper = False
    frontier = {_dotted(path)}
    while frontier:
        nxt: set[str] = set()
        for rel, facts in scan.items():
            if rel == path or rel in importers or not (facts.imports & frontier):
                continue
            if _is_collectible_test_file(rel):
                importers.add(rel)
                nxt.add(_dotted(rel))
            else:
                helper = True
        frontier = nxt
    return importers, helper


def _mutation_files(repo: Path) -> set[str]:
    """Every path named by a `file` field in `scripts/mutations/*.json`. A table
    that cannot be read raises, and `main` turns that into ALL."""
    files: set[str] = set()
    for table in sorted((repo / _MUTATION_TABLES).glob("*.json")):
        entries = json.loads(table.read_text(encoding="utf-8"))
        files.update(
            entry["file"] for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("file"), str)
        )
    return files


# --- classification ---------------------------------------------------------


def _is_collectible_test_file(path: str) -> bool:
    """Would pytest actually collect tests out of this file?

    Decided by pytest's own default collection convention — basename starts
    with `test_` and ends in `.py` — rather than a hand-maintained list of
    known helper filenames. A filename list goes stale the moment someone adds
    a new helper.

    This assumes the repo never overrides `python_files` in
    `[tool.pytest.ini_options]` (it doesn't: testpaths is set, python_files is
    not). It will MISS a collectible module named outside that convention if
    such an override is ever added; the fix then is to update this rule.
    """
    name = Path(path).name
    return name.startswith("test_") and name.endswith(".py")


def _is_doc_or_data(path: str) -> bool:
    """A documentation or data file outside the code trees, so one that the
    reader scan can answer for."""
    if path.startswith("tests/") or path.startswith(_CODE_TREE_PREFIXES):
        return False
    if path.endswith(".md"):
        return True
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return path.startswith(_DOCS_TREE_PREFIXES) and ext in _DATA_EXTENSIONS


def select(changed: list[str], repo: Path = ROOT) -> list[str] | str:
    """Decide the pytest targets for a changed-path list.

    Returns either a list of pytest targets (possibly empty — meaning a rule
    found that nothing reads this diff) or the literal string `ALL`. Each path
    is classified on its own (module docstring); the answer is the union, and
    one `ALL` anywhere makes it `ALL`. A path no class names is `ALL`.
    """
    if not changed:
        return []

    targets: set[str] = set()
    scan: dict[str, _TestFacts] | None = None
    mutated = _mutation_files(repo)

    for path in changed:
        if path in mutated:
            targets.add(_MUTATION_ANCHOR_TEST)
        if path.startswith("tests/"):
            # Not every path under tests/ is collectible (conftest.py,
            # builders.py, fixtures, ...) and a deleted or renamed-away test
            # cannot be run: both are ALL, never a target that collects nothing.
            if not (_is_collectible_test_file(path) and (repo / path).is_file()):
                return ALL
            # An existing test: itself, the meta tests (they read tests/
            # itself), and whatever reads or imports it.
            if scan is None:
                scan = _scan_tests(repo)
            readers, helper_reads = _readers(path, scan, loose=False)
            importers, helper_imports = _importers(path, scan)
            if helper_reads or helper_imports:
                return ALL
            targets.update({path, _META_TESTS, *readers, *importers})
            continue
        if _is_doc_or_data(path):
            if scan is None:
                scan = _scan_tests(repo)
            readers, helper_reads = _readers(path, scan)
            if helper_reads:
                return ALL
            targets.update(readers)
            if path.endswith(".md"):
                targets.add(_ANY_MD_READER)
            continue
        return ALL

    if _META_TESTS in targets:
        # the directory target already runs every file in it
        targets = {t for t in targets if not t.startswith(_META_TESTS + "/")}
    return sorted(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", default="HEAD~1",
        help="ref to diff HEAD against (default: HEAD~1, the last commit only "
             "— NOT origin/master, which would re-flag every commit for the "
             "rest of the branch's life the first time it touches loaded "
             "markdown; see module docstring)",
    )
    parser.add_argument(
        "--include-worktree", action="store_true",
        help="also include uncommitted edits and untracked files in the diff "
             "(`make gate` passes this; the loop's default HEAD~1 run does "
             "not — see git_changed_paths)",
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    try:
        changed = git_changed_paths(
            args.repo, args.base, include_worktree=args.include_worktree,
        )
        result = select(changed, args.repo)
    except Exception as exc:  # noqa: BLE001 - fail closed on ANY failure
        # Whatever went wrong (no git, no diff base, a bug in a rule), the
        # honest answer is "I cannot scope this": run everything, and say why
        # on stderr so it is not a silent full run.
        print(f"affected_tests: cannot select targets "
              f"({type(exc).__name__}: {exc}); falling back to ALL",
              file=sys.stderr)
        print(ALL)
        return 0

    print(ALL if result == ALL else " ".join(result) or NONE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
