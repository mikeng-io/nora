"""`MiniMaxTtsSpeaker` — reply text to Cantonese speech, via MiniMax's CN
TTS endpoint (design/voice-channel/01 §2, decisions in 04 §3b).

One POST against a CLOUD vendor (`api.minimaxi.com`, CN plan; `api.minimax.chat`
as the documented fallback host — the two were verified 2026-09-14 to be the
same backend answering the same key). The deliberate asymmetry with
`SenseVoiceTranscriber` is recorded in design/voice-channel/00: STT is inbound
audio and stays on the box, TTS is outbound text Nora has already decided is
fit to say aloud in a shared channel.

NEVER load-bearing and NEVER raises into a caller's turn, the same posture as
its transcription sibling: every failure — transport, timeout, non-200, a
refused voice, empty audio — returns an unavailable `SpeakResult`, and the
turn proceeds text-only. A reply that fails to sound is still a reply; a turn
is never dropped over TTS.

Facts that shaped the defaults (all measured 2026-09-14, design/voice-
channel/04 §3b): the model is `speech-2.8-turbo` (hd's 1.75x price buys
nothing measurable at reply lengths); `language_boost = "Chinese,Yue"` is
HARD-SET, never `auto` — `auto` mis-renders Cantonese function words (係→是,
唔該→嗯該); the voice is a native Cantonese one (`Cantonese_CuteGirl` as
configured), because the voice is the lever, not the model tier. Cost, for
the operator: MiniMax bills ~2 characters per CJK input char; turbo is
¥2.00 per 10k billed characters, so a typical short reply costs a fraction
of a fen. Not load-bearing — quoted so a surprise on the bill can be checked
against it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from nora.domain.ports import SpeakResult

log = logging.getLogger("nora.speech")

__all__ = ["MiniMaxTtsSpeaker"]

_DEFAULT_MODEL = "speech-2.8-turbo"
# Hard-set, never "auto" — see the module docstring for the measurement.
_LANGUAGE_BOOST = "Chinese,Yue"
_MEDIA_TYPE = "audio/mpeg"


class _TransportError(Exception):
    """One host could not be reached at all — the signal to try the fallback
    host. Distinct from a host that ANSWERED with an error: a refused request
    (bad voice, over quota) fails identically on both hosts, so retrying it
    would only double the bill."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MiniMaxTtsSpeaker:
    """One synthesis endpoint, constructed once by the composition root.

    Satisfies `nora.domain.ports.Speaker`. The wire contract is MiniMax's
    `POST /v1/t2a_v2` (non-streaming): the answer JSON carries the clip
    hex-encoded under `data.audio`, with the request's own status under
    `base_resp.status_code` — an HTTP 200 is NOT yet a success, and an
    `available` result requires both. `audio_setting` pins MP3 out, so
    `SpeakResult.media_type` is a fact about the request, not a guess at the
    bytes.
    """

    def __init__(
        self, *, api_key: str, voice: str, model: str = _DEFAULT_MODEL,
        host: str = "https://api.minimaxi.com",
        fallback_host: str = "https://api.minimax.chat",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        speed: float = 1.0,
        pitch: float = 0.0,
        vol: float = 1.0,
        emotion: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._voice = voice
        self._model = model
        self._speed = speed
        self._pitch = pitch
        self._vol = vol
        self._emotion = emotion
        self._hosts = (host.rstrip("/"), fallback_host.rstrip("/"))
        self._timeout = httpx.Timeout(timeout_seconds)
        # The seam a test drives: with no transport injected, httpx's own
        # `AsyncHTTPTransport` does real HTTPS. Everything ABOVE the transport
        # — request body, auth header, status/hex decoding, failure mapping —
        # is the real code path either way.
        self._transport = transport

    async def speak(self, *, text: str, voice: str = "") -> SpeakResult:
        """One synthesis attempt, or an unavailable result — never raises.

        Empty text short-circuits WITHOUT a call: it is the `SttResult`
        `empty` distinction again — nothing to say is not an outage, and a
        caller handing us whitespace must not reach the vendor's bill.
        """
        if not text.strip():
            return SpeakResult(available=False, reason="empty")
        body = {
            "model": self._model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice or self._voice,
                "speed": self._speed,
                "vol": self._vol,
                "pitch": self._pitch,
                **({"emotion": self._emotion} if self._emotion else {}),
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "language_boost": _LANGUAGE_BOOST,
        }
        reason = "error"
        for host in self._hosts:
            try:
                return await self._attempt(host, body)
            except _TransportError as exc:
                # The host itself could not be reached (DNS, refused,
                # timeout) — the documented ctrld failure mode of
                # api.minimaxi.com. The OTHER host may route fine.
                reason = exc.reason
                log.warning("tts host %s unreachable (%s)", host, reason)
            except asyncio.CancelledError:
                raise                   # shutdown is not a synthesis failure
            except Exception:
                log.exception("tts synthesis failed against %s", host)
                return SpeakResult(available=False, reason="error")
        return SpeakResult(available=False, reason=reason)

    async def _attempt(self, host: str, body: dict[str, Any]) -> SpeakResult:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport,
            ) as client:
                response = await client.post(
                    host + "/v1/t2a_v2",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise _TransportError("timeout") from exc
        except httpx.HTTPError as exc:
            raise _TransportError("error") from exc
        if response.status_code != 200:
            log.warning("tts refused (http %s)", response.status_code)
            return SpeakResult(available=False, reason="error")
        payload: Any = response.json()
        base: Any = (payload or {}).get("base_resp") or {}
        if base.get("status_code", 0) != 0:
            # The vendor's own refusal (auth, quota, an unknown voice) — the
            # call reached the server, so the fallback host would refuse
            # identically; do not retry it.
            log.warning(
                "tts refused by the api (status_code=%s: %s)",
                base.get("status_code"), base.get("status_msg"),
            )
            return SpeakResult(available=False, reason="error")
        hex_audio = ((payload.get("data") or {}).get("audio")) or ""
        if not hex_audio:
            # A legitimate success with no clip — kept distinct from `error`
            # so an outage and a silent synthesis never share one metric.
            log.info("tts returned empty audio")
            return SpeakResult(available=False, reason="empty")
        try:
            audio = bytes.fromhex(hex_audio)
        except ValueError:
            log.warning("tts returned audio that is not valid hex")
            return SpeakResult(available=False, reason="error")
        if not audio:
            log.info("tts returned empty audio")
            return SpeakResult(available=False, reason="empty")
        return SpeakResult(available=True, audio=audio, media_type=_MEDIA_TYPE)
