"""Read the wake gate's knobs from Nora's config.toml.

The file is the operator's single source for "ports, models, timeouts"
(AGENTS.md, the three config files). Compose bind-mounts it read-only at
`/app/config.toml`, exactly as it does for the nora container; this module
reads `[speech.wake]` and the shared `[speech.stt]` endpoint/language/timeout
once at startup. A missing file or section falls back to the code defaults
in `service.Config`, so a fresh clone still boots healthy-but-idle.

Deliberately no env override for these keys: two sources can give two answers.
What stays in env is deployment identity and bind addresses — see `__main__`.

stdlib only (tomllib, py>=3.11): this container does not import nora.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("/app/config.toml")


def load_sections(path: Path = DEFAULT_PATH) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (`[speech.wake]`, `[speech.stt]`) as plain dicts; empty when absent.

    Raises on a malformed file: a broken config.toml must not be silently
    replaced by defaults.
    """
    if not path.is_file():
        log.warning("config.toml not found at %s — using built-in defaults", path)
        return {}, {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    speech = data.get("speech", {}) or {}
    wake = dict(speech.get("wake", {}) or {})
    stt = dict(speech.get("stt", {}) or {})
    log.info("config.toml loaded from %s: [speech.wake] keys=%s, [speech.stt] keys=%s",
             path, sorted(wake), sorted(stt))
    return wake, stt
