"""The wake-gate service wiring: TCP seam listener + per-user gates + /health.

One `asyncio.start_server` is the ONLY consumer of the sidecar's seam on the
private `nora` network (the sidecar connects OUT to `PUMP_HOST:PUMP_PORT` —
here). Per user: sidecar frames are conditioned to 16 kHz mono int16 80 ms
chunks (`wakegate.audio`), driven through a per-user `WakeGate`, and a fired
gate's utterance is transcribed by the local `stt` service and forwarded to
Nora as TEXT — audio is transient end to end, never written anywhere.

Posture mirrors the sidecar and `stt`: healthy-but-idle. With no model file
the service still answers /health 200 (`wake: "disabled"`) and consumes
frames without scoring — a deployment without the wake model starts clean,
like a deployment without a bot token starts the sidecar clean.

openWakeWord's `Model` is stateful (it owns the rolling melspectrogram and
embedding windows the brief calls the per-user 16-frame window), so each
user gets their OWN instance, created lazily on their first frame and
reaped after `idle_timeout_s` of silence. `model.reset()` after every
completed utterance keeps one utterance's features out of the next.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import web

from .audio import BLOCK_48K, OWW_CHUNK, downsample_to_16k, pcm16_to_wav, stereo_to_mono
from .codec import FrameParser
from .gate import Utterance, WakeGate
from .stt import forward_utterance, transcribe

log = logging.getLogger("wakegate.service")

_CHUNK_MS = 80


@dataclass(frozen=True)
class Config:
    listen_host: str = "0.0.0.0"
    listen_port: int = 4599
    health_host: str = "127.0.0.1"
    health_port: int = 4601
    model_path: str = "/app/models/喂Nora.onnx"
    threshold: float = 0.5
    silence_tail_ms: int = 800       # no-speech tail that ends an utterance (1000 in the RMS era)
    max_utterance_s: float = 10.0
    silence_rms: float = 400.0       # RMS fallback when vad_enabled is false
    vad_enabled: bool = True         # Silero VAD decides "finished speaking"; noise is not speech
    vad_threshold: float = 0.5       # speech probability at/above this = speech
    stt_url: str = "http://stt:8000"
    stt_model: str = "noise-detection"
    stt_language: str = "yue"
    stt_timeout_s: float = 30.0
    nora_url: str = "http://nora:9100"
    conversation_id: str = ""
    idle_timeout_s: float = 600.0


@dataclass
class _UserState:
    """Everything the seam knows about one speaking user."""

    gate: WakeGate
    model: object  # openwakeword Model — typed as object: the import is lazy
    vad: object | None  # SileroSpeech (stateful) or None when RMS fallback is configured
    pending48: np.ndarray  # 48 kHz mono int16 not yet downsampled (< one block)
    frames: int = 0
    bytes_in: int = 0
    wakes: int = 0
    utterances: int = 0
    last_seen: float = field(default_factory=time.time)


def _load_model(model_path: str):
    """Blocking ONNX session setup — always called via asyncio.to_thread.

    openwakeword 0.4.0 (the pinned PyPI release — see pyproject.toml) is
    ONNX-only: no `inference_framework` kwarg, and the model list kwarg is
    `wakeword_model_paths`, not the 0.6.x `wakeword_models`."""
    from openwakeword.model import Model

    return Model(wakeword_model_paths=[model_path])


class WakeService:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._started_at = time.time()
        self._users: dict[str, _UserState] = {}
        self._clients = 0
        self._server: asyncio.AbstractServer | None = None
        self._model_available = Path(config.model_path).is_file()
        if not self._model_available:
            log.warning("wake model %s absent — scoring disabled (healthy-but-idle)",
                        config.model_path)

    # --- frame path ---------------------------------------------------------

    async def _user_state(self, user_id: str) -> _UserState | None:
        state = self._users.get(user_id)
        if state is not None:
            return state
        if not self._model_available:
            return None
        model = await asyncio.to_thread(_load_model, self._cfg.model_path)

        def score(chunk: np.ndarray, _model=model) -> float:
            return float(list(_model.predict(chunk).values())[0])

        vad = None
        if self._cfg.vad_enabled:
            from .vad import SileroSpeech  # onnxruntime-backed; loaded per user, stateful

            vad = await asyncio.to_thread(SileroSpeech, self._cfg.vad_threshold)

        state = _UserState(
            gate=WakeGate(
                score=score,
                threshold=self._cfg.threshold,
                silence_tail_chunks=max(1, round(self._cfg.silence_tail_ms / _CHUNK_MS)),
                max_chunks=max(1, round(self._cfg.max_utterance_s * 1000 / _CHUNK_MS)),
                silence_rms=self._cfg.silence_rms,
                speech=vad,
            ),
            model=model,
            vad=vad,
            pending48=np.empty(0, dtype=np.int16),
        )
        self._users[user_id] = state
        log.info("wake gate armed for user %s", user_id)
        return state

    async def _on_frame(self, user_id: str, pcm: bytes) -> None:
        state = await self._user_state(user_id)
        if state is None:
            return  # no model — counted nowhere, dropped (healthy-but-idle)
        state.frames += 1
        state.bytes_in += len(pcm)
        state.last_seen = time.time()
        mono = stereo_to_mono(pcm)
        state.pending48 = np.concatenate([state.pending48, mono])
        while state.pending48.size >= BLOCK_48K:
            block, state.pending48 = state.pending48[:BLOCK_48K], state.pending48[BLOCK_48K:]
            chunk = downsample_to_16k(block)
            assert chunk.size == OWW_CHUNK
            utterance = state.gate.feed(chunk)
            if utterance is not None:
                state.wakes += 1
                state.model.reset()
                if state.vad is not None:
                    state.vad.reset()  # type: ignore[attr-defined]
                asyncio.create_task(self._handle_utterance(user_id, utterance, state))

    async def _handle_utterance(
        self, user_id: str, utterance: Utterance, state: _UserState
    ) -> None:
        """Off the frame path: STT seconds must never stall the next user's
        20 ms frames. Text crosses to Nora; the audio dies here."""
        log.info("wake fired for user %s (peak %.3f, %.1fs)",
                 user_id, utterance.peak_score, utterance.audio.size / 16000)
        if not self._cfg.conversation_id:
            log.warning("no VOICE_CHANNEL_ID conversation — utterance dropped")
            return
        wav = pcm16_to_wav(utterance.audio)
        timeout = aiohttp.ClientTimeout(total=self._cfg.stt_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            text = await transcribe(
                session, stt_url=self._cfg.stt_url, model=self._cfg.stt_model,
                language=self._cfg.stt_language, wav=wav,
            )
            if text is None:
                return  # no wake -> no cost; dropped silently by design
            ok = await forward_utterance(
                session, nora_url=self._cfg.nora_url, user_id=user_id,
                text=text, conversation_id=self._cfg.conversation_id,
            )
        if ok:
            state.utterances += 1

    # --- TCP seam -----------------------------------------------------------

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients += 1
        peer = writer.get_extra_info("peername")
        log.info("seam client connected: %s", peer)
        parser = FrameParser()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                for user_id, pcm in parser.feed(chunk):
                    await self._on_frame(user_id, pcm)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("seam client %s failed", peer)
        finally:
            self._clients -= 1
            writer.close()
            log.info("seam client disconnected: %s", peer)

    async def _reap_idle_users(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.time() - self._cfg.idle_timeout_s
            for user_id in [u for u, s in self._users.items() if s.last_seen < cutoff]:
                log.info("reaping idle wake gate for user %s", user_id)
                del self._users[user_id]

    # --- /health ------------------------------------------------------------

    def _health_payload(self) -> dict:
        return {
            "status": "ok",
            "wake": "listening" if self._model_available else "disabled",
            "clients": self._clients,
            "conversation_id": self._cfg.conversation_id or None,
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "users": {
                user_id: {
                    "frames": s.frames,
                    "bytes": s.bytes_in,
                    "wakes": s.wakes,
                    "utterances": s.utterances,
                }
                for user_id, s in self._users.items()
            },
        }

    # --- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        health_app = web.Application()
        health_app.router.add_get("/health", lambda request: web.json_response(self._health_payload()))
        runner = web.AppRunner(health_app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, self._cfg.health_host, self._cfg.health_port).start()
        self._server = await asyncio.start_server(
            self._on_client, self._cfg.listen_host, self._cfg.listen_port,
        )
        asyncio.create_task(self._reap_idle_users())
        log.info(
            "wake gate listening on %s:%s (model %s, threshold %.2f, -> %s)",
            self._cfg.listen_host, self._cfg.listen_port,
            self._cfg.model_path if self._model_available else "DISABLED",
            self._cfg.threshold, self._cfg.nora_url,
        )
        await asyncio.Event().wait()  # forever; the container's SIGTERM ends us
