#!/usr/bin/env python3
"""Create the next manual Nora release, then build, deploy and publish it.

The operator decides when to run this command, on the deploy host, as
``python3 scripts/release.py``. The committed release record is
``docs/releases/V<N>.md``; its English summary stays in Git while its
``## Group message`` section becomes the annotated tag message Nora relays.

One command does the whole release, in this order, and stops at the first
failure:

1. take the deploy lock every other deploy on the host takes, refuse an
   unlocked docker build/up, and refuse commits not on ``origin/master``;
2. tag ``V<N>`` at HEAD with the group message (removed again if a later
   step fails, so a rerun asks for the same version);
3. build the stamped ``nora:V<N>`` image, and rebuild the images that must
   move with it (``VOICE_IMAGES``);
4. recreate every service that runs them (``RELEASE_SERVICES``), run
   ``scripts/verify-deploy.py`` and read each voice service's image back;
5. tag the image ``nora:sha-<short>`` and log it to ``DEPLOY_LOG``, so the
   rollback in the deploy procedure can find it;
6. push the tag. A tag made here and never pushed stays on this host alone,
   as V2 did from 2026-08-12 until V5.

V5 (2026-09-26) could not use this command. It refused on untracked files
(the host checkout always holds some), and ``uv`` was not on the
non-interactive PATH. It would also have recreated only ``nora`` and
``presence``, leaving ``analytics`` and both voice services on the old image.
It is stdlib-only for the same PATH reason.
"""

from __future__ import annotations

import argparse
import fcntl
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"V(\d+)$")

#: The lock every deploy on the host holds (the nora-deploy skill, step 2).
DEPLOY_LOCK = Path("/tmp/nora-deploy.lock")  # noqa: S108 - a fixed, shared host lock
LOCK_WAIT_SECONDS = 900
#: Built from this checkout beside `nora:latest`; their images move together
#: or not at all (docs/runbook.md 3b).
VOICE_IMAGES = ("voice-sidecar", "voice-wake")
#: Every service running `nora:latest` or a `VOICE_IMAGES` image. Recreating
#: fewer leaves some on the old image: `presence` and `analytics` crash-looped
#: 279 times on a new config key that way (2026-09-16).
RELEASE_SERVICES = ("nora", "presence", "analytics", *VOICE_IMAGES)
#: Where a verified deploy is recorded for the rollback procedure.
DEPLOY_LOG = Path.home() / "nora-backups" / "deploys.log"


def run(*args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed release-script commands only
        args, cwd=ROOT, text=True, capture_output=True, check=False
    )
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or " ".join(args))
    return done.stdout


def next_version() -> str:
    numbers = [
        int(match.group(1))
        for tag in run("git", "tag", "--list", "V*").splitlines()
        if (match := VERSION.fullmatch(tag)) is not None
    ]
    highest = max(numbers, default=0)
    return f"V{highest + 1}"


def group_message(path: Path, version: str) -> str:
    if not path.exists():
        raise ValueError(f"release note is missing: {path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith(f"# {version}") or "## Summary\n" not in text:
        raise ValueError(f"release note must start with '# {version}' and include '## Summary'")
    match = re.search(r"^## Group message\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise ValueError("release note needs a non-empty '## Group message' section")
    return match.group(1).strip()


def take_deploy_lock() -> object:
    """Hold `DEPLOY_LOCK` for the rest of the process, waiting up to
    `LOCK_WAIT_SECONDS` for another deploy to finish. The returned file must
    stay referenced: closing it releases the lock."""
    handle = DEPLOY_LOCK.open("a")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() > deadline:
                handle.close()
                raise RuntimeError(
                    f"another deploy holds {DEPLOY_LOCK}; nothing was tagged") from None
            time.sleep(5)


def refuse_unlocked_deploys() -> None:
    """A hand-run `docker build` or `docker compose up` that skipped the lock
    can still overlap the recreate; two concurrent recreates left orphan
    containers on 2026-09-24. Same check as the deploy skill's step 3."""
    found = subprocess.run(
        ["pgrep", "-af", "docker( compose)? (build|up)"],  # noqa: S607
        text=True, capture_output=True, check=False,
    ).stdout.strip()
    if found:
        raise RuntimeError(f"an unlocked docker build/up is running; nothing was tagged:\n{found}")


def refuse_unpublished_commits() -> None:
    """The deploy checkout collects unpushed commits. Tagging one and pushing
    the tag would publish it past master, so HEAD must already be on
    `origin/master`."""
    run("git", "fetch", "-q", "origin")
    ahead = run("git", "rev-list", "--count", "origin/master..HEAD").strip()
    if ahead != "0":
        raise ValueError(
            f"HEAD has {ahead} commit(s) not on origin/master; land them by PR first")


def verify_voice_images() -> None:
    """`verify-deploy.py` checks only `nora:latest`; each voice service is read
    back against the image just built (docs/runbook.md 3b)."""
    for name in VOICE_IMAGES:
        running = run("docker", "inspect", "--format", "{{.Image}}", name).strip()
        built = run("docker", "image", "inspect", "--format", "{{.Id}}", f"{name}:latest").strip()
        if running != built:
            raise RuntimeError(f"{name} is not running the image just built")


def step(*args: str) -> None:
    """Run one deploy step with its output shown, raising on a nonzero exit."""
    subprocess.run(args, cwd=ROOT, check=True)  # noqa: S603 - fixed release commands only


def deploy(version: str) -> None:
    step(str(ROOT / "scripts" / "build-release-image.sh"), version)
    step("docker", "compose", "build", *VOICE_IMAGES)
    step("docker", "compose", "up", "-d", "--no-build", "--force-recreate", *RELEASE_SERVICES)
    step("python3", str(ROOT / "scripts" / "verify-deploy.py"))
    verify_voice_images()
    short = run("git", "rev-parse", "--short", "HEAD").strip()
    step("docker", "tag", "nora:latest", f"nora:sha-{short}")
    image = subprocess.run(
        ["docker", "image", "inspect", "-f", "{{.Id}}", "nora:latest"],  # noqa: S607
        cwd=ROOT, text=True, capture_output=True, check=True,
    ).stdout.strip()
    DEPLOY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEPLOY_LOG.open("a", encoding="utf-8") as log:
        log.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {short} {image}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-deploy", action="store_true",
                        help="tag only: no lock, build, deploy or push")
    args = parser.parse_args(argv)
    lock = None
    try:
        # Tracked files only: an untracked backup is not part of the release,
        # and the image's own stamp refuses untracked code under the
        # directories it copies (gen_version_manifest.IMAGE_DIRS).
        if run("git", "status", "--porcelain", "--untracked-files=no"):
            raise ValueError("refusing to release a dirty working tree")
        if not args.skip_deploy:
            lock = take_deploy_lock()
            refuse_unlocked_deploys()
            refuse_unpublished_commits()
        version = next_version()
        note = group_message(ROOT / "docs" / "releases" / f"{version}.md", version)
        run("git", "tag", "-a", version, "-m", note)
        if not args.skip_deploy:
            try:
                deploy(version)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                # A tag left behind makes the rerun ask for V<n+1>'s note, so
                # an undeployed version is untagged again before reporting.
                run("git", "tag", "-d", version)
                raise RuntimeError(
                    f"{version} did not deploy ({exc}); its tag was removed, "
                    "fix the cause and rerun") from exc
            try:
                run("git", "push", "origin", version)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"{version} is deployed and verified, but its tag was not pushed "
                    f"({exc}); run: git push origin {version}") from exc
        print(f"released {version}")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
