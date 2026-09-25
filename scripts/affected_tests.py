"""Change-aware test selection (fast-test-pass spec §3,
docs/superpowers/specs/2026-08-21-fast-test-pass-design.md).

Takes a base ref, reads the changed paths from git, and decides which pytest
targets are worth running for *that diff*. This backs BOTH `make test-affected`
(the iteration loop, default base `HEAD~1`, committed history only) AND
`make gate` (one run per PR, base `origin/master`, `--include-worktree` so an
uncommitted edit is judged too — see `git_changed_paths` below). `make
full-gate` is the one command this script never touches: it always runs the
whole serial suite, unconditionally, regardless of what changed.

The loop's default base is `HEAD~1` — the latest commit only, not
`origin/master`. Diffing the whole branch was the original design and it has a
real cost: a branch that touches `nora/dreamer/**` or `nora/doctrine/**` once
stays pinned to `ALL` for every commit for the rest of its life, because that
path never leaves the cumulative diff — even a bare `config.toml` commit on
top gets `ALL`. `make gate` runs on `origin/master` deliberately (it must
judge everything the branch changed, once, before it lands); the loop's
`HEAD~1` default is what avoids re-flagging an already-judged commit on every
later diff. Found and confirmed independently by three sessions on 2026-08-21.

Fails closed by construction: any diff this script cannot map to a narrow rule
with confidence gets the literal string `ALL`, meaning "run everything." A
confident wrong subset is the one failure mode this script must never have
(spec §1) — a slow correct answer beats a fast wrong one. `make gate` inherits
that fail-closed property directly: an unmapped or broad diff runs the whole
suite through `make gate` exactly as it would have before this script existed,
so `gate` is never weaker than `full-gate` on a change it cannot scope.

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

A third version of the same mistake lived in the docs rule until 2026-09-25:
"every `.md` outside doctrine/dreamer is docs-only, run nothing" is false —
tests DO read specific docs (`tests/test_skill_content.py` loads every
`nora/skills/*/SKILL.md`, `tests/architecture/test_agent_skills.py` loads
`AGENTS.md` and `.agents/skills/**`, `tests/architecture/test_doc_references_
resolve.py` scans literally every tracked `.md` file for dangling path
citations). Mapping every markdown path to the empty selection meant `make
gate` — which now runs through this script — would pass on a broken doc these
tests would have caught. `_markdown_targets` below carries the known specific
readers (found by grepping `tests/` for each path, not guessed) and, for any
`.md` this script cannot name a narrower reader for, falls back to
`test_doc_references_resolve.py` rather than the empty list — never "no tests
at all" for a change to a file something in `tests/` demonstrably reads.
`config.example.toml` got the same correction: `tests/builders.py` copies it
for the whole config-fixture layer (~29 test modules), so a change to it alone
is `ALL`, not the two narrow config tests — those stay mapped from `config.toml`.
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

# Whole-directory docs trees, any extension: no reader was found by grepping
# `tests/` for any non-.md file under these (design/*.toml, design/*.yaml,
# _index.yaml — checked 2026-09-25). A `.md` file under any of these is NOT
# covered by this set — see `_markdown_targets`, which runs first for `.md`
# paths and never returns "no tests" for one.
_DOCS_ONLY_PREFIXES = ("docs/", "release-fragments/", "design/")

_CONFIG_TARGETS = ["tests/test_config.py", "tests/test_env_template.py"]

# The universal fallback for any tracked `.md` this table doesn't name a
# narrower reader for: this test scans EVERY tracked `.md` file for dangling
# path citations (`git ls-files "*.md"` — tests/architecture/
# test_doc_references_resolve.py's own docstring), so it is a real reader of
# any doc, not a guess.
_ANY_MD_READER = "tests/architecture/test_doc_references_resolve.py"

# Specific markdown readers, found by grepping `tests/` for each path (never
# guessed — a docstring MENTION of a path is not a read of it; only a
# `.read_text()` / `Path(...)` construction on the real repo path counts).
# Prefix entries match any path starting with the prefix; exact entries match
# only that literal path. First matching entry per changed path wins; a path
# matching none of these still gets `_ANY_MD_READER` if it ends in `.md`
# (see `_markdown_targets`).
_MD_PREFIX_READERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Every shipped skill is loaded and validated whole
    # (tests/test_skill_content.py: SkillStore(SKILLS_DIR).available()); the
    # image-generation skill's own SKILL.md is additionally hashed by name
    # (tests/unit/test_canonical_self_reference.py: skill_self_handle(SKILL)).
    ("nora/skills/", ("tests/test_skill_content.py",
                       "tests/unit/test_canonical_self_reference.py")),
    # nora/compute/smith/**/*.md (doctrine.md, the smith skills' own
    # SKILL.md files) has its OWN test suite under nora/compute/smith/tests/,
    # which is not on `testpaths` and is never collected by the tests/
    # tree — checked 2026-09-25, no `tests/` module reads a
    # `nora/compute/smith` path by name. Only the universal `.md` reader
    # applies; naming that suite here would be a build-time guess this
    # script cannot ground.
    (".agents/skills/", ("tests/architecture/test_agent_skills.py",)),
)
_MD_EXACT_READERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # AGENTS_MD = ROOT / "AGENTS.md" in test_agent_skills.py (lists every
    # skill + the operational constraints table); test_architecture_doc.py
    # separately asserts specific phrases are present in it.
    ("AGENTS.md", ("tests/architecture/test_agent_skills.py",
                    "tests/test_architecture_doc.py")),
    (".github/pull_request_template.md",
     ("tests/architecture/test_contribution_conventions.py",)),
    ("CONTRIBUTING.md", ("tests/architecture/test_contribution_conventions.py",)),
    # design/09-decision-index.md: ids-are-unique + tests-exist parse the
    # table rows; test_architecture_doc.py separately asserts specific
    # decision ids are present in the file's text.
    ("design/09-decision-index.md",
     ("tests/architecture/test_decision_index_ids_are_unique.py",
      "tests/architecture/test_decision_index_tests_exist.py",
      "tests/test_architecture_doc.py")),
)

# release-fragments/**: JSON fragments (not markdown) are loaded whole by
# `load_fragments` (tests/test_release_notes.py). Any extension under this
# prefix maps here; a `.md` file under it (release-fragments/README.md,
# release-fragments/released/V2/NOTES.md) ALSO gets `_ANY_MD_READER` via the
# markdown rule, since both apply independently.
_RELEASE_FRAGMENTS_PREFIX = "release-fragments/"
_RELEASE_FRAGMENTS_TARGET = "tests/test_release_notes.py"


def _git(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local git only
        ["git", *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return [line for line in result.stdout.splitlines() if line]


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
    `HEAD~1`) does NOT set this — it keeps judging committed history only,
    unchanged from before this flag existed.
    """
    merge_base = _git(repo, "merge-base", base, "HEAD")[0]
    if include_worktree:
        # One ref vs none compares that commit to the working tree, which
        # covers the index (staged) and the unstaged edits on top of it, as
        # well as every commit between merge_base and HEAD.
        tracked = _git(repo, "diff", "--name-only", merge_base)
        untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
        return sorted(set(tracked) | set(untracked))
    return _git(repo, "diff", "--name-only", f"{merge_base}...HEAD")


