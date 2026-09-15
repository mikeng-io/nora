"""Entry point: env -> Config -> WakeService.

The wake gate is its own container and does NOT read Nora's config.toml —
every knob is an env var (compose maps the operator's VOICE_* .env names
onto them; see docker-compose.yml's `voice-wake` block). The one derived
value: `conversation_id` is Discord's flattened `"<guild_id>/<channel_id>"`
— the same shape `nora/platform/discord/parser.py` produces for typed
messages — built from VOICE_GUILD_ID + VOICE_CHANNEL_ID so a voice
utterance lands in the same conversation namespace as the channel's typed
chat.
"""

from __future__ import annotations

import asyncio
import logging
import os

from .service import Config, WakeService


def _from_env() -> Config:
    guild_id = os.environ.get("VOICE_GUILD_ID", "")
    channel_id = os.environ.get("VOICE_CHANNEL_ID", "")
    conversation_id = f"{guild_id}/{channel_id}" if guild_id and channel_id else channel_id
    return Config(
        listen_host=os.environ.get("WAKE_LISTEN_HOST", "0.0.0.0"),
        listen_port=int(os.environ.get("WAKE_LISTEN_PORT", "4599")),
        health_host=os.environ.get("WAKE_HEALTH_HOST", "127.0.0.1"),
        health_port=int(os.environ.get("WAKE_HEALTH_PORT", "4601")),
        model_path=os.environ.get("WAKE_MODEL_PATH", "/app/models/喂Nora.onnx"),
        threshold=float(os.environ.get("WAKE_THRESHOLD", "0.5")),
        silence_tail_ms=int(os.environ.get("WAKE_SILENCE_TAIL_MS", "1000")),
        max_utterance_s=float(os.environ.get("WAKE_MAX_UTTERANCE_S", "10")),
        silence_rms=float(os.environ.get("WAKE_SILENCE_RMS", "400")),
        stt_url=os.environ.get("STT_URL", "http://stt:8000"),
        stt_model=os.environ.get("STT_MODEL", "noise-detection"),
        stt_language=os.environ.get("STT_LANGUAGE", "yue"),
        stt_timeout_s=float(os.environ.get("STT_TIMEOUT_S", "30")),
        nora_url=os.environ.get("NORA_INGEST_URL", "http://nora:9100"),
        conversation_id=conversation_id,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(WakeService(_from_env()).run())


if __name__ == "__main__":
    main()
