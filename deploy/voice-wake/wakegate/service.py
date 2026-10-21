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
embedding windows), so each
user gets their OWN instance, created lazily on their first frame and
reaped after `idle_timeout_s` of silence. `model.reset()` after every
completed utterance keeps one utterance's features out of the next.

End of speech has TWO end-pointers. In-burst: the gate's own tail (VAD-quiet
chunks). Wall-clock: the sidecar's source is the Discord client, which sends
packets only while its mic gate is open, so after a phrase NO chunks arrive
and the gate cannot count anything. A per-user timer armed on every frame
while COLLECTING flushes the utterance once `silence_tail_ms` of real time
passes with no frame; without it a fired gate would sit collecting and swallow
every later 「喂 Nora」.
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
from .config import Config
from .gate import Utterance, WakeGate
from .selftest import log_self_test, run_self_test
from .stt import forward_utterance, transcribe

log = logging.getLogger("wakegate.service")

_CHUNK_MS = 80




@dataclass
class _UserState:
    """Everything the seam knows about one speaking user."""

    gate: WakeGate
    model: object  # openwakeword Model — typed as object: the import is lazy
    vad: object | None  # SileroSpeech (stateful) or None when RMS fallback is configured
    pending48: np.ndarray  # 48 kHz mono int16 not yet downsampled (< one block)
    frames: int = 0
    bytes_in: int = 0
    chunks: int = 0            # 80 ms blocks actually scored
    reported_chunks: int = 0   # chunks at the last scoring report
    wakes: int = 0
    utterances: int = 0
    last_seen: float = field(default_factory=time.time)
    inactivity: asyncio.TimerHandle | None = None  # armed while COLLECTING


def _load_model(model_path: str):
    """Blocking ONNX session setup — always called via asyncio.to_thread.

    openwakeword 0.4.0 (the pinned PyPI release — see pyproject.toml) is
    ONNX-only: no `inference_framework` kwarg, and the model list kwarg is
    `wakeword_model_paths`, not the 0.6.x `wakeword_models`."""
    from openwakeword.model import Model

    return Model(wakeword_model_paths=[model_path])


#: 80 ms chunks per scoring report — 50 is one line per 4 s of speech per
#: speaker, enough to see a near-miss and too few to drown the log.
_SCORE_REPORT_CHUNKS = 50

#: Seconds between `wake summary` lines. Five minutes: frequent enough that a
#: dead gate is visible the same day, rare enough to grep a week of them.
_SUMMARY_INTERVAL_S = 300.0


