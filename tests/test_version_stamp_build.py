"""The build-time half of DEC-97: the generator, the gate, and the reader agreeing.

WHY THIS RUNS A REAL GIT REPOSITORY
-----------------------------------
`scripts/gen_version_manifest.py` writes the file `nora.infra.version.VersionManifest`
reads, and nothing else checks that those two agree — a generator that emitted
`{"tag": …}` instead of `{"version": …}` would leave every unit test in this
suite green and every deployed Nora answering `unavailable`. So this builds an
actual repository with actual annotated tags, runs the actual script over it, and
reads the result back through the actual reader.

Mocking `git` here would be the fake-that-answers-everything: the whole question
is what `git tag -l --format=%(contents)` and `git log --format=%s` really
return, and a double would return whatever this file was written to expect.

The scripts are COPIED into the throwaway repository rather than run against this
checkout, because both resolve their paths from `__file__` — and because a test
must never create a tag in a worktree that shares its refs with the operator's
repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nora.infra.version import NOTHING_FOUND, OK, VersionManifest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "gen_version_manifest.py"
CHECKER = ROOT / "scripts" / "check_version_stamp.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="the generator derives the manifest from git",
)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # Every argv here is built in this file from literals and a tmp_path;
    # nothing comes from input and no shell is involved.
    return subprocess.run(  # noqa: S603
        argv, cwd=cwd, capture_output=True, text=True, check=False,
    )


def _git(cwd: Path, *args: str) -> None:
    done = _run(["git", *args], cwd)
    assert done.returncode == 0, done.stderr


def _commit(repo: Path, name: str, subject: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", subject)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one old manual tag (V1, annotated) and one release
    tagged the way release-please tags it: a LIGHTWEIGHT `v6.0.0` on the commit
    that wrote its `CHANGELOG.md` section (DEC-314)."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "nora").mkdir()
    shutil.copy(GENERATOR, repo / "scripts" / GENERATOR.name)
    shutil.copy(CHECKER, repo / "scripts" / CHECKER.name)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")

    # The copied scripts land in this first commit, so the tree is CLEAN when
    # the generator runs — which matters, because a dirty tree is refused and
    # that refusal has its own test below.
    _commit(repo, "a", "feat: the first thing")
    _git(repo, "tag", "-a", "V1", "-m", "The first one.")
    _commit(repo, "b", "feat(reminders): the heartbeat")
    _commit(repo, "c", "fix(fire): the header")
    (repo / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-qm", "chore(master): release 6.0.0")
    _git(repo, "tag", "v6.0.0")
    return repo


CHANGELOG = """# Changelog

## [6.0.0](https://github.com/o/r/compare/V1...v6.0.0) (2026-10-16)


### Features

