"""Release fragments are authored input, not a changelog reconstructed from git."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from render_release_notes import load_fragments  # noqa: E402

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
    (tmp_path / "feature.json").write_text(
        json.dumps(
            {
                "type": "feature",
                "summary": "Nora can send GIFs.",
                "details": ["GIF-only replies are allowed."],
                "limitations": ["No stickers yet."],
            }
        )
    )
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
    fragment.write_text('{"type": "none"}')
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fragment")

    invalid = _run(str(CHECK), "--repo", str(repo), "--base", "master")
    assert invalid.returncode == 2
    assert "requires a non-empty reason" in invalid.stderr
    fragment.write_text('{"type": "none", "reason": "Internal refactor."}')
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "valid fragment")

    assert _run(str(CHECK), "--repo", str(repo), "--base", "master").returncode == 0


def test_branch_gate_does_not_accept_deleting_an_existing_fragment(tmp_path: Path):
    repo = tmp_path / "repo"
    fragment = repo / "release-fragments" / "unreleased" / "old.json"
    fragment.parent.mkdir(parents=True)
    fragment.write_text('{"type": "none", "reason": "Old internal work."}')
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "nora.py").write_text("changed\n")
    fragment.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "delete fragment")

    result = _run(str(CHECK), "--repo", str(repo), "--base", "master")

    assert result.returncode == 2
    assert "no release fragment" in result.stderr


def test_every_fragment_in_the_tree_is_well_formed():
    """The guard the seven malformed fragments of 2026-09-11 got past (#172).

    Nothing checked the fragments THEMSELVES. `check_release_fragments.py` is a
    branch gate — it asks whether a change brought a fragment, and on a branch
    with no changes against its base it has nothing to say and exits 0. So a
    malformed fragment reached `master` and then failed every OTHER branch,
    which is the wrong end to notice it from. This reads the real directory.
    """
    fragments = load_fragments(ROOT / "release-fragments" / "unreleased")
    assert fragments, "the unreleased directory is empty"


def test_the_loader_names_every_bad_fragment_not_the_first(tmp_path: Path):
    """Three bad files, three names, one run.

    The loader raised on the first offending path, so a tree with seven bad
    fragments reported one; you fixed it, ran again, got the next. Seven runs
    to learn what one run knew. Worse than slow: fixing the first UNCOVERED
    three more problems that had been invisible behind it, which is how the
    count went from seven to ten on the day this was written.
    """
    (tmp_path / "a.json").write_text(
        json.dumps({"type": "fix", "summary": "A.", "details": "a string"})
    )
    (tmp_path / "b.json").write_text(json.dumps({"type": "internal", "summary": "B."}))
    (tmp_path / "c.json").write_text(json.dumps({"type": "none"}))
    (tmp_path / "d.json").write_text(json.dumps({"type": "fix", "summary": "D.", "details": []}))

    result = _run(str(RENDER), "--dir", str(tmp_path))

    assert result.returncode == 2
    assert "3 release-fragment problem(s)" in result.stderr
    for name in ("a.json", "b.json", "c.json"):
        assert name in result.stderr, result.stderr
    assert "d.json" not in result.stderr, "the valid fragment was reported"


def test_a_details_string_says_what_to_do_about_it(tmp_path: Path):
    """`details` as a bare string is the single mistake seven fragments made,
    so the message names the shape rather than restating the schema."""
    (tmp_path / "a.json").write_text(
        json.dumps({"type": "fix", "summary": "A.", "details": "one long paragraph"})
    )

    result = _run(str(RENDER), "--dir", str(tmp_path))

    assert "it is a single string" in result.stderr
    assert "split it into the sentences it already is" in result.stderr
