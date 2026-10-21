#!/usr/bin/env python3
"""Commit-subject convention check: `type(scope): description`.

Three failure shapes this catches:

1. A subject with NO type at all, most often a bare `Merge ...` line (the
   GitHub default merge message, which docs/maintainers/conventions.md's merge-strategy rule
   forbids).
2. An UNDECLARED type. A type that is in use but not declared is a convention
   that only looks agreed.
3. A superseded or default MERGE subject — `Merge pull request #N ...` (the
   GitHub default) and `Merge <branch>: ...` (the superseded form). Both are
   replaced by `merge: <the arc>`; the shape is checked, not just the prefix.

The type list below is CLOSED: it is CONTRIBUTING.md's declared table plus the
types this repo uses beyond it (`design`, `config`, `build`, `style`, `ci`,
`ops`, `doctrine`, plus `perf` and `security` from the standard). Add a type
by changing both files in one commit — the table is the contract, this script only enforces it.

Wired as a `commit-msg` hook (see `.githooks/README.md`); the Makefile `hooks`
target installs it. It reads the message file git passes as $1 and exits 1 on
the first violation, printing which rule and where the convention is written.
"""
from __future__ import annotations

import re
import sys

# Declared in CONTRIBUTING.md §Commit messages. Keep the two in lockstep.
TYPES = {
    # declared in the table
    "feat": "new capability",
    "fix": "defect repair",
    "docs": "documentation",
    "test": "tests",
    "refactor": "restructuring with no behaviour change",
    "chore": "tooling, hygiene, dependencies",
    "change": "deliberate behaviour change that is neither feature nor fix",
    "merge": "the merge commit for a branch",
    # in use beyond the base table, declared
    "design": "design-bundle / architecture-document work",
    "config": "configuration surface (schema, example files, loader)",
    "build": "build/deploy tooling (Dockerfile, compose, Makefile)",
    "style": "formatting or lint with no behaviour change",
    "ci": "CI / GitHub plumbing",
    "ops": "operational/deployment toggles on the live stack",
    "doctrine": "Nora's doctrine files (voice/conduct/silence/constraints)",
    "perf": "a change that only affects performance (standard Conventional Commits)",
    "security": "a security-relevant fix (its own CHANGELOG section, DEC-314)",
}

TYPES_RE = "|".join(sorted(TYPES))
SUBJECT_RE = re.compile(rf"^({TYPES_RE})(\([^)]+\))?!?: .+")

# Bare `Merge pull request #N from ...` / `Merge branch ...` are GitHub's
# default merge subjects. The authored-merge rule (docs/maintainers/conventions.md §Merge
# strategy) REPLACES them with a `merge: <summary of the arc>` subject.
BARE_MERGE_RE = re.compile(r"^(Merge pull request|Merge branch|Merge remote-tracking branch)\b")
# `Revert "<original subject>"` is standard git plumbing; allowed as-is.
REVERT_RE = re.compile(r'^Revert "')
# Fixup/temporary commits are for interactive rebase only, never master.
WIP_RE = re.compile(r"^(wip|WIP)\b")


# `merge: <the arc>` is the current form. The arc must be REAL text: a bare
# `merge:` (or whitespace) states nothing and would satisfy the check without
# writing the arc, so it is rejected explicitly.
# `\s*` before the colon, not `\s*` inside it: `merge\t: x` is a typo, not a
# subject, and must not sneak through.
AUTHORED_MERGE_RE = re.compile(r"^merge:\s+\S")  # merge: <non-empty arc> — current form
# The GitHub-default bodies, checked AFTER the authored prefix matched: without
# this, `merge: Merge pull request #N from x` would pass on the prefix alone — the
# exact defect this script exists to catch, wearing the sanctioned type.
DEFAULT_BODY_RE = re.compile(
    r"^Merge (pull request|branch|remote-tracking branch)\b"
)
OLD_MERGE_RE = re.compile(r"^Merge\s+[^ ]*\s*:")  # Merge <branch>: <desc> — superseded