* **reminders:** the heartbeat ([abc1234](https://github.com/o/r/commit/abc1234)), closes [#12](https://github.com/o/r/issues/12)


### Bug Fixes

* **fire:** the header ([def5678](https://github.com/o/r/commit/def5678))
"""


def _stamp(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["python", str(repo / "scripts" / GENERATOR.name), *args], repo)


def test_the_stamp_the_generator_writes_is_the_stamp_the_reader_reads(repo: Path):
    """The whole loop, in one test: git → the file → `VersionManifest`.

    The version's CHANGELOG.md section becomes `notes` and the commit subjects
    become `changes` (what «what was in v6.0.0» reads). Those two must not swap,
    and nothing else in the suite would notice if they did.
    """
    done = _stamp(repo, "--version", "v6.0.0")
    assert done.returncode == 0, done.stderr

    manifest = VersionManifest(repo / "nora" / "version.json")
    current = manifest.current()

    assert current.status == OK
    assert current.entry is not None
    assert current.entry.version == "v6.0.0"
    assert current.entry.notes.startswith("### Features")
    assert "* **reminders:** the heartbeat, closes #12" in current.entry.notes
    assert "https://" not in current.entry.notes
    assert set(current.entry.changes) >= {
        "feat(reminders): the heartbeat", "fix(fire): the header",
    }
    assert manifest.known() == ("v6.0.0", "V1")


def test_an_old_manual_tags_annotation_survives_into_the_manifest(repo: Path):
    _stamp(repo, "--version", "v6.0.0")

    entry = VersionManifest(repo / "nora" / "version.json").lookup("V1").entry

    assert entry is not None
    assert entry.notes == "The first one."
    assert entry.changes == ("feat: the first thing",)


def test_a_version_that_was_never_tagged_is_nothing_found(repo: Path):
    """The generator recorded V1 and v6.0.0 and nothing else, so a v9.0.0
    question is answered from a manifest that really was read — `nothing_found`,
    not the `unavailable` an unstamped build gives."""
    _stamp(repo, "--version", "v6.0.0")

    assert VersionManifest(
        repo / "nora" / "version.json").lookup("v9.0.0").status == NOTHING_FOUND


def test_a_build_past_the_newest_tag_is_stamped_with_its_distance(repo: Path):
    """A deploy between releases: `git describe` names it `v6.0.0-1-g<sha>`.
    She knows what she IS (`ok`) and has no notes for it, and the manifest must
    not borrow v6.0.0's notes for a build that holds more than v6.0.0."""
    _commit(repo, "d", "fix: after the release")
    assert _stamp(repo).returncode == 0
    manifest = VersionManifest(repo / "nora" / "version.json")

    assert manifest.current().entry is not None
    assert manifest.current().entry.version.startswith("v6.0.0-1-g")
    assert manifest.current().entry.notes == ""
    assert manifest.lookup("v6.0.0").status == OK


def test_a_dirty_tree_is_refused(repo: Path):
    """The image would contain code no commit describes, so the recorded commit
    would name an artifact that never existed. A change to a TRACKED file is
    dirty wherever it is."""
    (repo / "a").write_text("half a change")

    done = _stamp(repo, "--version", "v6.0.0")

    assert done.returncode == 2
    assert "dirty" in done.stderr
    assert not (repo / "nora" / "version.json").exists()


def test_an_untracked_file_the_image_copies_is_dirty(repo: Path):
    """An untracked file under a directory the Dockerfile copies IS in the
    image, and no commit describes it."""
    (repo / "nora" / "stray.py").write_text("print('not committed')")

    done = _stamp(repo, "--version", "v6.0.0")

    assert done.returncode == 2
    assert "dirty" in done.stderr


def test_an_untracked_file_outside_the_image_is_not_dirty(repo: Path):
    """The deploy host's checkout always holds untracked backups and tool
    caches. None reaches the image, and refusing on them forced V5 through
    `--allow-dirty`, which also waves through what the check is for."""
    (repo / "config.toml.bak-20260907").write_text("a backup")
    (repo / ".uacp").mkdir()
    (repo / ".uacp" / "cache").write_text("a tool's cache")

    done = _stamp(repo, "--version", "v6.0.0")

    assert done.returncode == 0, done.stderr
    assert (repo / "nora" / "version.json").exists()


def test_the_image_dirs_are_every_directory_the_dockerfile_copies():
    """`IMAGE_DIRS` is what the untracked half of the dirty check looks under,
    so a directory the Dockerfile starts copying must join it, or untracked code
    there ships unstamped."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_version_manifest", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import json

    # Join continuation lines first: a COPY split with `\\` is one instruction.
    text = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    copied: set[str] = set()
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) < 2 or parts[0].upper() not in ("COPY", "ADD"):
            continue
        body = parts[1]
        if "--from" in body:  # from another stage, not the build context
            continue
        words = [w for w in body.split() if not w.startswith("--")]
        if body.lstrip().startswith("["):
            words = json.loads(body[body.index("["):])
        for source in words[:-1]:
            assert source.strip("./") != "", (
                f"`{line.strip()}` copies the whole context; IMAGE_DIRS cannot bound it")
            top = source.strip("./").split("/")[0]
            if (ROOT / top).is_dir():
                copied.add(top)
    assert copied, "no directory COPY found: the Dockerfile parse is broken"
    assert copied <= set(module.IMAGE_DIRS), copied - set(module.IMAGE_DIRS)


def test_a_version_outside_the_scheme_is_refused(repo: Path):
    """vX.Y.Z or git describe's form, and nothing else: an old `V3` or a typed
    `v3.1` is refused rather than silently accepted."""
    for wrong in ("V3", "v3.1"):
        assert _stamp(repo, "--version", wrong).returncode == 2, wrong
    done = _stamp(repo, "--version", "v3.1")

    assert done.returncode == 2
    assert not (repo / "nora" / "version.json").exists()


def test_the_newest_tag_is_used_when_no_version_is_given(repo: Path):
    assert _stamp(repo).returncode == 0

    assert json.loads(
        (repo / "nora" / "version.json").read_text())["version"] == "v6.0.0"


def test_a_tree_with_no_release_tag_is_refused_rather_than_named_after_v1(tmp_path: Path):
    """Before the first release-please release only `V<n>` tags exist, and
    `--match 'v[0-9]*'` must not fall back to one of them."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "nora").mkdir()
    shutil.copy(GENERATOR, repo / "scripts" / GENERATOR.name)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    _commit(repo, "a", "feat: the first thing")
    _git(repo, "tag", "-a", "V1", "-m", "The first one.")

    done = _stamp(repo)

    assert done.returncode == 2
    assert not (repo / "nora" / "version.json").exists()


def _check(repo: Path, arg: str) -> subprocess.CompletedProcess[str]:
    return _run(["python", str(repo / "scripts" / CHECKER.name), arg], repo)


def test_the_build_gate_passes_when_the_stamp_matches(repo: Path):
    _stamp(repo, "--version", "v6.0.0")

    assert _check(repo, "v6.0.0").returncode == 0


def test_the_build_gate_refuses_a_stamp_that_disagrees_with_the_build(repo: Path):
    """The grounding gate, and the reason "the reported version cannot disagree
    with the running code" is a property rather than an intention: the image is
    never finished if the two strings differ."""
    _stamp(repo, "--version", "v6.0.0")

    done = _check(repo, "v6.1.0")

    assert done.returncode == 2
    assert "disagrees" in done.stderr


def test_the_build_gate_refuses_an_unstamped_context(repo: Path):
    """An unstamped image answers `unavailable` to every version question
    forever, which under R-5 looks exactly like a healthy Nora nobody asked."""
    done = _check(repo, "v6.0.0")

    assert done.returncode == 2
    assert "missing" in done.stderr


def test_an_unstamped_build_is_allowed_and_leaves_no_stale_stamp(repo: Path):
    """The empty argument is the UNSTAMPED build, and it is allowed.

    Refusing it would break `docker compose build` and the plain
    `docker build --network=host` in README — both documented. It would also
    buy nothing: the property is «the reported version cannot disagree with the
    running code», and an image that reports nothing cannot disagree.

    But it has to be unstamped for REAL. A manifest left over from an earlier
    stamped build would ride into the image and be read as authoritative, so
    the image would claim a version it is not running — the exact disagreement
    this gate exists to prevent. Warning would leave the property depending on
    somebody reading the build log, so the stale file is deleted.

    Both halves asserted: the build proceeds AND the stamp is gone."""
    _stamp(repo, "--version", "v6.0.0")
    assert (repo / "nora" / "version.json").exists()

    result = _check(repo, "")

    assert result.returncode == 0
    assert not (repo / "nora" / "version.json").exists()
    assert "UNSTAMPED" in result.stdout


def test_the_build_gate_still_refuses_a_claimed_version_with_no_manifest(repo: Path):
    """Allowing the unstamped build must not have widened into allowing a build
    that CLAIMS V3 and ships nothing to read — that is the same failure from the
    other side, and it is still a hard stop."""
    assert _check(repo, "v6.1.0").returncode == 2


def test_the_dockerfile_runs_the_gate_with_the_build_argument():
    """The gate is only a gate if the build actually runs it. Asserted on the
    Dockerfile text because the alternative — building the image — needs the
    network and a docker daemon, and this is the one fact that would silently
    stop being true if a COPY were reordered."""
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "ARG NORA_VERSION" in dockerfile
    assert 'python scripts/check_version_stamp.py "$NORA_VERSION"' in dockerfile
    # …after the code it is checking, or it would check an empty directory.
    assert dockerfile.index("COPY nora/") < dockerfile.index("check_version_stamp.py")


def test_a_lightweight_tag_with_no_changelog_section_has_no_notes(repo: Path):
    """release-please's tags are lightweight. `%(contents)` of a lightweight tag
    is the COMMIT's message, which nobody wrote as a note about a version, so a
    tag with no CHANGELOG section must carry empty notes, not that message."""
    _commit(repo, "e", "fix: a commit message, not a release note")
    _git(repo, "tag", "v6.0.1")

    assert _stamp(repo, "--version", "v6.0.1").returncode == 0
    entry = VersionManifest(repo / "nora" / "version.json").lookup("v6.0.1").entry

    assert entry is not None
    assert entry.notes == ""
    assert entry.changes == ("fix: a commit message, not a release note",)
