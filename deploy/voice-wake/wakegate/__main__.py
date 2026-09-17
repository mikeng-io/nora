"""Entry point: config.toml + env -> Config -> WakeService.

Two sources, each owning what it should (AGENTS.md, the three config files):

- **config.toml** (`[speech.wake]`, and `[speech.stt]`'s endpoint / language /
  timeout) — the model knobs: threshold, model path, STT engine, silence
  tail, utterance ceiling, silence RMS. Bind-mounted read-only at
  `/app/config.toml` by compose, read once at boot (`config_toml.py`); a
  change needs a restart, like identity.toml and policy.toml. No env override
  for these — one place, one answer.
- **env** — deployment identity and bind addresses only: `VOICE_GUILD_ID`
  (a secrets-adjacent .env value), the listen/health binds,
  `NORA_INGEST_URL`. The GUILD is the whole identity this service needs
  (design/voice-channel/07 §12.2, reversed 2026-09-17): nora resolves it to
  the room the operator marked `voice = true`, so a spoken utterance lands in
  the same room as that server's typed chat, and adding a voice channel to
  the server changes nothing here. `VOICE_CHANNEL_ID` is still read by the
  audio-controller as its first-boot channel; this service ignores it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .config_toml import DEFAULT_PATH, load_sections
from .service import Config, WakeService

log = logging.getLogger(__name__)


def build_config(config_path: Path = DEFAULT_PATH, env: dict[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    wake, stt = load_sections(config_path)
    # VOICE_CHANNEL_ID is no longer read here (design/voice-channel/07 §12.2,
    # reversed 2026-09-17): the guild is what nora resolves a room from, and
    # the controller owns which channel it is in. The env var still exists —
    # the audio-controller reads it as its FIRST-BOOT channel.
    guild_id = e.get("VOICE_GUILD_ID", "")
    d = Config()  # code defaults; config.toml keys override them
    return Config(
        listen_host=e.get("WAKE_LISTEN_HOST", d.listen_host),
        listen_port=int(e.get("WAKE_LISTEN_PORT", d.listen_port)),
        health_host=e.get("WAKE_HEALTH_HOST", d.health_host),
        health_port=int(e.get("WAKE_HEALTH_PORT", d.health_port)),
        model_path=str(wake.get("model_path", d.model_path)),
        threshold=float(wake.get("threshold", d.threshold)),
        silence_tail_ms=int(wake.get("silence_tail_ms", d.silence_tail_ms)),
        max_utterance_s=float(wake.get("max_utterance_s", d.max_utterance_s)),
        silence_rms=float(wake.get("silence_rms", d.silence_rms)),
        vad_enabled=bool(wake.get("vad_enabled", d.vad_enabled)),
        vad_threshold=float(wake.get("vad_threshold", d.vad_threshold)),
        stt_url=str(stt.get("endpoint", d.stt_url)),
        stt_model=str(wake.get("stt_model", d.stt_model)),
        stt_language=str(stt.get("language", d.stt_language)),
        stt_timeout_s=float(stt.get("timeout_seconds", d.stt_timeout_s)),
        nora_url=e.get("NORA_INGEST_URL", d.nora_url),
        guild_id=guild_id,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = build_config()
    log.info(
        "effective config: model=%s threshold=%.2f stt=%s/%s lang=%s end-of-speech=%s "
        "silence_tail_ms=%d max_utterance_s=%.1f silence_rms=%.0f",
        cfg.model_path, cfg.threshold, cfg.stt_url, cfg.stt_model, cfg.stt_language,
        f"vad@{cfg.vad_threshold:.2f}" if cfg.vad_enabled else "rms",
        cfg.silence_tail_ms, cfg.max_utterance_s, cfg.silence_rms,
    )
    asyncio.run(WakeService(cfg).run())


if __name__ == "__main__":
    main()
