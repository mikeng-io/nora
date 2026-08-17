"""Per-modality media describers (2026-08-17 spec).

The dispatch half: which modality a blob belongs to, whether that modality has a
describer, and what the tool says when it does not. Config-level behaviour lives
in `test_config.py`; the timeout invariant lives in `test_vision_client.py`.
"""

# ruff: noqa: F811 — importing a pytest fixture and then naming it as a test
# parameter is the standard idiom for sharing one; ruff reads the parameter as a
# redefinition of the import. The alternative is duplicating `wiring`'s whole
# Postgres/blob-store setup here, which would be a second thing to keep in step.
from tests.test_media_fetch import (  # noqa: F401 — fixtures are used by name
    PNG_SHA,
    _append_image,
    _call,
    wiring,
)


async def test_a_voice_note_is_unavailable_when_audio_is_switched_off(wiring):
    """R-27's whole point: a wiring gap is «I couldn't check», NEVER «there's
    nothing there». Audio ships inert, so a voice note reaches a describer that
    is off — and an off describer is `unavailable`, not `nothing_found`, and no
    longer `not_an_image` either (it IS a thing we could describe, once enabled).

    Reverting the dispatch turns this red: today every non-image collapses to
    `not_an_image` before any model call.
    """
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256="a" * 64, media_type="audio/aac")
    tool = wiring["build"](describe_models={"image": "m/image"})
    out = await _call(tool, code="a" * 6)
    assert out["status"] == "unavailable"
    assert wiring["vision"].calls == 0


async def test_a_voice_note_reaches_the_describer_when_audio_is_enabled(wiring):
    """The seam itself: naming an audio model is all it takes to light the path
    up, with no code change. This is the promise the whole split exists for."""
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="audio/aac")
    tool = wiring["build"](describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert wiring["vision"].calls == 1


async def test_a_file_is_still_turned_away_before_any_model_call(wiring):
    """A modality nothing describes stays `not_an_image` — unchanged behaviour,
    and still decided before the bytes reach any model."""
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256="c" * 64, media_type="application/pdf")
    tool = wiring["build"](describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code="c" * 6)
    assert out["status"] == "not_an_image"
    assert wiring["vision"].calls == 0


class _RecordingVision:
    """Records what the tool asked for, so «routed to the audio model» is
    asserted against the CALL and not against configuration."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def describe(self, *, data, media_type, prompt="", model=""):
        from nora.infra.vision import VisionResult
        self.calls.append({"media_type": media_type, "prompt": prompt, "model": model})
        return VisionResult(available=True, description="something")


async def test_each_modality_is_described_by_its_own_model_and_prompt(wiring):
    """Both halves of the seam in one assertion, because either alone is a
    silent no-op: the AUDIO model must be the one asked for, and the prompt must
    not still be «Describe this image» — asking a transcriber to describe an
    image is how a configured modality returns nonsense instead of failing.
    """
    recorder = _RecordingVision()
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="audio/aac")
    tool = wiring["build"](vision_source=recorder,
                           describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert recorder.calls[-1]["model"] == "m/audio"
    assert "image" not in recorder.calls[-1]["prompt"].lower()
