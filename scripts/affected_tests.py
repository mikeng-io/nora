"""Change-aware test selection for the iteration loop (fast-test-pass spec §3,
docs/superpowers/specs/2026-08-21-fast-test-pass-design.md).

Takes a base ref, reads the changed paths from git, and decides which pytest
targets are worth running for *that diff*. This backs `make test-affected`
only — it never touches `make gate`, which always runs the whole serial suite
(CLAUDE.md's four commands are unchanged by this feature).

The default base is `HEAD~1` — the latest commit only, not `origin/master`.
Diffing the whole branch was the original design and it has a real cost: a
branch that touches `nora/dreamer/**` or `nora/doctrine/**` once stays pinned
to `ALL` for every commit for the rest of its life, because that path never
leaves the cumulative diff — even a bare `config.toml` commit on top gets
`ALL`. Since `make gate` (not this script) is what guarantees every commit was
actually tested before it landed, re-flagging an already-gated commit on every
later diff buys nothing; only the latest commit needs judging here. Found and
confirmed independently by three sessions on 2026-08-21.

Fails closed by construction: any diff this script cannot map to a narrow rule
with confidence gets the literal string `ALL`, meaning "run everything." A
confident wrong subset is the one failure mode this script must never have
(spec §1) — a slow correct answer beats a fast wrong one.

The rule that matters most: `nora/doctrine/**` and `nora/dreamer/**` are
markdown, but the running application LOADS and HASHES them at startup/reload
— they are not documentation for selection purposes. That check runs first,
before the generic "changed paths are all markdown" rule, so a doctrine edit
can never be mistaken for a docs-only change and skip the doctrine tests.

A second, quieter version of the same mistake lives in the tests-only rule:
not every path under `tests/` is a file pytest collects tests from —
`conftest.py`, `builders.py`, `fakes.py`, `objectives.py` are imported by
dozens of test modules but contain none themselves. Running the changed path
verbatim for a shared fixture edit would select a target that collects zero
tests and exits green — a SILENT confident-wrong-subset, worse than the
doctrine case, which at least runs the wrong tests rather than none. So the
tests-only rule additionally requires every changed path to look like a file
pytest would actually collect from; one helper in the diff sends the whole
selection to `ALL`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALL = "ALL"

# Markdown that the application loads and hashes at runtime — NOT docs for
# selection purposes. Checked before the generic docs-only rule (see module
# docstring); getting this ordering backwards is exactly the failure this
# feature must not have.
_LOADED_MARKDOWN_PREFIXES = ("nora/doctrine/", "nora/dreamer/")

# Whole-directory docs trees: any file under these, any extension, is safe to
# skip — no Python imports anything here at runtime.
_DOCS_ONLY_PREFIXES = ("docs/", "release-fragments/", "design/")

_CONFIG_ONLY_FILES = {"config.toml", "config.example.toml"}
_CONFIG_TARGETS = ["tests/test_config.py", "tests/test_env_template.py"]


def _git(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local git only
        ["git", *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return [line for line in result.stdout.splitlines() if line]


def git_changed_paths(repo: Path, base: str) -> list[str]:
    """The paths that differ between HEAD and `base`'s merge-base with HEAD."""
    merge_base = _git(repo, "merge-base", base, "HEAD")[0]
    return _git(repo, "diff", "--name-only", f"{merge_base}...HEAD")


def _is_loaded_markdown(path: str) -> bool:
    return path.startswith(_LOADED_MARKDOWN_PREFIXES)


def _is_docs_only(path: str) -> bool:
    if _is_loaded_markdown(path):
        return False
    if path.endswith(".md"):
        return True
    return path.startswith(_DOCS_ONLY_PREFIXES)


def _is_config_only(path: str) -> bool:
    return path in _CONFIG_ONLY_FILES


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/")


def _is_collectible_test_file(path: str) -> bool:
    """Would pytest actually collect tests out of this file?

    Decided by pytest's own default collection convention — basename starts
    with `test_` and ends in `.py` — rather than a hand-maintained list of
    known helper filenames. A filename list goes stale the moment someone adds
    a new helper (that is the failure this rule exists to avoid repeating).

    This assumes the repo never overrides `python_files` in
    `[tool.pytest.ini_options]` (it doesn't, as of this writing — testpaths is
    set, python_files is not, so pytest's default applies). It will MISS a
    collectible module named outside that convention if such an override is
    ever added; the fix then is to update this rule, not to special-case
    filenames here.
    """
    name = Path(path).name
    return name.startswith("test_") and name.endswith(".py")


def select(changed: list[str]) -> list[str] | str:
    """Decide the pytest targets for a changed-path list.

    Returns either a list of pytest targets (possibly empty — meaning nothing
    needs to run) or the literal string `ALL`. Rules apply in the order below;
    first whole-diff match wins. Anything that doesn't cleanly match one rule
    for EVERY changed path falls through to `ALL` — the fail-closed default.
    """
    if not changed:
        return []

    # Rule 0 — highest priority, checked before every other rule (including
    # the docs-only rule below): loaded-at-runtime markdown anywhere in the
    # diff forces the full suite, no matter what else changed alongside it.
    if any(_is_loaded_markdown(p) for p in changed):
        return ALL

    if all(_is_docs_only(p) for p in changed):
        return []

    if all(_is_config_only(p) for p in changed):
        return _CONFIG_TARGETS

    if all(_is_test_path(p) for p in changed):
        # Not every path under tests/ is collectible (conftest.py, builders.py,
        # fakes.py, ...) — a shared-helper edit must not narrow to a target
        # that silently collects and runs nothing. See module docstring.
        if all(_is_collectible_test_file(p) for p in changed):
            return changed
        return ALL

    return ALL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", default="HEAD~1",
        help="ref to diff HEAD against (default: HEAD~1, the last commit only "
             "— NOT origin/master, which would re-flag every commit for the "
             "rest of the branch's life the first time it touches loaded "
             "markdown; see module docstring)",
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    try:
        changed = git_changed_paths(args.repo, args.base)
    except (RuntimeError, IndexError) as exc:
        # Can't determine the diff at all — fail closed, run everything, but
        # say why on stderr so it isn't a silent full run.
        print(f"affected_tests: cannot determine changed paths ({exc}); "
              f"falling back to ALL", file=sys.stderr)
        print(ALL)
        return 0

    result = select(changed)
    print(ALL if result == ALL else " ".join(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
