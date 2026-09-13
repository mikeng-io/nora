#!/usr/bin/env python3
"""Commit-subject convention check: `type(scope): description`.

Two failure shapes this catches (each measured on this repo's log before the
rule was written):

1. A subject with NO type at all — 294 of 2195 commits since 2026-08-01 were
   untyped, most of them bare `Merge ...` lines (the GitHub default merge
   message, which CONTRIBUTING.md's merge-strategy rule already forbids).
2. An UNDECLARED type — eleven spellings lived in the log that the table in
   CONTRIBUTING.md did not declare (`design` x69, `config` x20, `build` x12,
   `style` x11, `ops` x7, `security`, `spec`, `plan`, ...). A type that is in
   use but not declared is a convention that only looks agreed.

The type list below is CLOSED: it is CONTRIBUTING.md's declared table plus the
six de-facto types this repo already used heavily. Add a type by changing both
files in one commit — the table is the contract, this script only enforces it.

Wired as a `commit-msg` hook (see `.githooks/README.md`); the Makefile `hooks`
target installs it. It reads the message file git passes as $1 and exits 1 on
the first violation, printing which rule and where the convention is written.
"""
from __future__ import annotations

import re
import sys

# Declared in CONTRIBUTING.md §Commit messages. Keep the two in lockstep.
TYPES = {
    # declared in the table since the file existed
    "feat": "new capability",
    "fix": "defect repair",
    "docs": "documentation",
    "test": "tests",
    "refactor": "restructuring with no behaviour change",
    "chore": "tooling, hygiene, dependencies",
    "change": "deliberate behaviour change that is neither feature nor fix",
    "merge": "the merge commit for a branch",
    # adopted from live use, now declared
    "design": "design-bundle / architecture-document work",
    "config": "configuration surface (schema, example files, loader)",
    "build": "build/deploy tooling (Dockerfile, compose, Makefile)",
    "style": "formatting or lint with no behaviour change",
    "ci": "CI / GitHub plumbing",
    "ops": "operational/deployment toggles on the live stack",
    "doctrine": "Nora's doctrine files (voice/conduct/silence/constraints)",
    "perf": "a change that only affects performance (standard Conventional Commits)",
    "security": "a security-relevant fix (matches the release-fragment `security` type)",
}

TYPES_RE = "|".join(sorted(TYPES))
SUBJECT_RE = re.compile(rf"^({TYPES_RE})(\([^)]+\))?!?: .+")

# Bare `Merge pull request #N from ...` / `Merge branch ...` are GitHub's
# default merge subjects. The authored-merge rule (CONTRIBUTING.md §Merge
# strategy) REPLACES them with a `merge: <summary of the arc>` subject.
BARE_MERGE_RE = re.compile(r"^(Merge pull request|Merge branch|Merge remote-tracking branch)\b")
# `Revert "<original subject>"` is standard git plumbing; allowed as-is.
REVERT_RE = re.compile(r'^Revert "')
# Fixup/temporary commits are for interactive rebase only, never master.
WIP_RE = re.compile(r"^(wip|WIP)\b")


AUTHORED_MERGE_RE = re.compile(r"^merge\s*:")    # merge: <the arc> — current generation
OLD_MERGE_RE = re.compile(r"^Merge\s+[^ ]*\s*:")  # Merge <branch>: <desc> — superseded


def check_subject(subject: str) -> list[str]:
    problems: list[str] = []
    if AUTHORED_MERGE_RE.match(subject):
        return problems
    if OLD_MERGE_RE.match(subject):
        problems.append(
            "superseded merge-subject generation (`Merge <branch>: ...`). The current\n"
            "    shape is `merge: <the arc>` - what the branch set out to fix, what was\n"
            "    measured, what shipped. The branch name is already in the merge commit;\n"
            "    it does not need to be in the subject. (CONTRIBUTING.md section Merge\n"
            "    strategy, same superseded-generations table as branch naming.)"
        )
        return problems
    if BARE_MERGE_RE.match(subject):
        problems.append(
            "GitHub-default merge subject. Write an AUTHORED merge subject:\n"
            "    merge: <what the branch set out to fix, what was measured, what shipped>\n"
            "    (CONTRIBUTING.md §Merge strategy)"
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
