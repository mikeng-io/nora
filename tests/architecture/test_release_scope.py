"""Exercise release eligibility against real git history, not mocked subjects."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_scope", ROOT / "scripts/release_scope.py")
assert spec and spec.loader
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


def run(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)  # noqa: S603


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run("init", "-q")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "Test")
    run("config", "core.hooksPath", "/dev/null")
    (tmp_path / "README.md").write_text("base")
    run("add", ".")
    run("commit", "-qm", "docs: initial")
    run("tag", "v1.0.0")
    return tmp_path


def commit(repo, path, subject):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed")
    run("add", ".")
    run("commit", "-qm", subject)


@pytest.mark.parametrize(
    "path,subject,expected",
    [
        ("design/new-capability.md", "feat: propose a capability", False),
        ("docs/setup.md", "fix: clarify setup", False),
        ("README.md", "feat: decorate README", False),
        ("tests/test_new.py", "fix: improve test", False),
        (".github/workflows/ci.yml", "feat: add a check", False),
        ("nora/feature.py", "docs: describe a feature", False),
        ("nora/feature.py", "feat: implement a feature", True),
        ("nora/feature.py", "fix(feature)!: change runtime behavior", True),
        ("nora/doctrine/voice.md", "doctrine: change runtime voice", True),
        ("nora/skills/example/SKILL.md", "change: adjust runtime guidance", True),
        ("uv.lock", "security: update a vulnerable dependency", True),
    ],
)
def test_release_requires_user_facing_runtime_change(repo, path, subject, expected):
    commit(repo, path, subject)
    assert scope.eligible("v1.0.0") is expected


def test_pending_code_releases_with_later_docs_but_docs_after_release_do_not(repo):
    commit(repo, "nora/feature.py", "feat: implement a feature")
    commit(repo, "docs/feature.md", "docs: describe the feature")
    assert scope.eligible("v1.0.0")
    run("tag", "v1.1.0")
    commit(repo, "design/future.md", "feat: describe future work")
    assert not scope.eligible("v1.1.0")


def test_failed_history_read_never_grants_release(repo):
    with pytest.raises(subprocess.CalledProcessError):
        scope.eligible("missing-tag")


def test_git_generated_revert_of_released_runtime_code_qualifies(repo):
    commit(repo, "nora/feature.py", "feat: implement a feature")
    run("tag", "v1.1.0")
    run("revert", "--no-edit", "HEAD")
    assert scope.eligible("v1.1.0")


@pytest.mark.parametrize(
    "path",
    [
        ".dockerignore",
        "scripts/standing_edit.py",
        "scripts/check_version_stamp.py",
        "scripts/gen_version_manifest.py",
        "scripts/build-image.sh",
        "config.example.toml",
        "identity.example.toml",
        "alembic.ini",
    ],
)
def test_shipped_and_build_inputs_qualify(repo, path):
    commit(repo, path, "fix: repair a runtime input")
    assert scope.eligible("v1.0.0")


@pytest.mark.parametrize(
    "path",
    [
        "nora/documents/catalog-prompt.md",
        "nora/dreamer/doctrine/dreamer.md",
        "nora/dreamer/objectives/cognition.md",
        "nora/dreamer/preludes/cognition-prelude.md",
        "nora/dreamer/skills/cognition-register.md",
    ],
)
def test_model_prompt_inputs_are_runtime_code(repo, path):
    commit(repo, path, "fix: correct a runtime prompt")
    assert scope.eligible("v1.0.0")
