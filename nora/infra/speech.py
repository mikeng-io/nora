"""`SenseVoiceTranscriber` — voice notes to Traditional-Chinese text, via the
local `stt` service (`deploy/stt/`, spec 2026-08-21).

One POST against Nora's OWN local service (private `nora` network, no host
port — audio never leaves the box). NEVER load-bearing and NEVER raises into
the ingest loop, the same posture as `LinkShortener`: every failure —
transport, timeout, non-200, an empty transcript — returns an unavailable
`SttResult`, and the caller records the voice note with a bare handle exactly
as it did before speech existed. A voice note that fails to transcribe is
still a voice note; a message is never dropped over STT.

The endpoint already applies OpenCC s2t (Simplified → Traditional), so the
text this returns is yue-Hant-HK-ready. `language` is a BCP-47 hint the
endpoint may use for LID; it never overrides detection, because the group
mixes Cantonese and English.
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
    string is just framing. `model` is fixed to `sensevoice`; the service
    pre-loads that model at boot.
    """

    def __init__(
        self, *, endpoint: str, language: str = "yue-HK", timeout_seconds: float = 30.0,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/transcriptions"
        self._language = language
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def transcribe(
        self, *, data: bytes, media_type: str, language: str = "",
    ) -> SttResult:
        """One transcription attempt, or an unavailable result — never raises.

        A `media_type` that is not `audio/*` is refused here (a song is not a
        voice note and a voice note is not a song — the caller is expected to
        gate on `ref.voice_note`, but this is the port's own belt).
        """
        if not media_type.startswith("audio/"):
            return SttResult(available=False, reason="unsupported")
        lang = language or self._language
        boundary = "----nora-stt"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"sensevoice\r\n"
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
            log.warning("stt returned an empty transcript")
            return SttResult(available=False, reason="error")
        return SttResult(available=True, text=text)
