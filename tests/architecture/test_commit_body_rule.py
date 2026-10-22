"""A user-visible commit says why (CONTRIBUTING.md, Commit messages).

The subject of a `feat`, `fix`, `security`, `perf`, `change` or `doctrine` commit is a
changelog line; without a body nobody can tell what was broken or measured. Merge,
revert and hidden-type commits are not asked for one. The same function judges the
local commit-msg and pre-push hooks and the server-side PR check.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("checker", ROOT / "scripts/check_commit_subject.py")
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

WHY = "Reads blocked on a lock; measured 40 ms to 3 ms; the cache is not covered."


@pytest.mark.parametrize("kind", sorted(checker.BODY_TYPES))
def test_a_visible_commit_without_a_body_is_refused(kind):
    problems = checker.check_message(f"{kind}(scope): do a thing\n")
    assert problems and "body" in problems[0]


@pytest.mark.parametrize("kind", sorted(checker.BODY_TYPES))
def test_a_visible_commit_with_a_body_passes(kind):
    assert checker.check_message(f"{kind}(scope): do a thing\n\n{WHY}\n") == []


@pytest.mark.parametrize(
    "message",
    [
        "docs: describe a thing\n",
        "chore: tidy\n",
        "test: cover a thing\n",
        "refactor: split a module\n",
        "build(deps): bump a package\n",
        "merge: the arc of the branch (#1)\n",
        'Revert "fix: a thing"\n',
    ],
)
def test_hidden_merge_and_revert_commits_need_no_body(message):
    assert checker.check_message(message) == []


def test_trailers_and_git_comments_are_not_a_body():
    message = (
        "fix: a thing\n\n"
        "# Please enter the commit message\n"
        "Co-Authored-By: Someone <someone@example.invalid>\n"
        "PII-Ack: 1 (human)\n"
    )
    assert checker.check_message(message), "metadata alone must not satisfy the rule"


def test_a_short_body_is_refused_and_the_boundary_is_twenty_characters():
    assert checker.check_message("fix: a thing\n\n" + "x" * 19)
    assert checker.check_message("fix: a thing\n\n" + "x" * 20) == []


def test_a_bad_subject_is_reported_before_the_body():
    problems = checker.check_message("did something\n")
    assert problems and "type" in problems[0]


def test_the_hook_entry_point_applies_the_body_rule(tmp_path):
    import subprocess
    import sys

    def run(text):
        path = tmp_path / "msg.txt"
        path.write_text(text, encoding="utf-8")
        return subprocess.run(  # noqa: S603 -- fixed repo script and a tempfile
            [sys.executable, str(ROOT / "scripts/check_commit_subject.py"), str(path)],
            capture_output=True,
            text=True,
        ).returncode

    assert run("fix: a thing\n") == 1
    assert run(f"fix: a thing\n\n{WHY}\n") == 0
