"""`SenseVoiceTranscriber` — voice notes to Traditional-Chinese text, via the
local `stt` service (`deploy/stt/`).

The class name is historical: the service's DEFAULT model is the JackyHoCL
`whisper-large-v3-turbo-cantonese-noise-detection` whisper, with SenseVoice as
the config-selectable backup — `model` picks which. The client
itself is engine-agnostic: it speaks the OpenAI `/v1/audio/transcriptions`
contract either way.

One POST against Nora's OWN local service (private `nora` network, no host
port — audio never leaves the box). NEVER load-bearing and NEVER raises into
the ingest loop, the same posture as the link preview: every failure —
transport, timeout, non-200, an empty transcript — returns an unavailable
`SttResult`, and the caller records the voice note with a bare handle. A voice note that fails to
transcribe is still a voice note; a message is never dropped over STT.

The endpoint already applies OpenCC s2t (Simplified → Traditional), so the
text this returns is yue-Hant-HK-ready. `language` is one of the engines'
own short LID codes (`auto`/`zh`/`en`/`yue`/`ja`/`ko`/`nospeech`) — NOT a
BCP-47 tag; the model silently treats anything it does not recognize as
`auto`, so a tag like `yue-HK` would pin nothing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from nora.domain.ports import SttResult

log = logging.getLogger("nora.speech")

__all__ = ["SenseVoiceTranscriber"]


class SenseVoiceTranscriber:
    """One transcription endpoint, constructed once by the composition root.

    The multipart body is built by hand (not `aiohttp.FormData`) for two
    reasons: the OpenAI contract wants the audio under a `file` part with the
    filename, and the STT service reads the uploaded bytes as-is — a boundary
    string is just framing. `model` names which engine the service runs the
    audio through (see `deploy/stt/server.py`'s `MODEL_CONFIGS`) and defaults
    to `noise-detection`, the primary the service itself pre-loads at boot.
    """

    def __init__(
        self, *, endpoint: str, language: str = "yue", timeout_seconds: float = 30.0,
        model: str = "noise-detection",
    ) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/transcriptions"
        self._language = language
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def transcribe(
        self, *, data: bytes, media_type: str, language: str = "",
    ) -> SttResult:
        """One transcription attempt, or an unavailable result — never raises.

        A `media_type` that is KNOWN and is not `audio/*` is refused here (a
        song is not a voice note and a voice note is not a song — the caller
        is expected to gate on `ref.voice_note`, but this is the port's own
        belt). An UNRESOLVED type (`""` — the platform declared none and the
        filename didn't resolve via `resolve_media_type`) is let through: the
        caller already gated on `ref.voice_note`, and refusing here would
        silently drop a real voice note over a missing content-type header
        rather than over anything about the bytes themselves.
        """
        if media_type and not media_type.startswith("audio/"):
            return SttResult(available=False, reason="unsupported")
        lang = language or self._language
        boundary = "----nora-stt"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"{self._model}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="language"\r\n\r\n'
            f"{lang}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="voice-note"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    self._url,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    data=body,
                ) as response,
            ):
                if response.status != 200:
                    log.warning("stt refused (http %s)", response.status)
                    return SttResult(available=False, reason="error")
                payload: Any = await response.json(content_type=None)
                text = (payload or {}).get("text") or ""
        except asyncio.CancelledError:
            raise                       # shutdown is not a transcription failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.warning("stt unreachable (%s)", type(exc).__name__)
            reason = "timeout" if isinstance(exc, TimeoutError) else "error"
            return SttResult(available=False, reason=reason)
        except Exception:
            log.exception("transcribe failed")
            return SttResult(available=False, reason="error")

        if not text:
            # A legitimate 200 with no words (silent/wordless voice note) is
            # NOT the same failure as an unreachable/erroring endpoint — kept
            # a distinct reason so the metric can tell them apart.
            log.info("stt returned an empty transcript")
            return SttResult(available=False, reason="empty")
        return SttResult(available=True, text=text)
