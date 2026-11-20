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
    """`[speech.wake].stt_model` from config.toml, else the built-in fallback."""
    if not path.is_file():
        log.warning("config.toml not found at %s — pre-loading %s", path, FALLBACK_MODEL)
        return FALLBACK_MODEL
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    model = ((data.get("speech") or {}).get("wake") or {}).get("stt_model") or FALLBACK_MODEL
    log.info("config.toml %s: pre-loading [speech.wake].stt_model=%s", path, model)
    return str(model)


def qwen_settings(path: Path = DEFAULT_PATH) -> tuple[str, float, bool]:
    """Device/deadline and whether either independent role selects Qwen."""
    data = tomllib.loads(path.read_text()) if path.is_file() else {}
    speech = data.get("speech", {})
    stt, wake = speech.get("stt", {}), speech.get("wake", {})
    device = stt.get("qwen", {}).get("device", "cpu")
    if device not in ("cpu", "vulkan"):
        raise ValueError("speech.stt.qwen.device must be cpu or vulkan")
    timeout = float(stt.get("timeout_seconds", 30))
    if not 0 < timeout < float("inf"):
        raise ValueError("invalid STT deadline")
    selected = "qwen3-asr-hk-0.6b" in (stt.get("model"), wake.get("stt_model"))
    return device, timeout, selected