def check_subject(subject: str) -> list[str]:
    problems: list[str] = []
    if AUTHORED_MERGE_RE.match(subject):
        # The prefix is right; the body still has to be an authored arc. A
        # GitHub-default body behind the `merge:` prefix is the same defect,
        # one keystroke away from looking compliant.
        body = subject.split(":", 1)[1].strip()
        if DEFAULT_BODY_RE.match(body):
            problems.append(
                "authored-merge prefix on a GitHub-DEFAULT body. The prefix is right;\n"
                "    the body is the default merge message. Write the arc instead:\n"
                "    merge: <what the branch set out to fix, what was measured, what shipped>\n"
                "    (docs/maintainers/conventions.md §Merge strategy)"
            )
        return problems
    if re.match(r"^merge\s*:", subject):
        # Reached when the subject mentions `merge` as a type but is not the
        # authored shape: either the arc is empty/whitespace-only, or the colon
        # is not flush against the type (`merge :  x`, `merge\t: x`). Both are
        # cheap ways to satisfy the check without stating the arc, so both are
        # named rather than silently accepted.
        problems.append(
            "malformed `merge:` subject — the arc is empty, or the prefix is not\n"
            "    flush (`merge: <arc>`). Say what the branch set out to fix and what\n"
            "    shipped, e.g. `merge: the conventions get delivered (#246)`.\n"
            "    (docs/maintainers/conventions.md §Merge strategy)"
        )
        return problems
    if OLD_MERGE_RE.match(subject):
        problems.append(
            "superseded merge-subject generation (`Merge <branch>: ...`). The current\n"
            "    shape is `merge: <the arc>` - what the branch set out to fix, what was\n"
            "    measured, what shipped. The branch name is already in the merge commit;\n"
            "    it does not need to be in the subject. (docs/maintainers/conventions.md section Merge\n"
            "    strategy.)"
        )
        return problems
    if BARE_MERGE_RE.match(subject):
        problems.append(
            "GitHub-default merge subject. Write an AUTHORED merge subject:\n"
            "    merge: <what the branch set out to fix, what was measured, what shipped>\n"
            "    (docs/maintainers/conventions.md §Merge strategy)"
        )
        return problems
    if REVERT_RE.match(subject):
        return problems
    if WIP_RE.match(subject):
        problems.append(
            "`wip:` commits do not get written to history — rebase them away\n"
            "    before merge (interactive rebase / fixup)."
        )
        return problems
    m = SUBJECT_RE.match(subject)
    if m:
        return problems
    no_type = re.match(r"^[a-zA-Z][\w-]*(\([^)]*\))?!?:", subject)
    if no_type:
        used = subject.split(":", 1)[0].split("(", 1)[0].rstrip("!")
        problems.append(
            f"type `{used}:` is not in the declared table. Declared types:\n"
            "    " + ", ".join(f"{t} ({TYPES[t]})" for t in sorted(TYPES)) + "\n"
            "    To add one: extend CONTRIBUTING.md §Commit messages AND the TYPES\n"
            "    map in scripts/check_commit_subject.py, in the same commit."
        )
    else:
        problems.append(
            "subject has no `type:` prefix at all.\n"
            "    Format: type(scope): description\n"
            "    e.g. fix(gate): the verdict forms on a raw completion\n"
            "    Declared types: " + ", ".join(sorted(TYPES)) + "\n"
            "    (CONTRIBUTING.md §Commit messages)"
        )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_commit_subject.py <commit-msg-file>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if not lines or not lines[0].strip():
        print("commit subject is empty.", file=sys.stderr)
        return 1
    # A merge commit whose subject is already authored passes through the same check.
    problems = check_subject(lines[0].rstrip())
    if problems:
        print("commit subject violates the convention:", file=sys.stderr)
        print(f"    subject: {lines[0]!r}", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