def _is_loaded_markdown(path: str) -> bool:
    return path.startswith(_LOADED_MARKDOWN_PREFIXES)


def _markdown_targets(path: str) -> list[str]:
    """The pytest targets that actually read this `.md` file's content.

    Never empty: a path this table can't name a narrower reader for still
    gets `_ANY_MD_READER`, which is a real, grepped-for reader of every
    tracked `.md` file (see module docstring). Only call this for a path that
    `path.endswith(".md")`.
    """
    targets = {_ANY_MD_READER}
    for prefix, readers in _MD_PREFIX_READERS:
        if path.startswith(prefix):
            targets.update(readers)
    for exact, readers in _MD_EXACT_READERS:
        if path == exact:
            targets.update(readers)
    return sorted(targets)


def _is_docs_only_non_markdown(path: str) -> bool:
    """A non-`.md` file under a whole-directory docs tree, with no reader
    found in `tests/` (design/*.toml, design/*.yaml, ...). `.md` files are
    handled separately by `_markdown_targets`, never by this function."""
    return path.startswith(_DOCS_ONLY_PREFIXES)


def _is_release_fragment(path: str) -> bool:
    return path.startswith(_RELEASE_FRAGMENTS_PREFIX)


def _is_config_only(path: str) -> bool:
    # config.example.toml is deliberately NOT here: tests/builders.py copies
    # it for the whole config-fixture layer, so a change to it alone must be
    # ALL, not this narrow pair. See module docstring.
    return path == "config.toml"


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
    a changed path can match more than one rule (a `.md` under `design/` gets
    both the markdown targets and nothing else, since `design/` itself maps
    non-`.md` files only). Anything left over after every rule below has had
    its say falls through to `ALL` — the fail-closed default.
    """
    if not changed:
        return []

    # Rule 0 — highest priority, checked before every other rule: loaded-at-
    # runtime markdown anywhere in the diff forces the full suite, no matter
    # what else changed alongside it.
    if any(_is_loaded_markdown(p) for p in changed):
        return ALL

    # config.example.toml is deliberately NOT recognised by `_is_config_only`
    # (tests/builders.py copies it broadly; see module docstring), so a diff
    # containing it alone or mixed with anything else falls through every
    # rule below to the fail-closed `ALL` at the bottom, same as any other
    # unmapped path — no special case needed.

    if all(_is_config_only(p) for p in changed):
        return _CONFIG_TARGETS

    if all(_is_test_path(p) for p in changed):
        # Not every path under tests/ is collectible (conftest.py, builders.py,
        # fakes.py, ...) — a shared-helper edit must not narrow to a target
        # that silently collects and runs nothing. See module docstring.
        if all(_is_collectible_test_file(p) for p in changed):
            return changed
        return ALL

    # Rule for markdown + docs/design/release-fragments: every remaining path
    # must be one of (a) a `.md` file (mapped to its real readers, never
    # empty), (b) a release-fragments/** path of any extension, or (c) a
    # non-`.md` file under a whole-directory docs tree with no known reader.
    # Anything outside those three shapes falls through to ALL below.
    if all(
        p.endswith(".md") or _is_release_fragment(p) or _is_docs_only_non_markdown(p)
        for p in changed
    ):
        targets: set[str] = set()
        for p in changed:
            if p.endswith(".md"):
                targets.update(_markdown_targets(p))
            if _is_release_fragment(p):
                targets.add(_RELEASE_FRAGMENTS_TARGET)
            # a non-.md docs-only path (design/*.toml, ...) contributes
            # nothing — it has no known reader.
        return sorted(targets)

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
