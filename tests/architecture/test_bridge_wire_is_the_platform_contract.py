"""No file names a route of the bridge wire the `platform` contract replaced.

The app and every bridge switch together and nothing serves both wires, so a
path of the old wire anywhere is a caller, a fake or a document that would
answer or expect a 404. This file is the only one allowed to spell them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("nora/", "deploy/", "tests/", "scripts/", "design/platform-bridges/",
           "docs/runbook.md", "docker-compose.yml")
OLD_ROUTES = ("/v1/actions", "/v1/reads", "/v1/attachments", "/v1/self",
              "/v1/groups", "/v1/guilds")


def _tracked() -> list[Path]:
    names = subprocess.run(  # noqa: S603 — fixed argv
        ["git", "ls-files", "-z", "--", *SCANNED],
        cwd=ROOT, check=True, capture_output=True).stdout.decode().split("\0")
    own = Path(__file__).resolve()
    return [ROOT / name for name in names if name and (ROOT / name).resolve() != own]


def test_the_scan_reads_the_bridges_and_the_client() -> None:
    """Positive control: the scan covers the files that carried the old wire."""
    scanned = {path.relative_to(ROOT).as_posix() for path in _tracked()}
    assert {"nora/platform/bridge/client.py", "deploy/signal-bridge/wrapper.py",
            "docker-compose.yml", "docs/runbook.md"} <= scanned


def test_no_old_bridge_route_is_named() -> None:
    named = []
    for path in _tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        named += [f"{path.relative_to(ROOT)}: {route}" for route in OLD_ROUTES if route in text]
    assert not named, "the old bridge wire is still named:\n  " + "\n  ".join(named)
