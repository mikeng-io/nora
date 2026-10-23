"""Entry point: config.toml + env -> Config -> WakeService.

Two sources, each owning what it should (AGENTS.md, the three config files):

- **config.toml** (`[speech.wake]`, and `[speech.stt]`'s endpoint / language /
  timeout) — the model knobs: threshold, model path, STT engine, silence
  tail, utterance ceiling, silence RMS. Bind-mounted read-only at
  `/app/config.toml` by compose, read once at boot (`config_toml.py`); a
  change needs a restart, like identity.toml and policy.toml. No env override
  for these — one place, one answer.
- **env** — bind addresses, `NORA_INGEST_URL`, and the shared
  `NORA_BRIDGE_TOKEN` that authenticates the seam hello and the utterance.
  NO deployment identity: the
  guild arrives per CONNECTION in the sidecar's hello frame, so this service
  is identical on every deployment and serves however many guilds connect to
  it. nora resolves that guild to the room marked `voice = true` in
  policy.toml, so a spoken utterance lands beside that server's typed chat.
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
    # No deployment identity is read here. The guild arrives per CONNECTION in
    # the sidecar's hello frame, so one gate serves every guild and nothing
    # per-guild is configured.
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
        bridge_token=e.get("NORA_BRIDGE_TOKEN", ""),
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
