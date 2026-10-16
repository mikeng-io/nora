"""`MiniMaxTtsSpeaker` unit tests — the infra client's failure lanes.

Mirrors `tests/unit/test_speech.py`'s posture (R-27): every failure mode
returns an unavailable `SpeakResult` instead of raising, and `empty` stays
distinct from `error`. The seam is httpx's OWN `MockTransport` injected
through the constructor — the real `AsyncClient`, the real request building
and the real response parsing run either way; only the socket is staged. The
handler asserts the wrong-call shapes (path, auth header, body) rather than
answering every request the same way.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from nora.agent.speech_format import SpeechFormatter
from nora.domain.ports import SpeakResult
from nora.infra.tts import MiniMaxTtsSpeaker
from nora.obs.metrics import Metrics

_CLIP = b"\xff\xfb\x90\x00fake-mp3-frames"


def _ok_payload(audio: bytes = _CLIP) -> dict:
    return {
        "data": {"audio": audio.hex(), "status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
        "trace_id": "trace-1",
    }


class _Server:
    """Replays the wire shapes of `POST /v1/t2a_v2`, including the refusal
    modes, and records every request so a test can see WHICH host was tried
    and with what body."""

    def __init__(
        self, *, status: int = 200, payload: dict | None = None,
        raw: str | None = None, exc: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload if payload is not None else _ok_payload()
        self.raw = raw
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        if self.raw is not None:
            return httpx.Response(self.status, text=self.raw)
        return httpx.Response(self.status, json=self.payload)


def _speaker(server: _Server, **kwargs: object) -> MiniMaxTtsSpeaker:
    return MiniMaxTtsSpeaker(
        api_key="sk-cp-test", voice="Cantonese_CuteGirl",
        transport=httpx.MockTransport(server.handler), **kwargs,  # type: ignore[arg-type]
    )


async def test_a_synthesis_returns_the_clip_and_the_wire_shape(monkeypatch):
    server = _Server()
    speaker = _speaker(server)

    result = await speaker.speak(text="喂，我係 Nora。")

    assert result == SpeakResult(available=True, audio=_CLIP, media_type="audio/mpeg")
    (request,) = server.requests
    assert request.url.path == "/v1/t2a_v2"
    assert request.headers["Authorization"] == "Bearer sk-cp-test"
    body = json.loads(request.content)
    assert body["model"] == "speech-2.8-turbo"
    assert body["voice_setting"]["voice_id"] == "Cantonese_CuteGirl"
    # Hard-set, never "auto" — the measurement behind it is in the adapter's
    # docstring; if this line is ever "auto" the Cantonese function words go.
    assert body["language_boost"] == "Chinese,Yue"
    assert body["audio_setting"]["format"] == "mp3"


async def test_a_per_call_voice_overrides_the_configured_default():
    server = _Server()
    speaker = _speaker(server)

    result = await speaker.speak(text="唔該晒", voice="Cantonese_KindWoman")

    assert result.available is True
    body = json.loads(server.requests[0].content)
    assert body["voice_setting"]["voice_id"] == "Cantonese_KindWoman"


async def test_voice_setting_knobs_ride_the_wire_and_emotion_stays_off_when_unset():
    """The speed/pitch/vol knobs must reach the request
    body with their exact types (pitch is MiniMax's one INTEGER knob —
    `0.0` would be a type error on the default path), and an unset
    emotion must stay OFF the wire so the vendor default applies."""
    server = _Server()
    speaker = _speaker(server, speed=1.3, pitch=2, vol=0.8)

    result = await speaker.speak(text="喂")

    assert result.available is True
    body = json.loads(server.requests[0].content)
    vs = body["voice_setting"]
    assert vs["speed"] == 1.3
    assert vs["pitch"] == 2
    assert type(vs["pitch"]) is int
    assert vs["vol"] == 0.8
    assert "emotion" not in vs


async def test_an_emotion_rides_the_wire_when_set():
    server = _Server()
    speaker = _speaker(server, emotion="fluent")

    result = await speaker.speak(text="喂")

    assert result.available is True
    vs = json.loads(server.requests[0].content)["voice_setting"]
    assert vs["emotion"] == "fluent"


async def test_an_http_error_is_an_unavailable_result():
    speaker = _speaker(_Server(status=500, raw="boom"))

    result = await speaker.speak(text="喂")

    assert result.available is False
    assert result.reason == "error"


async def test_an_api_refusal_is_an_error_and_the_fallback_host_is_not_billed():
    """`base_resp.status_code != 0` (an unknown voice, over quota) means the
    server ANSWERED — the fallback host would refuse identically, so it must
    not see a second request."""
    server = _Server(payload={
        "data": None,
        "base_resp": {"status_code": 2013, "status_msg": "invalid params"},
    })
    speaker = _speaker(server)

    result = await speaker.speak(text="喂", voice="No_Such_Voice")

    assert result.available is False
    assert result.reason == "error"
    assert len(server.requests) == 1


async def test_a_transport_failure_falls_over_to_the_second_host():
    """The documented ctrld failure mode: `api.minimaxi.com` unresolvable,
    `api.minimax.chat` fine. The fallback is for hosts that cannot be
    REACHED, which is exactly what a transport error means."""
    down = _Server(exc=httpx.ConnectError("name or service not known"))
    up = _Server()
    speaker = MiniMaxTtsSpeaker(
        api_key="sk-cp-test", voice="Cantonese_CuteGirl",
        transport=httpx.MockTransport(
            lambda request: down.handler(request)
            if request.url.host == "api.minimaxi.com"
            else up.handler(request)
        ),
    )

    result = await speaker.speak(text="喂")

    assert result.available is True
    assert len(down.requests) == 1
    assert len(up.requests) == 1


async def test_a_transport_failure_on_both_hosts_is_an_unavailable_result():
    speaker = _speaker(_Server(exc=httpx.ConnectError("refused")))

    result = await speaker.speak(text="喂")

    assert result.available is False
    assert result.reason == "error"


async def test_a_timeout_is_an_unavailable_result_with_its_own_reason():
    speaker = _speaker(_Server(exc=httpx.ReadTimeout("slow")))

    result = await speaker.speak(text="喂")

    assert result.available is False
    assert result.reason == "timeout"


async def test_empty_audio_is_a_distinct_reason_from_a_server_error():
    """A legitimate success carrying no clip is NOT the same failure as an
    unreachable/erroring endpoint — the same distinction `SttResult` keeps,
    so the metric can tell an outage from a silent synthesis."""
    speaker = _speaker(_Server(payload=_ok_payload(audio=b"")))

    result = await speaker.speak(text="喂")

    assert result.available is False
    assert result.reason == "empty"


async def test_empty_text_short_circuits_without_a_call():
    server = _Server()
    speaker = _speaker(server)

    result = await speaker.speak(text="   ")

    assert result.available is False
    assert result.reason == "empty"
    assert server.requests == []


async def test_audio_that_is_not_valid_hex_is_an_error_not_a_raise():
    speaker = _speaker(_Server(payload={
        "data": {"audio": "not-hex-at-all", "status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }))

    result = await speaker.speak(text="喂")

    assert result.available is False
    assert result.reason == "error"


@pytest.mark.network
async def test_the_live_endpoint_speaks_cantonese():
    """The whole path against the LIVE vendor: DNS, auth, the voice, real MP3
    frames back. Needs `MINIMAX_CN_API_KEY` in the environment; deselected by
    default, run deliberately with `uv run pytest -m network`."""
    import os

    key = os.environ.get("MINIMAX_CN_API_KEY", "")
    if not key:
        pytest.skip("MINIMAX_CN_API_KEY is not set")
    speaker = MiniMaxTtsSpeaker(api_key=key, voice="Cantonese_CuteGirl")

    result = await speaker.speak(text="喂，我係 Nora。")

    assert result.available is True, f"live synthesis failed: {result.reason}"
    assert result.audio
    assert result.media_type == "audio/mpeg"


# -- the speech formatter seam and the two request knobs. The formatter here is
#    the REAL `SpeechFormatter` over a scripted model, so what reaches the wire is
#    what production would send.

class _Said:
    def __init__(self, content: str) -> None:
        self.content, self.finish_reason = content, "stop"


class _ScriptedModel:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def complete(self, *, system: str, prompt: str) -> _Said:
        return _Said(self.answer)


class _Instruction:
    def get(self, *, applies_to: str = "speech_format") -> SimpleNamespace:
        return SimpleNamespace(doctrine_text="# What you are reading\nPace it.")


def _formatter(answer: str) -> SpeechFormatter:
    return SpeechFormatter(
        client=_ScriptedModel(answer), prompts=_Instruction(), metrics=Metrics(),
        timeout_seconds=1.0, min_chars=1, max_chars=200,
    )


async def test_with_no_formatter_the_reply_reaches_the_wire_exactly_as_written():
    """`mode = "off"`: emoji and all, byte-identical to before the formatter."""
    server = _Server()
    await _speaker(server).speak(text="好呀😂 **聽日**見")
    body = json.loads(server.requests[0].content)
    assert body["text"] == "好呀😂 **聽日**見"
    assert "pronunciation_dict" not in body
    assert "text_normalization" not in body["voice_setting"]


async def test_the_formatters_passed_text_is_what_is_synthesized():
    server = _Server()
    paced = "好呀(laughs) 聽日三點<#0.30#>喺旺角等"
    speaker = _speaker(server, formatter=_formatter(paced))
    await speaker.speak(text="好呀😂 聽日三點喺旺角等")
    assert json.loads(server.requests[0].content)["text"] == paced


async def test_a_formatter_that_rewrites_a_word_never_reaches_the_wire():
    server = _Server()
    speaker = _speaker(server, formatter=_formatter("好呀 聽日四點喺旺角等"))
    await speaker.speak(text="好呀😂 聽日三點喺旺角等")
    assert json.loads(server.requests[0].content)["text"] == "好呀 聽日三點喺旺角等"


async def test_a_formatter_that_raises_speaks_the_reply_as_written():
    class _Broken:
        async def format(self, *, text: str) -> str:
            raise RuntimeError("contract broken")

    server = _Server()
    result = await _speaker(server, formatter=_Broken()).speak(text="好呀")
    assert result.available is True
    assert json.loads(server.requests[0].content)["text"] == "好呀"


async def test_a_reply_of_only_emoji_is_empty_and_never_billed():
    server = _Server()
    result = await _speaker(server, formatter=_formatter("")).speak(text="😂👍")
    assert result == SpeakResult(available=False, reason="empty")
    assert server.requests == []


async def test_text_normalization_and_pronunciation_ride_the_wire_when_set():
    server = _Server()
    speaker = _speaker(
        server, text_normalization=True, pronunciation=("RV/(aa1)(wai1)",),
    )
    await speaker.speak(text="架 RV 好正")
    body = json.loads(server.requests[0].content)
    assert body["voice_setting"]["text_normalization"] is True
    assert body["pronunciation_dict"] == {"tone": ["RV/(aa1)(wai1)"]}
