"""Which STT engine to pre-load, read from Nora's config.toml.

Compose bind-mounts the operator's config.toml read-only at
`/app/config.toml` (as it does for nora and voice-wake). The engine warmed
at boot is the WAKE path's — `[speech.wake].stt_model` — because that is the
latency-bound caller; voice-note ingest names its own model per request
(`[speech.stt].model`), so it is unaffected by what is pre-loaded.

No env override on purpose: two sources (compose env and the config file) can
disagree. stdlib only; this container does not import nora.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

log = logging.getLogger("stt.config")

DEFAULT_PATH = Path("/app/config.toml")
FALLBACK_MODEL = "sensevoice"


def preload_model(path: Path = DEFAULT_PATH) -> str:
    """`[speech.wake].stt_model` from config.toml, defaulting only when the section is absent."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, UnicodeError):
        raise RuntimeError("config: unreadable") from None
    except tomllib.TOMLDecodeError:
        raise RuntimeError("config: invalid_toml") from None

    model = ((data.get("speech") or {}).get("wake") or {}).get("stt_model") or FALLBACK_MODEL
    log.info("config: stt preload selected")
    return str(model)