class WakeService:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._started_at = time.time()
        # KEYED BY (guild, user), not user. The wake
        # model is per-speaker stateful — openWakeWord owns rolling
        # melspectrogram/embedding windows — so two guilds sharing a key would
        # feed one model two interleaved audio streams. The same person in two
        # servers is two gates, which is correct: they are two conversations.
        self._users: dict[tuple[str, str], _UserState] = {}
        self._clients = 0
        self._server: asyncio.AbstractServer | None = None
        self._model_available = Path(config.model_path).is_file()
        self._selftest = None  # SelfTestResult once boot has run it
        self._last_summary = 0.0
        if not self._model_available:
            log.warning("wake model %s absent — scoring disabled (healthy-but-idle)",
                        config.model_path)

    # --- frame path ---------------------------------------------------------

    async def _user_state(self, key: tuple[str, str]) -> _UserState | None:
        state = self._users.get(key)
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
        self._users[key] = state
        log.info("wake gate armed for user %s in guild %s", key[1], key[0])
        return state

    async def _on_frame(self, key: tuple[str, str], pcm: bytes) -> None:
        state = await self._user_state(key)
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
            state.chunks += 1
            utterance = state.gate.feed(chunk)
            if utterance is not None:
                self._fire(key, utterance, state)
        self._report_scoring(key, state)
        self._arm_inactivity(key, state)

    def _report_scoring(self, key: tuple[str, str], state: _UserState) -> None:
        """What the model is SEEING, sampled.

        The gate scores every chunk, and without this a gate that stopped
        firing is indistinguishable from a gate hearing nothing. The peak over
        a window separates the two cases
        that need opposite fixes -- a peak near 0 means the audio reaching the
        model is not speech (a pipeline fault), a peak just under the threshold
        means it is speech the model nearly matched (a model or mic fault).

        Sampled per window, never per chunk: one line every 80 ms would be
        12.5 lines a second per speaker."""
        if state.chunks - state.reported_chunks < _SCORE_REPORT_CHUNKS:
            return
        state.reported_chunks = state.chunks
        peak = state.gate.take_listening_peak()
        log.info(
            "wake scoring: user=%s guild=%s chunks=%d window_peak=%.3f threshold=%.2f",
            key[1], key[0], state.chunks, peak, self._cfg.threshold,
        )

    def _fire(self, key: tuple[str, str], utterance: Utterance, state: _UserState) -> None:
        state.wakes += 1
        state.model.reset()
        if state.vad is not None:
            state.vad.reset()  # type: ignore[attr-defined]
        asyncio.create_task(self._handle_utterance(key, utterance, state))

    def _arm_inactivity(self, key: tuple[str, str], state: _UserState) -> None:
        """Re-arm the wall-clock end-pointer on every frame while COLLECTING;
        disarm the moment the gate is back in LISTENING."""
        if state.inactivity is not None:
            state.inactivity.cancel()
            state.inactivity = None
        if state.gate.collecting:
            state.inactivity = asyncio.get_running_loop().call_later(
                self._cfg.silence_tail_ms / 1000, self._on_inactivity, key, state,
            )

    def _on_inactivity(self, key: tuple[str, str], state: _UserState) -> None:
        state.inactivity = None
        if self._users.get(key) is not state:
            return  # reaped or replaced since the timer was armed
        utterance = state.gate.flush()
        if utterance is None:
            return
        log.info("utterance ended by inactivity for user %s (no frame for %d ms)",
                 key[1], self._cfg.silence_tail_ms)
        self._fire(key, utterance, state)

    async def _handle_utterance(
        self, key: tuple[str, str], utterance: Utterance, state: _UserState
    ) -> None:
        """Off the frame path: STT seconds must never stall the next user's
        20 ms frames. Text crosses to Nora; the audio dies here."""
        guild_id, user_id = key
        log.info("wake fired for user %s in guild %s (peak %.3f, %.1fs)",
                 user_id, guild_id, utterance.peak_score,
                 utterance.audio.size / 16000)
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
                text=text, guild_id=guild_id,
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
        # ONE PARSER PER CONNECTION, which is what carries the guild: the
        # sidecar announces it in a hello on connect, and a pump is created per
        # join, so a connection is one guild for its lifetime. That is how a
        # single gate serves every guild at once.
        parser = FrameParser()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                frames = parser.feed(chunk)
                if frames and parser.guild_id is None:
                    # Audio before the hello. Dropped, not guessed: attributing
                    # it to some other connection's guild would put one
                    # server's words in another server's room.
                    log.warning(
                        "seam client %s sent %d audio frame(s) before its hello "
                        "— dropped", peer, len(frames),
                    )
                    continue
                for user_id, pcm in frames:
                    await self._on_frame((parser.guild_id or "", user_id), pcm)
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
            for key in [k for k, s in self._users.items() if s.last_seen < cutoff]:
                log.info("reaping idle wake gate for user %s in guild %s",
                         key[1], key[0])
                state = self._users.pop(key)
                if state.inactivity is not None:
                    state.inactivity.cancel()

    # --- /health ------------------------------------------------------------

    def _health_payload(self) -> dict:
        return {
            "status": "ok",
            "wake": "listening" if self._model_available else "disabled",
            "clients": self._clients,
            # The guilds this gate is CURRENTLY serving, learned from the
            # connections' hellos rather than configured. One gate serves them
            # all, so a single `guild_id` could not say what it is doing.
            "guilds": sorted({g for g, _ in self._users}),
            "uptime_seconds": round(time.time() - self._started_at, 1),
            # Whether a KNOWN wake clip still wakes this process. `status:
            # "fail"` with everything else green is a gate that is up and
            # cannot wake.
            "selftest": self._selftest.as_json() if self._selftest is not None else None,
            "users": {
                f"{guild_id}/{user_id}": {
                    "frames": s.frames,
                    "bytes": s.bytes_in,
                    "wakes": s.wakes,
                    "utterances": s.utterances,
                }
                for (guild_id, user_id), s in self._users.items()
            },
        }

    # --- lifecycle ----------------------------------------------------------

    async def _run_boot_self_test(self) -> None:
        """Prove the wake path can wake, once, before any user speaks.

        Uses the REAL model and the real conditioning — see selftest.py for
        why a stub would defeat the purpose. Never fatal: a gate that cannot
        self-test still serves, and the operator's signal is the log line and
        /health rather than a crash loop that would destroy the evidence."""
        if not self._model_available:
            return  # healthy-but-idle: there is no scorer to test
        try:
            model = await asyncio.to_thread(_load_model, self._cfg.model_path)

            def score(chunk: np.ndarray, _model=model) -> float:
                return float(list(_model.predict(chunk).values())[0])

            self._selftest = await asyncio.to_thread(
                run_self_test, score=score, threshold=self._cfg.threshold,
            )
        except Exception as exc:  # noqa: BLE001
            from .selftest import SelfTestResult

            self._selftest = SelfTestResult(
                status="error", peak=0.0, threshold=self._cfg.threshold,
                detail=f"self-test could not run: {type(exc).__name__}: {exc}",
                elapsed_ms=0.0,
            )
        log_self_test(self._selftest)

    def _log_summary(self) -> None:
        """One greppable line saying whether the feature is alive.

        `wake summary` answers "is it dead?" without correlating anything: a
        line with arms climbing and wakes stuck at 0 IS the failure, and it is
        emitted whether or not anyone is speaking."""
        now = time.time()
        if now - self._last_summary < _SUMMARY_INTERVAL_S:
            return
        self._last_summary = now
        arms = sum(1 for _ in self._users)
        log.info(
            "wake summary: gates=%d clients=%d frames=%d chunks=%d wakes=%d "
            "utterances=%d selftest=%s uptime_s=%d",
            arms, self._clients,
            sum(s.frames for s in self._users.values()),
            sum(s.chunks for s in self._users.values()),
            sum(s.wakes for s in self._users.values()),
            sum(s.utterances for s in self._users.values()),
            self._selftest.status if self._selftest is not None else "none",
            round(now - self._started_at),
        )

    async def _summary_loop(self) -> None:
        while True:
            await asyncio.sleep(_SUMMARY_INTERVAL_S)
            try:
                self._log_summary()
            except Exception:  # noqa: BLE001 — telemetry must never kill the seam
                log.exception("wake summary failed")

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
        asyncio.create_task(self._summary_loop())
        # AFTER the listeners are up: the seam must not miss a sidecar that
        # reconnects while the model is loading, and /health must answer
        # during it. The result lands in the log and /health when it lands.
        asyncio.create_task(self._run_boot_self_test())
        log.info(
            "wake gate listening on %s:%s (model %s, threshold %.2f, -> %s)",
            self._cfg.listen_host, self._cfg.listen_port,
            self._cfg.model_path if self._model_available else "DISABLED",
            self._cfg.threshold, self._cfg.nora_url,
        )
        await asyncio.Event().wait()  # forever; the container's SIGTERM ends us
