"""The command boundary keeps ordinary deployments distinct from releases."""

import io
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


def _release_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_release_recreates_every_service_on_the_images_it_builds():
    """Recreating fewer leaves some on the old image. Until V5 the release
    recreated `nora` and `presence` only, missing `analytics` and both voice
    services. Pinned against docker-compose.yml, not a list in this file."""
    import yaml

    release = _release_module()
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    built = {"nora:latest", *(f"{name}:latest" for name in release.VOICE_IMAGES)}
    running_them = {
        name for name, spec in services.items()
        if isinstance(spec, dict) and spec.get("image") in built
    }
    assert running_them == set(release.RELEASE_SERVICES)


class _FakeRelease:
    """`release.main` with every host action replaced by a recorder, so the
    ORDER of the real `main` is what is asserted. `git` answers what a clean
    host on `origin/master` answers unless a test says otherwise."""

    def __init__(self, monkeypatch, *, ahead: str = "0", deploy_fails: bool = False,
                 push_fails: bool = False, lock_busy: bool = False) -> None:
        import subprocess

        self.release = _release_module()
        self.calls: list[str] = []
        release = self.release

        def fake_run(*args: str) -> str:
            if args[:2] == ("git", "status"):
                return ""
            if args[:2] == ("git", "rev-list"):
                return ahead
            if args[:2] == ("git", "fetch"):
                return ""
            if args[:3] == ("git", "tag", "-a"):
                self.calls.append("tag")
            elif args[:3] == ("git", "tag", "-d"):
                self.calls.append("untag")
            elif args[:2] == ("git", "push"):
                self.calls.append("push")
                if push_fails:
                    raise RuntimeError("Permission denied (publickey)")
            return ""

        def fake_lock() -> io.StringIO:
            if lock_busy:
                raise RuntimeError("another deploy holds the lock; nothing was tagged")
            self.calls.append("lock")
            return io.StringIO()

        def fake_deploy(version: str) -> None:
            self.calls.append("deploy")
            if deploy_fails:
                raise subprocess.CalledProcessError(1, ["verify-deploy.py"])

        monkeypatch.setattr(release, "run", fake_run)
        monkeypatch.setattr(release, "next_version", lambda: "V3")
        monkeypatch.setattr(release, "group_message", lambda path, version: "V3 出咗。")
        monkeypatch.setattr(release, "take_deploy_lock", fake_lock)
        monkeypatch.setattr(release, "refuse_unlocked_deploys", lambda: None)
        monkeypatch.setattr(release, "deploy", fake_deploy)

    def main(self) -> int:
        return self.release.main([])


def test_a_release_locks_then_tags_then_deploys_then_pushes(monkeypatch):
    """The lock comes before the tag, so a busy host tags nothing. The push
    comes after a verified deploy, so a failed one publishes nothing, and a
    successful one never leaves the tag on the host alone (V2)."""
    fake = _FakeRelease(monkeypatch)

    assert fake.main() == 0
    assert fake.calls == ["lock", "tag", "deploy", "push"]


def test_a_busy_host_tags_nothing(monkeypatch):
    fake = _FakeRelease(monkeypatch, lock_busy=True)

    assert fake.main() == 2
    assert "tag" not in fake.calls


def test_commits_not_on_origin_master_are_never_tagged(monkeypatch, capsys):
    """The deploy checkout collects unpushed commits; pushing a tag on one
    would publish it past master."""
    fake = _FakeRelease(monkeypatch, ahead="2")

    assert fake.main() == 2
    assert "tag" not in fake.calls
    assert "not on origin/master" in capsys.readouterr().err


def test_a_failed_deploy_removes_its_tag_and_pushes_nothing(monkeypatch, capsys):
    """A tag left behind makes the rerun ask for the NEXT version's note, so
    the release could not be retried without a hand `git tag -d`."""
    fake = _FakeRelease(monkeypatch, deploy_fails=True)

    assert fake.main() == 2
    assert fake.calls == ["lock", "tag", "deploy", "untag"]
    assert "rerun" in capsys.readouterr().err


def test_a_failed_push_after_a_verified_deploy_names_the_push(monkeypatch, capsys):
    """The containers are up and verified; only the push is missing. The tag
    stays (the image is stamped with it) and the error says what to run."""
    fake = _FakeRelease(monkeypatch, push_fails=True)

    assert fake.main() == 2
    assert fake.calls == ["lock", "tag", "deploy", "push"]
    assert "git push origin V3" in capsys.readouterr().err
