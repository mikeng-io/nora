"""Change-aware test selection.

Takes a base ref, reads the changed paths from git, and decides which pytest
targets are worth running for *that diff*. This backs `make test`, `make
test-fast` and `make test-affected`: auto compares meaningful staged, unstaged
and untracked work against HEAD; a clean tree compares HEAD~1 to HEAD. Only
byte-exact generated untracked LFS post-hooks are ignored. Explicit --base
preserves ref selection. `make gate` pins origin/master with --include-worktree
for branch-level validation. `make test-all` and `make full-gate` always run
the whole serial suite, without this selector.

THE OUTPUT CONTRACT (one line on stdout, three shapes). `ALL` means run the
whole suite; `NONE` means a narrow rule found that nothing reads the diff, so
nothing runs; anything else is a space-separated list of pytest targets. The
EMPTY string is none of these: it means the script died before it could say,
and the Makefile treats it, and any nonzero exit status, as `ALL`; reading
empty as "docs-only, run nothing" would let a crash (git missing, an import
error, a bug in a rule) pass `make gate` with zero tests run. `main` turns ANY
exception into `ALL` on stdout and the reason on stderr.

Fails closed by construction: `select` classifies EACH changed path, and one
`ALL` anywhere in the diff makes the whole answer `ALL`. A confident wrong
subset is the one failure mode this script must never have — a slow correct
answer beats a fast wrong one.

TWO SOURCES OF DEPENDENCY, each used for what it can see, combined in `select`.

Python code: the recorded dependency map (`pytest-testmon`, `.testmondata`,
fetched by `scripts.testmon_record`). A full run records, per test, the code
blocks it executed as content checksums; `_testmon_targets` replays the
selection read-only and returns the files whose recorded blocks changed. A
missing, unreadable, corrupt or environment-mismatched record yields `ALL`,
never `NONE` — testmon's own environment check (installed packages + Python
version) also selects everything on mismatch, and a selection covering every
collectible test file IS `ALL`. The map compares content, so a record made at
any `master` commit selects correctly for a branch cut from another. Its known
floor: code-block granularity (a comment outside every tracked block may not
register) and it cannot see a file a test reads as data — the reader scan
owns those.

Everything else: the reader scan below. Its coverage is every non-`.py` path
outside `nora/` — documents, `app/`, `deploy/`, `scripts/` non-Python,
configs — plus `.py` files a test only runs as a subprocess (their dotted
module name, `scripts.release_plan`, counts as a literal, which testmon cannot
see). A path no test names selects nothing.

`stays ALL`, deliberately:

* any non-`.py` file under `nora/`: doctrine, skills and prompts are opened by
  loader code at a path computed at runtime, which neither source can see;
* `pyproject.toml`, `uv.lock`, `Makefile`, `Dockerfile`, `docker-compose.yml`,
  `.github/workflows/**`, any `conftest.py`, `tests/builders.py`: they change
  the environment or every test;
* a missing, unreadable, corrupt or environment-mismatched record;
* a selector crash (any exception in `main`).

The classes, in order:

* `.claude/skills/<name>` canonical links to in-repo `.agents/skills/<name>`
  select the agent-skill contract and the linked SKILL.md's document readers.
  Missing, redirected or escaping links remain unclassified (`ALL`).
* ANY path named in the `file` field of a `scripts/mutations/*.json` entry also
  selects `tests/architecture/test_mutation_tables_anchor_to_their_source.py`
  (read from the tables at select time, so a new table is covered with no edit
  here): the anchor test reads those files by a name held in data, which no
  literal in a test module shows the scan.
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
  tests that enumerate tests. README guides under `tests/` (excluding
  fixtures) are documents and fall through to the reader scan.
* `.py` outside `tests/` (`nora/`, `evals/`, `scripts/`, anywhere else): the
  recorded dependency map, plus the reader scan for the subprocess case.
* every other non-`.py` path: the reader scan — the tests that READ it, found
  by scanning `tests/` at select time (`_readers`), plus the universal
  doc-reference scan for a `.md`. A file read by a shared test helper is
  `ALL`. Byte-exact untracked Git LFS post-hooks are ignored as generated
  installation noise; modified/symlink/tracked hooks are not.

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
`f"{name}.md"`), is, for a `.py` P, P's dotted module name
(`scripts.release_plan`), or, only when the module traverses, is a directory
containing P. A shared helper or conftest (collects nothing, imported by an
unknown set of modules) that matches by ANY of those makes the answer `ALL`;
so a deploy README is `ALL` while `tests/unit/nora_computer_harness.py` walks
`deploy/computer`. It over-selects on purpose.

What the scan CANNOT see, stated because the omission is the whole risk:
- a path built with no literal naming the file, directory, glob or extension
  (`ROOT / name` with `name` read from a data file or computed). The mutation
  tables are the one such case found and have their own rule above;
- a read made by code a test calls (a `nora/` function that opens a file): a
  non-`.py` file so read sits under `nora/`, which is `ALL`, or is named by
  the test;
- an import through `importlib` or any computed string (`import_module(
  "pkg." + name)`): no literal matches and no static edge exists — a module
  so imported is NOT covered by selection, pinned by
  `test_a_computed_dynamic_import_is_a_documented_blind_spot`;
- a file that changes what pytest runs without being read by a test —
  build, workflow and environment files, which is why they are `ALL` by name.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
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
# non-`.py` files are runtime-loaded at computed paths, so `nora/**` non-`.py`
# is ALL outright; the other trees' `.md` narrows to its readers.
_CODE_TREE_PREFIXES = ("nora/", "evals/", "scripts/")

# The recorded dependency map (module docstring), one SQLite file per checkout.
_TESTMON_DB = ".testmondata"

# Files that change the environment or every test, so they never narrow — no
# test needs to read them for the rule to hold.
_ALWAYS_ALL_PATHS = frozenset({
    "pyproject.toml", "uv.lock", "Makefile", "Dockerfile",
    "docker-compose.yml", "alembic.ini", "tests/builders.py",
})
_ALWAYS_ALL_PREFIXES = (".github/workflows/", ".githooks/")

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


def generated_lfs_hook(repo: Path, path: str) -> bool:
    """Recognise only byte-exact, regular Git LFS post-hook installations."""
    name = path.removeprefix(".githooks/")
    if path != f".githooks/{name}" or name not in {"post-checkout", "post-commit", "post-merge"}:
        return False
    file = repo / path
    expected = (
        "#!/bin/sh\n"
        'command -v git-lfs >/dev/null 2>&1 || { printf >&2 "\\n%s\\n\\n" '
        f'"This repository is configured for Git LFS but \'git-lfs\' was not found on your path. '
        f"If you no longer wish to use Git LFS, remove this hook by deleting the '{name}' "
        "file in the hooks directory (set by 'core.hookspath'; "
        "usually '.git/hooks').\"; exit 2; }\n"
        f'git lfs {name} "$@"\n'
    )
    return not file.is_symlink() and file.is_file() and file.read_bytes() == expected.encode()


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
    not a stale commit). Auto selection also includes the worktree.

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
        return sorted(set(tracked) | {
            path for path in untracked if not generated_lfs_hook(repo, path)
        })
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
        # a subprocess run names the module, not the file: 'scripts.release_plan'
        if path.endswith(".py") and lit == path[:-3].replace("/", "."):
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


def _canonical(dotted: str) -> str:
    """The name an import binds to — `pkg/__init__.py` IS the module `pkg`,
    not a module named `pkg.__init__`."""
    return dotted.removesuffix(".__init__")


def _import_graph(repo: Path) -> dict[str, set[str]]:
    """Every `.py` outside `tests/` → the repo modules it imports.

    The record's collection-time blind spot (a module imported at collection
    is not in per-test coverage) closes only through the real import graph —
    transitive through production modules, not just test modules. Built once
    per select() call, only when a `.py` outside `tests/` changed.
    """
    files = [
        file for file in repo.rglob("*.py")
        if ".git" not in file.parts and ".venv" not in file.parts
        and not file.relative_to(repo).as_posix().startswith("tests/")
    ]
    modules = set()
    for file in files:
        dotted = _dotted(file.relative_to(repo).as_posix())
        modules.add(_canonical(dotted))
    graph: dict[str, set[str]] = {}
    for file in files:
        rel = file.relative_to(repo).as_posix()
        tree = ast.parse(file.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names |= _imported_names(node, rel.split("/")[:-1])
        # an imported name also binds every package above it: importing
        # `pkg.sub` executes `pkg`'s `__init__` first, so each ancestor that
        # is a module is a dependency too.
        deps = set()
        for name in names:
            parts = name.split(".")
            for i in range(1, len(parts) + 1):
                cand = ".".join(parts[:i])
                if cand in modules:
                    deps.add(cand)
        graph.setdefault(_canonical(_dotted(rel)), set()).update(deps)
    return graph


def _module_closure(root: str, graph: dict[str, set[str]]) -> set[str]:
    """Every module that imports `root`, transitively."""
    importers: dict[str, set[str]] = {}
    for mod, deps in graph.items():
        for dep in deps:
            importers.setdefault(dep, set()).add(mod)
    seen: set[str] = set()
    frontier = {root}
    while frontier:
        nxt = set()
        for mod in frontier:
            nxt |= importers.get(mod, set()) - seen
        seen |= nxt
        frontier = nxt
    return seen


def _importers(
    path: str, scan: dict[str, _TestFacts],
    graph: dict[str, set[str]] | None = None,
) -> tuple[set[str], bool]:
    """(collectible test modules that import `path`, directly or through
    production modules (when `graph` is given) and other test modules; a
    shared helper imports one of them). A test that imports another test
    module runs it as part of itself."""
    importers: set[str] = set()
    helper = False
    frontier = {_dotted(path)}
    if graph is not None:
        frontier = {_canonical(_dotted(path))}
        frontier |= _module_closure(_canonical(_dotted(path)), graph)
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


def _stays_all(path: str) -> bool:
    """Environment/build files that can change every test's result (module
    docstring's "stays ALL" list); any `conftest.py` anywhere qualifies."""
    return (
        path in _ALWAYS_ALL_PATHS
        or path.startswith(_ALWAYS_ALL_PREFIXES)
        or path.rsplit("/", 1)[-1] == "conftest.py"
    )


