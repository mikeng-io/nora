"""Release fragments are authored input, not a changelog reconstructed from git."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "scripts" / "render_release_notes.py"
CHECK = ROOT / "scripts" / "check_release_fragments.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is test literals and tmp paths
        ["python", *args], capture_output=True, text=True, check=False
    )


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(  # noqa: S603 - test-local git repository
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_renderer_groups_user_visible_fragments_and_hides_none(tmp_path: Path):
    (tmp_path / "feature.json").write_text(json.dumps({
        "type": "feature", "summary": "Nora can send GIFs.",
        "details": ["GIF-only replies are allowed."], "limitations": ["No stickers yet."],
    }))
    (tmp_path / "internal.json").write_text(json.dumps({"type": "none", "reason": "Refactor."}))

    result = _run(str(RENDER), "--dir", str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "New:\n- Nora can send GIFs.\n  - GIF-only replies are allowed.\n"
        "Limitations:\n- No stickers yet.\n"
    )


def test_renderer_rejects_an_unexplained_internal_only_fragment(tmp_path: Path):
    (tmp_path / "bad.json").write_text('{"type": "none"}')

    result = _run(str(RENDER), "--dir", str(tmp_path))

    assert result.returncode == 2
    assert "requires a non-empty reason" in result.stderr


def test_branch_gate_requires_a_fragment_or_explicit_none(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "nora.py").write_text("first\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "nora.py").write_text("changed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")

    missing = _run(str(CHECK), "--repo", str(repo), "--base", "master")

    assert missing.returncode == 2
    assert "no release fragment" in missing.stderr
    fragment = repo / "release-fragments" / "unreleased" / "internal.json"
    fragment.parent.mkdir(parents=True)
    fragment.write_text('{"type": "none", "reason": "Internal refactor."}')
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fragment")

    assert _run(str(CHECK), "--repo", str(repo), "--base", "master").returncode == 0
