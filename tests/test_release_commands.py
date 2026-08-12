"""The command boundary keeps ordinary deployments distinct from releases."""

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
