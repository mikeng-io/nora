"""`Transcriber` has two implementations, so it gets one suite both pass —
CLAUDE.md's rule, and the reason `FakeTranscriber` cannot quietly drift into
answering differently from `SenseVoiceTranscriber`. Mirrors
`test_media_interpreter_contract.py`'s shape for `MediaInterpreter`.

`SenseVoiceTranscriber.transcribe` refuses a known non-audio type
(`unsupported`) before any network call goes out, so `FakeTranscriber` must
gate on `media_type` too — otherwise the fake masks the omission, the gap
`test_media_interpreter_contract.py`'s own docstring names. Each test below is
written against the PORT, parametrized over both concrete classes, so that
divergence cannot happen silently.
"""

from __future__ import annotations

import pytest

from nora.domain.ports import SttResult, Transcriber
from nora.infra.speech import SenseVoiceTranscriber
from nora.testing import FakeTranscriber


class _FakeResponse:
    """Local to this file on purpose — a shared double living in
    `tests/unit/test_speech.py` would make this contract suite depend on
    another test module's internals instead of standing on its own."""

    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload if payload is not None else {"text": "喂你今日得唔得閒啊"}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def json(self, **_: object) -> dict:
        return self._payload


class _FakeSession:
    """Replaces `aiohttp.ClientSession` enough for one POST per test."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.posted: list[tuple[str, dict]] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.posted.append((url, kwargs))
        return self.response


def _make_fake(*, available: bool, text: str):
    return FakeTranscriber(text, available=available, reason="" if available else "error")


def _make_sense_voice(*, available: bool, text: str, monkeypatch: pytest.MonkeyPatch):
    import aiohttp

    response = _FakeResponse(payload={"text": text}) if available else _FakeResponse(status=500)
    session = _FakeSession(response=response)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    return SenseVoiceTranscriber(endpoint="http://stt:8000")


@pytest.fixture(params=["fake", "sensevoice"])
def make_transcriber(request, monkeypatch: pytest.MonkeyPatch):
    """A factory, not a bare instance: each test needs a DIFFERENT underlying
    result (available/unavailable), and the two implementations reach that
    result through different constructors — `FakeTranscriber` takes it
    directly, `SenseVoiceTranscriber` takes it via the stubbed HTTP session
    it wraps."""
    if request.param == "fake":
        return lambda *, available=True, text="喂你今日得唔得閒啊": _make_fake(
            available=available, text=text,
        )
    return lambda *, available=True, text="喂你今日得唔得閒啊": _make_sense_voice(
        available=available, text=text, monkeypatch=monkeypatch,
    )


async def test_a_known_non_audio_media_type_is_refused_before_any_call(make_transcriber):
    """A song is not a voice note — the port's OWN belt, in
    addition to the caller's `ref.voice_note` gate. Refused BEFORE any
    underlying call could matter, regardless of what the underlying result
    would have been had it been asked."""
    transcriber: Transcriber = make_transcriber(available=True, text="a lyric")

    got = await transcriber.transcribe(data=b"\x00\xff", media_type="image/png")

    assert got.available is False
    assert got.reason == "unsupported"
    assert got.text == ""


async def test_an_unresolved_media_type_is_let_through(make_transcriber):
    """`media_type=""` means `resolve_media_type` could not tell what this
    is — NOT that it is known to be something other than audio. The caller
    already gated on `ref.voice_note`, so this must transcribe normally
    rather than refuse over a missing content-type header."""
    transcriber: Transcriber = make_transcriber(available=True, text="喂你今日得唔得閒啊")

    got = await transcriber.transcribe(data=b"\x00\xff", media_type="")

    assert got == SttResult(available=True, text="喂你今日得唔得閒啊")


async def test_an_available_result_carries_its_text(make_transcriber):
    transcriber: Transcriber = make_transcriber(available=True, text="今晚食飯未呀")

    got = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert got == SttResult(available=True, text="今晚食飯未呀")


async def test_an_unavailable_result_never_becomes_a_blank_transcript(make_transcriber):
    """R-27/R-51 shape. A failure must not turn into a guessed or blank
    transcript riding as if it were real — `text` stays empty and `reason`
    carries why, for both implementations alike."""
    transcriber: Transcriber = make_transcriber(available=False, text="")

    got = await transcriber.transcribe(data=b"\x00\xff", media_type="audio/aac")

    assert got.available is False
    assert got.text == ""
    assert got.reason != ""


async def test_transcribe_never_raises(make_transcriber):
    """Ports never raise (`nora/domain/ports.py`'s `Transcriber` docstring).
    Neither empty bytes nor an empty/malformed `media_type` may propagate an
    exception — both must resolve to a typed result instead."""
    transcriber: Transcriber = make_transcriber(available=True, text="fine")

    got = await transcriber.transcribe(data=b"", media_type="")

    assert isinstance(got, SttResult)