def needs_record(path: str) -> bool:
    """A path the recorded dependency map answers for: a `.py` outside
    `tests/` that is not an environment file."""
    return (
        path.endswith(".py")
        and not path.startswith("tests/")
        and not _stays_all(path)
    )


def _testmon_targets(repo: Path) -> set[str] | None:
    """Test files the recorded dependency map selects for the tree as it stands.

    None when the record is absent or unusable — the caller fails closed to
    ALL. Selection replays read-only (`--testmon-nocollect`); a corrupt record
    or a crashed collection is a nonzero exit here, also ALL upstream. An
    environment-mismatched record selects everything (the map's own packages +
    Python check), which `select` normalises to ALL.
    """
    if not (repo / _TESTMON_DB).is_file():
        return None
    try:
        done = subprocess.run(  # noqa: S603 - this interpreter, fixed pytest argv
            [
                sys.executable, "-m", "pytest", "-q", "--collect-only", "tests",
                "--testmon", "--testmon-nocollect", "--testmon-forceselect",
            ],
            cwd=repo, capture_output=True, text=True, check=False, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode:
        return None
    files = set()
    for line in done.stdout.splitlines():
        file = line.split("::", 1)[0].strip()
        if file.endswith(".py"):
            files.add(file)
    return files


def _fetch_record(repo: Path) -> str:
    """Try the shared artifact store for a matching record; fail closed."""
    script = Path(__file__).resolve().parent / "testmon_record.py"
    try:
        done = subprocess.run(  # noqa: S603 - this interpreter, repo script
            [sys.executable, str(script), "fetch", "--repo", str(repo)],
            capture_output=True, text=True, check=False, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable (fetch failed)"
    if done.returncode or not (repo / _TESTMON_DB).is_file():
        return "unavailable (no matching artifact)"
    return "fetched"


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


def _agent_skill_document(path: str, repo: Path) -> str | None:
    """Resolve only the repository's one-copy agent-skill directory links."""
    parts = Path(path).parts
    if len(parts) != 3 or parts[:2] != (".claude", "skills"):
        return None
    link = repo / path
    document = repo / ".agents" / "skills" / parts[2] / "SKILL.md"
    try:
        if (link.is_symlink()
                and os.readlink(link) == f"../../.agents/skills/{parts[2]}"
                and document.is_file()
                and document.resolve().is_relative_to(repo.resolve())):
            return document.relative_to(repo).as_posix()
    except (OSError, RuntimeError):
        pass
    return None


def _is_doc_or_data(path: str, repo: Path = ROOT) -> bool:
    """A documentation or data file outside the code trees, so one that the
    reader scan can answer for."""
    if _agent_skill_document(path, repo) is not None:
        return True
    if path.startswith(_CODE_TREE_PREFIXES):
        return False
    if path.startswith("tests/"):
        return Path(path).name == "README.md" and not path.startswith("tests/fixtures/")
    if path.endswith(".md"):
        return True
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return path.startswith(_DOCS_TREE_PREFIXES) and ext in _DATA_EXTENSIONS


def select(
    changed: list[str], repo: Path = ROOT, *, testmon: bool = True,
) -> list[str] | str:
    """Decide the pytest targets for a changed-path list.

    Returns either a list of pytest targets (possibly empty — meaning a rule
    found that nothing reads this diff) or the literal string `ALL`. Each path
    is classified on its own (module docstring); the answer is the union, and
    one `ALL` anywhere makes it `ALL`.

    `testmon=False` defers the recorded-map class entirely — `ci_plan` uses it
    because the record only exists inside the contracts job; it reports
    `needs_record` paths separately and the job re-runs this same function
    with the record fetched.
    """
    if not changed:
        return []

    targets: set[str] = set()
    scan: dict[str, _TestFacts] | None = None
    mutated = _mutation_files(repo)
    python_paths: list[str] = []

    for path in changed:
        skill_document = _agent_skill_document(path, repo)
        if skill_document is not None:
            targets.add("tests/architecture/test_agent_skills.py")
            path = skill_document
        elif path.startswith(".claude/skills/"):
            return ALL  # a link that is not the canonical copy is unclassified
        if path in mutated:
            targets.add(_MUTATION_ANCHOR_TEST)
        if path.startswith("tests/") and not _is_doc_or_data(path, repo):
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
        if _stays_all(path) or (path.startswith("nora/") and not path.endswith(".py")):
            return ALL
        if path.endswith(".py"):
            python_paths.append(path)
            continue
        # Every other non-.py path: the tests that read it, found by the scan.
        if scan is None:
            scan = _scan_tests(repo)
        readers, helper_reads = _readers(path, scan)
        if helper_reads:
            return ALL
        targets.update(readers)
        if path.endswith(".md"):
            targets.add(_ANY_MD_READER)

    if python_paths and testmon:
        selected = _testmon_targets(repo)
        if selected is None:
            return ALL
        if scan is None:
            scan = _scan_tests(repo)
        # An environment-mismatched record selects everything; that IS ALL.
        collectible = {rel for rel in scan if _is_collectible_test_file(rel)}
        if collectible and collectible <= selected:
            return ALL
        try:
            graph = _import_graph(repo)
        except Exception:  # noqa: BLE001 - unresolved importers is ALL, never a subset
            return ALL
        for path in python_paths:
            readers, helper_reads = _readers(path, scan, loose=False)
            importers, helper_imports = _importers(path, scan, graph)
            if helper_reads or helper_imports:
                return ALL
            # imports fill the record's collection-time blind spot: a module
            # imported only at collection (constants, types) is not in the
            # per-test execution coverage the record is built from.
            targets.update(readers | importers)
        targets.update(selected)

    if _META_TESTS in targets:
        # the directory target already runs every file in it
        targets = {t for t in targets if not t.startswith(_META_TESTS + "/")}
    return sorted(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="explicit diff base (default: HEAD for dirty work, HEAD~1 when clean)",
    )
    parser.add_argument(
        "--include-worktree", action="store_true",
        help="include staged, unstaged and untracked files with an explicit base",
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    try:
        base = args.base
        if base is None:
            changed = git_changed_paths(args.repo, "HEAD", include_worktree=True)
            staged = _git(args.repo, "diff", "--cached", "-z", "--name-only",
                          "--no-renames", "HEAD", nul=True)
            changed = sorted(set(changed) | set(staged))
            base = "HEAD" if changed else "HEAD~1"
            if not changed:
                changed = git_changed_paths(args.repo, base, include_worktree=True)
        else:
            changed = git_changed_paths(
                args.repo, base, include_worktree=args.include_worktree,
            )
        print(f"affected_tests: base={base}; worktree="
              f"{args.include_worktree or args.base is None}", file=sys.stderr)
        if any(needs_record(p) for p in changed) and not (
            args.repo / _TESTMON_DB
        ).is_file():
            print(
                f"affected_tests: dependency record {_fetch_record(args.repo)}",
                file=sys.stderr,
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
