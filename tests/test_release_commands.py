"""The command boundary keeps ordinary deployments distinct from releases."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ordinary_image_build_rejects_version_arguments():
    script = (ROOT / "scripts" / "build-image.sh").read_text()
    assert 'if [ "$#" -ne 0 ]; then' in script
    assert "deployments are unversioned" in script
    assert "NORA_VERSION=" in script


def test_only_the_explicit_release_command_uses_the_versioned_builder():
    release = (ROOT / "scripts" / "release.py").read_text()
    ordinary = (ROOT / "scripts" / "build-image.sh").read_text()
    assert "build-release-image.sh" in release
    assert 'VERSION="$1"' not in ordinary


def test_fragments_do_not_define_a_release_action():
    guide = (ROOT / "release-fragments" / "README.md").read_text()
    assert "never create a tag or deploy a release" in guide


def test_every_script_the_release_path_execs_is_executable():
    """`scripts/release.py` runs the release builder as a SUBPROCESS
    (`subprocess.run([str(path), version])`), not through a shell, so the
    execute bit is load-bearing rather than cosmetic.

    It was committed 100644 while its sibling `build-image.sh` was 100755, and
    the failure mode is the bad one: `release.py` creates the annotated tag
    FIRST and builds second, so a release would have tagged the version, raised
    PermissionError on the build, and left a version tag pointing at no image.

    Asserts the bit on disk rather than parsing `git ls-files`, because what
    breaks is the exec, not the index — a checkout with a broken mode fails the
    same way however it got there.

    `release.py` itself is deliberately NOT in this list: it is documented and
    run as `uv run python scripts/release.py` (README), so the interpreter is
    named by the caller and its own bit is not load-bearing. Only the scripts
    something else EXECS belong here.
    """
    for name in ("build-release-image.sh", "build-image.sh"):
        path = ROOT / "scripts" / name
        assert path.exists(), name
        assert os.access(path, os.X_OK), (
            f"scripts/{name} must be executable — the release path execs it directly"
        )
