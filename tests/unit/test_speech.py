"""`SenseVoiceTranscriber` unit tests — the infra client's failure lanes.

The ingest wiring (does a voice note get transcribed, does a failure still
record the note) is `tests/unit/test_ingest_pipeline.py`'s job; this file is
about the client itself: the multipart contract, and that every failure mode
returns an unavailable `SttResult` instead of raising (R-27).
"""

from __future__ import annotations

from nora.domain.ports import SttResult
from nora.infra.speech import SenseVoiceTranscriber


class _FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {"text": "喂你今日得唔得閒啊"}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def json(self, **_: object) -> dict:
        return self._payload


class _FakeSession:
    """Replaces `aiohttp.ClientSession` enough for one POST per test."""

    def __init__(self, response: _FakeResponse | None = None,
                 exc: Exception | None = None) -> None:
        self.response = response or _FakeResponse()
        self.exc = exc
        self.posted: list[tuple[str, dict]] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.posted.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response


async def test_a_transcription_returns_the_hant_text(monkeypatch):
    import aiohttp
    session = _FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    t = SenseVoiceTranscriber(endpoint="http://stt:8000", language="yue-HK")

    result = await t.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result == SttResult(available=True, text="喂你今日得唔得閒啊")
    url, kwargs = session.posted[0]
    assert url == "http://stt:8000/v1/audio/transcriptions"
    body = kwargs["data"]
    # The default model is the noise-detection whisper — the multipart body must carry it, not a
    # hardcoded sensevoice.
    assert b"noise-detection" in body
    assert b"sensevoice" not in body
    assert b"yue-HK" in body
    assert b"audio/aac" in body


async def test_a_configured_model_is_the_one_the_multipart_carries(monkeypatch):
    """`model` is the operator's lever for the SenseVoice backup
    (`[speech.stt] model = "sensevoice"`) — the form field must carry the
    CONFIGURED id, whatever the default is."""
    import aiohttp
    session = _FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    t = SenseVoiceTranscriber(endpoint="http://stt:8000", model="sensevoice")

    result = await t.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result.available is True
    _, kwargs = session.posted[0]
    assert b"sensevoice" in kwargs["data"]
    assert b"noise-detection" not in kwargs["data"]


async def test_an_http_error_is_an_unavailable_result(monkeypatch):
    import aiohttp
    session = _FakeSession(response=_FakeResponse(status=500))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result.available is False
    assert result.reason == "error"


async def test_a_network_error_is_an_unavailable_result(monkeypatch):
    import aiohttp
    session = _FakeSession(exc=aiohttp.ClientConnectionError("refused"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result.available is False
    assert result.reason == "error"


async def test_a_timeout_is_an_unavailable_result(monkeypatch):
    import aiohttp
    session = _FakeSession(exc=TimeoutError("slow"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result.available is False
    assert result.reason == "timeout"


async def test_an_empty_transcript_is_a_distinct_reason_from_a_server_error(monkeypatch):
    """A silent/wordless voice note is a legitimate 200 with no words — NOT
    the same failure as an unreachable/erroring endpoint. Conflating the two
    would make `nora_speech_transcriptions_total{status="error"}` unable to
    tell an outage from an ordinary silent note."""
    import aiohttp
    session = _FakeSession(response=_FakeResponse(payload={"text": ""}))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert result.available is False
    assert result.reason == "empty"


async def test_a_non_audio_type_is_refused_without_a_call():
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="text/plain")

    assert result.available is False
    assert result.reason == "unsupported"


async def test_an_unresolved_media_type_is_let_through_not_refused(monkeypatch):
    """`media_type=""` means `resolve_media_type` could not tell what this
    is (no declared content-type, no resolvable filename) — NOT that it is
    known to be something other than audio. The caller already gated on
    `ref.voice_note`, so refusing here would silently drop a real voice note
    over a missing header rather than over the bytes themselves."""
    import aiohttp
    session = _FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    transcriber = SenseVoiceTranscriber(endpoint="http://stt:8000")

    result = await transcriber.transcribe(data=b"\x00\xff", media_type="")

    assert result == SttResult(available=True, text="喂你今日得唔得閒啊")
