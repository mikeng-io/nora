"""Read back what a deploy actually did, against every container on the image.

**The failure this exists for, measured 2026-09-16.** `presence` and
`analytics` run `nora:latest` — the SAME image as `nora`, a different command.
A `docker compose up -d --force-recreate nora` recreates one of the three, so
the other two keep running whatever image they were created from. When
`config.toml` then gained the voice work's `[speech.tts]` keys, the two stale
containers rejected them (`extra_forbidden`, a Pydantic schema that predates
the keys) and entered a restart loop: **279 restarts, `analytics` down for four
days.** Nothing in the deploy path looked.

**And detection was never the problem.** `TargetDown` fired the whole time,
Alertmanager routed it, and the bridge delivered it to Discord. It was not
acted on because `PlatformSilent` trips on 900s of ordinary quiet and flaps all
day, so the channel delivers every ~25 minutes and a four-day outage is
invisible inside it — the exact failure `deploy/alertmanager/alertmanager.yml`
predicts in its own comment: *"a repeat every few minutes is how an alert
channel becomes a muted one."*

So this is a READ-BACK at the moment of deploy, not another alert. An alert
competes for attention that is already exhausted; a check that fails the deploy
does not. Two questions, both answerable in a second, neither answerable by the
thing being checked:

1. **Is every container that runs this image actually ON the image that was
   just built?** A container recreated last week reports `Image: nora:latest`
   in `docker ps` while running a different image ID, and that display is why
   the skew is invisible.
2. **Is it up and healthy, a grace period after the recreate?** A container
   that parses the config wrongly does not fail the recreate — it exits, and
   `docker compose up` has already returned success by then.

Usage, after `scripts/build-image.sh` and the recreate:

    python3 scripts/verify-deploy.py            # nora:latest
    python3 scripts/verify-deploy.py --image nora:v4 --settle 30

Exits non-zero on skew or on any container not running, so it can be the last
line of a deploy and mean something.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

DEFAULT_IMAGE = "nora:latest"
DEFAULT_SETTLE_SECONDS = 20


def _docker(*args: str) -> str:
    r = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", *args], capture_output=True, text=True, check=False,  # noqa: S607
    )
    if r.returncode:
        raise SystemExit(f"docker {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def image_id(image: str) -> str:
    out = _docker("image", "inspect", image, "--format", "{{.Id}}").strip()
    if not out:
        raise SystemExit(f"no such image: {image}")
    return out


def containers_for(image: str) -> list[dict]:
    """Every container that ASKED for this image, running or not.

    The membership test is `Config.Image` — the reference given at create time,
    which stays `nora:latest` however far the tag has since moved. Three other
    candidates were tried and each misses the case this check exists for:

    - `--filter ancestor=` resolves the tag to an ID and matches on that, so a
      container on the STALE image is exactly what it excludes.
    - `docker ps --format {{.Image}}` prints a DISPLAY value: after a retag it
      may resolve to some other tag of the old ID, or to a bare ID. Measured
      2026-09-16 against a constructed skew — the container did not appear at
      all, and this function's first draft reported "nothing to verify" for a
      case it was written to catch.
    - `.Image` (the container's resolved ID) is what we compare AGAINST; using
      it to select would make the check tautological.

    `-a` and not only running: a container in a restart loop spends most of its
    life not running, and that is the case this exists for.
    """
    names = _docker("ps", "-a", "--format", "{{.Names}}").split()
    if not names:
        return []
    raw = _docker("container", "inspect", *names)
    out = []
    for c in json.loads(raw):
        if c["Config"]["Image"] != image:
            continue
        out.append({
            "name": c["Name"].lstrip("/"),
            "image_id": c["Image"],
            "declared": c["Config"]["Image"],
            "running": c["State"]["Running"],
            "health": (c["State"].get("Health") or {}).get("Status", "none"),
            "restarts": c["RestartCount"],
        })
    return sorted(out, key=lambda c: c["name"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--settle", type=int, default=DEFAULT_SETTLE_SECONDS,
                    help="seconds to wait before reading, so a crash loop has "
                         "time to show itself (default 20)")
    args = ap.parse_args(argv)

    want = image_id(args.image)
    if args.settle:
        time.sleep(args.settle)
    rows = containers_for(args.image)
    if not rows:
        print(f"no container declares {args.image} — nothing to verify", file=sys.stderr)
        return 2

    bad: list[str] = []
    print(f"{args.image} = {want[:19]}")
    for c in rows:
        stale = c["image_id"] != want
        down = not c["running"]
        mark = "STALE" if stale else ("DOWN" if down else "ok")
        print(
            f"  {mark:<5} {c['name']:<16} image={c['image_id'][:19]} "
            f"running={c['running']} health={c['health']} restarts={c['restarts']}"
        )
        if stale:
            bad.append(
                f"{c['name']} still runs a different image than {args.image}. "
                "It declares the same tag, so `docker ps` shows no difference. "
                f"Recreate it: docker compose up -d --force-recreate {c['name']}"
            )
        elif down:
            bad.append(
                f"{c['name']} is not running ({c['restarts']} restarts). "
                f"Read why: docker logs {c['name']} --tail 30"
            )
    if bad:
        print("\nDEPLOY IS NOT DONE:", file=sys.stderr)
        for line in bad:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"\nall {len(rows)} container(s) on the built image and running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
