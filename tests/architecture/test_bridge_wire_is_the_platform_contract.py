"""No file names a route of the bridge wire the `platform` contract replaced,
nor one of the voice wire the `voice` and `intake` contracts replaced.

The app and every bridge switch together and nothing serves both wires, so a
path of the old wire anywhere is a caller, a fake or a document that would
answer or expect a 404. This file is the only one allowed to spell them.
The same holds for the voice connection: the app, the Discord bridge, the
audio controller and the wake gate switch together.
"""

from __future__ import annotations

import re
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


VOICE_SCANNED = (*SCANNED, "design/voice-channel/", "design/09-decision-index.md")
#: Phase 6 owns it while it is open; its docstring still names the old status read.
VOICE_EXEMPT = frozenset({"nora/config.py"})
OLD_VOICE_ROUTES = ("/voice/join", "/voice/exit", "/voice/status", "/voice/event",
                    "/voice/utterance", "/app/v1/voice")
OLD_VOICE_PATTERNS = (
    re.compile(r"(?<!/v1)/music/(?:play|stop|pause|resume|volume)"),
    # A whole path segment after `/v1/voice/` that is not one of the contract's.
    re.compile(r"/v1/voice/(?!(?:operations|queries)(?![\w-]))(?:\{\w+\}|[\w-]+)"),
)
#: The controller's old speech route, named only where a voice caller or server lives.
QUOTED_STREAM = re.compile(r"""["'`]/stream["'`]""")
STREAM_SCOPES = ("nora/infra/voice/", "nora/platform/discord/", "nora/testing/",
                 "deploy/voice-sidecar/", "deploy/discord-bridge/")


def _voice_scanned() -> list[tuple[str, str]]:
    names = subprocess.run(  # noqa: S603 — fixed argv
        ["git", "ls-files", "-z", "--", *VOICE_SCANNED],
        cwd=ROOT, check=True, capture_output=True).stdout.decode().split("\0")
    own = Path(__file__).resolve()
    texts = []
    for name in sorted(set(names) - VOICE_EXEMPT - {""}):
        path = ROOT / name
        if path.resolve() == own:
            continue
        try:
            texts.append((name, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
    return texts


def test_no_old_voice_route_is_named() -> None:
    scanned = _voice_scanned()
    assert {"nora/infra/voice/http_controller.py", "nora/testing/voice.py",
            "design/voice-channel/audio.md", "docker-compose.yml"} <= {n for n, _ in scanned}
    named = []
    for name, text in scanned:
        named += [f"{name}: {route}" for route in OLD_VOICE_ROUTES if route in text]
        named += [f"{name}: {m.group()}" for p in OLD_VOICE_PATTERNS for m in p.finditer(text)]
        if name.startswith(STREAM_SCOPES):
            named += [f"{name}: {m.group()}" for m in QUOTED_STREAM.finditer(text)]
    assert not named, "the old voice wire is still named:\n  " + "\n  ".join(named)


def test_the_voice_rule_reads_whole_segments() -> None:
    """Positive control for the `/v1/voice/` rule."""
    rule = OLD_VOICE_PATTERNS[1]
    named = ["/v1/voice/operations-legacy", "/v1/voice/{guild_id}/join", "/v1/voice/event"]
    clean = ["/v1/voice/operations", "/v1/voice/operations/{operation_id}",
             "${URL:-http://bridge/v1/voice/operations}",
             "/v1/voice/queries", 'startswith("/v1/voice/")']
    assert all(rule.search(text) for text in named)
    assert not any(rule.search(text) for text in clean)
