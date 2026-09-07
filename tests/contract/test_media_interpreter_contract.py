"""`MediaInterpreter` has two implementations, so it gets one suite both pass —
CLAUDE.md's rule, and the reason `FakeMediaInterpreter` cannot quietly drift
into answering differently from `MediaComprehender`.

Why it exists (fix round 1 on the sticker work, and it still holds): review
found `FakeMediaInterpreter` had no `media_type` gate at all, while production
refuses an unreadable type before any model call goes out. The pipeline tests
above this port are written against the fake — so without this suite, a
pipeline test that never actually checks the format could still pass (the fake
masks the omission), or a correct assertion could fail against the fake while
passing against production. Each test below is written against the PORT,
parametrized over both concrete classes, so that divergence can no longer
happen silently.

**The port widened with issue #116.** It used to return one sentence for a
sticker; it returns a comprehension — title, description, content — for any
medium, from ONE model pass, and it has a second verb (`summarize`) for the
text lane a voice note takes. The properties pinned here are the ones every
caller relies on: an unreadable type never reaches a model, an unavailable
result never carries a partial triple, and neither verb ever raises.
"""

from __future__ import annotations

import pytest

from nora.domain.ports import MediaDescription, MediaInterpreter
from nora.infra.vision import MediaComprehender, VisionResult
from nora.testing import FakeMediaInterpreter

# What the comprehension prompt asks a model to answer with. The stub hands
# this back so `MediaComprehender` exercises its real parse rather than the
# prose fallback — the fake's own answer is structurally equivalent.
_JSON = '{"title": "a cartoon cat", "description": "%s", "content": ""}'


class _StubVisionClient:
    """A minimal `VisionClient` stand-in, local to this file on purpose: a
    shared double living in `tests/test_media_fetch.py`
    (`_RecordingVisionClient`) would make this contract suite depend on
    another test module's internals instead of standing on its own. All it
    needs to do is hand back a configured `VisionResult` — it is not used to
    assert on calls here, only to drive `MediaComprehender`'s branches.
    """

    def __init__(self, *, result: VisionResult) -> None:
        self.result = result

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = "",
        provider: str = "omniroute", max_bytes: int | None = None,
    ) -> VisionResult:
        # Every keyword the real `VisionClient.describe` accepts. A double in
        # tests/contract/ that does not accept what the real thing accepts is
        # exactly the "fake that answers every request the same way" this
        # directory exists to prevent.
        return self.result

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        if not self.result.available:
            raise RuntimeError(self.result.reason or "vision")
        return _JSON % self.result.description


def _make_fake(*, available: bool, text: str, reason: str) -> MediaInterpreter:
    return FakeMediaInterpreter(text, available=available, reason=reason)


def _make_comprehender(
    *, available: bool, text: str, reason: str
) -> MediaInterpreter:
    client = _StubVisionClient(
        result=VisionResult(
            available=available,
            description=_JSON % text if available else "",
            reason=reason,
        )
    )
    return MediaComprehender(
        client=client,
        describe_models={"image": "mimo", "video": "k3", "audio": "mimo"},
        text_model="mimo",
    )


@pytest.fixture(params=["fake", "comprehender"])
def make_interpreter(request):
    """A factory, not a bare instance: each test needs a DIFFERENT underlying
    result (available/unavailable/refused), and the two implementations reach
    that result through different constructors — `FakeMediaInterpreter` takes
    it directly, `MediaComprehender` takes it via the stub client it wraps."""
    return {"fake": _make_fake, "comprehender": _make_comprehender}[request.param]


async def test_an_unreadable_media_type_is_refused_before_any_description(
    make_interpreter,
):
    """The lottie case (Discord JSON stickers): refused BEFORE any underlying
    call could matter, regardless of what the underlying result would have been
    had it been asked. Handing JSON to a vision model is not a degraded read,
    it is a wrong one — and the model would answer something anyway."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    got = await interpreter.describe(data=b'{"v":"5.5"}', media_type="application/json")

    assert got.available is False
    assert got.reason == "unsupported_format"
    assert got.text == ""


async def test_an_available_result_carries_all_three_fields(make_interpreter):
    """One model pass, three fields (issue #116). An implementation that
    returned only a description would let every caller above it forget to store
    the other two."""
    interpreter = make_interpreter(
        available=True, text="a cartoon cat collapsing", reason=""
    )

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got.available is True
    assert got.text == "a cartoon cat collapsing"
    assert got.title, "the same pass produced a title too"


async def test_an_unavailable_result_never_becomes_a_blank_comprehension(
    make_interpreter,
):
    """R-51. A timeout must not turn into `[image a3f2c1: ]` — every field
    stays empty and `reason` carries why, for both implementations alike. An
    unavailable result never carries a PARTIAL triple, so a caller cannot
    half-store a comprehension it could not make."""
    interpreter = make_interpreter(available=False, text="", reason="timeout")

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got.available is False
    assert got.reason == "timeout"
    assert (got.title, got.text, got.content) == ("", "", "")


async def test_describe_never_raises(make_interpreter):
    """Ports never raise (module docstring, `nora/domain/ports.py`). Neither
    empty bytes nor an empty/malformed `media_type` may propagate an
    exception — both must resolve to a typed, unavailable result instead."""
    interpreter = make_interpreter(available=True, text="fine", reason="")

    got = await interpreter.describe(data=b"", media_type="")

    assert got.available is False
    assert got.reason == "unsupported_format"


async def test_summarize_refuses_empty_text_rather_than_inventing_a_title(
    make_interpreter,
):
    """The voice-note lane's «no transcript, no claim» branch. An STT failure
    leaves nothing to summarize, and a title written over nothing would be a
    guess about what somebody said."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    got = await interpreter.summarize(text="   ")

    assert got.available is False
    assert got.reason == "empty"


async def test_summarize_never_returns_content(make_interpreter):
    """The caller already holds the words. Echoing them back through a second
    model would be a chance for it to paraphrase a member's own speech into the
    durable record."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    got = await interpreter.summarize(text="喂你今日得唔得閒啊")

    assert got.available is True
    assert got.content == ""


async def test_summarize_never_raises(make_interpreter):
    """Same promise as `describe`, on the verb that reaches a different client
    method. A transport failure is a typed unavailable result, never an
    exception into a spawned job where it would surface only as a
    done-callback log line."""
    interpreter = make_interpreter(available=False, text="", reason="timeout")

    got = await interpreter.summarize(text="喂你今日得唔得閒啊")

    assert got.available is False
    assert got.reason in ("timeout", "error", "vision")
    assert got == MediaDescription(available=False, reason=got.reason)
