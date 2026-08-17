"""`MediaInterpreter` has two implementations, so it gets one suite both pass —
CLAUDE.md's rule, and the reason `FakeMediaInterpreter` cannot quietly drift
into answering differently from `StickerInterpreter`.

Fix round 1 on Task 11: review found `FakeMediaInterpreter` had no
`media_type` gate at all, while `StickerInterpreter` refuses a non-image type
(`unsupported_format`) before any vision call goes out. Tasks 13/14 build the
sticker pipeline against the fake — without this suite, a pipeline test that
never actually checks the format could still pass (the fake masks the
omission), or a correct assertion could fail against the fake while passing
against production. Each test below is written against the PORT, parametrized
over both concrete classes, so that divergence can no longer happen silently.
"""

from __future__ import annotations

import pytest

from nora.domain.ports import MediaDescription, MediaInterpreter
from nora.infra.vision import StickerInterpreter, VisionResult
from nora.testing import FakeMediaInterpreter


class _StubVisionClient:
    """A minimal `VisionClient` stand-in, local to this file on purpose: a
    shared double living in `tests/test_media_fetch.py`
    (`_RecordingVisionClient`) would make this contract suite depend on
    another test module's internals instead of standing on its own. All it
    needs to do is hand back a configured `VisionResult` — it is not used to
    assert on calls here, only to drive `StickerInterpreter`'s branches.
    """

    def __init__(self, *, result: VisionResult) -> None:
        self.result = result

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = ""
    ) -> VisionResult:
        # `model` is part of the real `VisionClient.describe` since DEC-114.
        # A double in tests/contract/ that does not accept what the real thing
        # accepts is exactly the "fake that answers every request the same way"
        # this directory exists to prevent — it passed only because
        # `StickerInterpreter` happens never to pass a model.
        return self.result


def _make_fake(*, available: bool, text: str, reason: str) -> MediaInterpreter:
    return FakeMediaInterpreter(text, available=available, reason=reason)


def _make_sticker(*, available: bool, text: str, reason: str) -> MediaInterpreter:
    client = _StubVisionClient(
        result=VisionResult(available=available, description=text, reason=reason)
    )
    return StickerInterpreter(client, model="mimo")


@pytest.fixture(params=["fake", "sticker"])
def make_interpreter(request):
    """A factory, not a bare instance: each test needs a DIFFERENT underlying
    result (available/unavailable/refused), and the two implementations reach
    that result through different constructors — `FakeMediaInterpreter` takes
    it directly, `StickerInterpreter` takes it via the stub client it wraps."""
    return {"fake": _make_fake, "sticker": _make_sticker}[request.param]


async def test_a_non_image_media_type_is_refused_before_any_description(make_interpreter):
    """The lottie case (sticker spec, Discord JSON stickers): refused BEFORE
    any underlying call could matter, regardless of what the underlying
    result would have been had it been asked."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    got = await interpreter.describe(data=b'{"v":"5.5"}', media_type="application/json")

    assert got.available is False
    assert got.reason == "unsupported_format"
    assert got.text == ""


async def test_an_available_image_result_carries_its_text(make_interpreter):
    interpreter = make_interpreter(
        available=True, text="a cartoon cat collapsing", reason=""
    )

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got == MediaDescription(available=True, text="a cartoon cat collapsing")


async def test_an_unavailable_result_never_becomes_a_blank_description(make_interpreter):
    """R-51. A timeout must not turn into `[sticker a3f2c1: ]` — `text` stays
    empty and `reason` carries why, for both implementations alike."""
    interpreter = make_interpreter(available=False, text="", reason="timeout")

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got.available is False
    assert got.reason == "timeout"
    assert got.text == ""


async def test_describe_never_raises(make_interpreter):
    """Ports never raise (module docstring, `nora/domain/ports.py`). Neither
    empty bytes nor an empty/malformed `media_type` may propagate an
    exception — both must resolve to a typed, unavailable result instead."""
    interpreter = make_interpreter(available=True, text="fine", reason="")

    got = await interpreter.describe(data=b"", media_type="")

    assert got.available is False
    assert got.reason == "unsupported_format"
