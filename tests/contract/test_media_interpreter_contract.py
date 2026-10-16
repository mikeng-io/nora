"""`MediaInterpreter` has two implementations, so it gets one suite both pass —
CLAUDE.md's rule, and the reason `FakeMediaInterpreter` cannot quietly drift
into answering differently from `MediaComprehender`.

Why it exists: production refuses an unreadable `media_type` before any model
call goes out, so the fake must gate the same way. The pipeline tests above this
port are written against the fake — without this suite, a pipeline test that
never actually checks the format could still pass (the fake masks the
omission), or a correct assertion could fail against the fake while passing
against production. Each test below is written against the PORT, parametrized
over both concrete classes, so that divergence cannot happen silently.

**The port returns a comprehension** — title, description, content — for any
medium, from ONE model pass, and has a second verb (`summarize`) for the text
lane a voice note takes. The properties pinned here are the ones every
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

#: The two model names the fixtures configure, asserted by NAME rather than by
#: inequality: `!=` is satisfied by any wrong value, and `describe_model` is a
#: provenance column where a wrong value is worse than an empty one.
_IMAGE_MODEL = "mimo"
_TEXT_MODEL = "mimo-text"


class _RecordingStubVisionClient:
    """The stub above plus the PROMPT it was handed — `label` is the one new
    argument the widened port gained, and a suite that never exercised it would
    leave the whole point of the widening unpinned."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = "",
        provider: str = "omniroute", max_bytes: int | None = None,
        windows: int | None = None,
    ) -> VisionResult:
        self.prompts.append(prompt)
        return VisionResult(available=True, description=_JSON % "a cartoon cat")

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        return _JSON % "a cartoon cat"


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
        windows: int | None = None,
    ) -> VisionResult:
        # Every keyword the real `VisionClient.describe` accepts. A double in
        # tests/contract/ that does not accept what the real thing accepts is
        # exactly the "fake that answers every request the same way" this
        # directory exists to prevent.
        return self.result

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        if not self.result.available:
            raise RuntimeError(self.result.reason or "vision")
        # NOT `self.result.description` — that is already the JSON the vision
        # half hands back, and wrapping it again produced a nested object the
        # parser then read as prose. The text lane answers its own shape.
        return _JSON % "a cartoon cat"


def _make_fake(*, available: bool, text: str, reason: str) -> MediaInterpreter:
    fake = FakeMediaInterpreter(text, available=available, reason=reason)
    # The SAME two names the comprehender fixture configures, so an assertion
    # on the configured value is written once and holds over both
    # implementations. `describe_model` is a provenance column; «they differ»
    # is satisfied by any wrong answer, and the point is that each names what
    # it actually used.
    fake.model = _IMAGE_MODEL
    fake.text_model = _TEXT_MODEL
    return fake


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
    # DISTINCT model names on purpose: the text pass and the describer must be
    # tellable apart in `describe_model`, and a fixture that named both "mimo"
    # would make that assertion vacuous against production.
    return MediaComprehender(
        client=client,
        describe_models={
            "image": _IMAGE_MODEL, "video": "k3", "audio": _IMAGE_MODEL},
        text_model=_TEXT_MODEL,
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
    """One model pass, three fields. An implementation that
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


async def test_the_label_is_accepted_and_changes_nothing_it_should_not(
    make_interpreter,
):
    """`label` is a port argument, and both implementations must ACCEPT it — a
    fake that took only `media_type` would make every caller passing a label
    untestable against production, which is the exact drift this directory
    exists to prevent.

    What it may not do is change the RESULT SHAPE: the same bytes with a label
    and without one comprehend the same way. What it selects — which authored
    question is asked — is a property of the concrete adapter and is pinned in
    `tests/test_media_fetch.py::test_a_label_picks_the_authored_question`,
    because the fake has no prompt table to select from and asserting on one
    here would be asserting on the fake.
    """
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    labelled = await interpreter.describe(
        data=b"\x89PNG...", media_type="image/png", label="sticker")
    bare = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert labelled.available is bare.available is True
    assert labelled.text == bare.text


async def test_a_label_reaches_the_model_as_a_different_authored_question():
    """The production half of the test above, and the reason `label` exists at
    all: it SELECTS the authored prompt. A sticker is asked what it depicts and
    explicitly not what its sender meant by it (DEC-108); a photo is asked what
    it shows.

    Not parametrized over the fake: the fake has no prompt table, and a test
    that asserted on one would be asserting on the double rather than on the
    port's contract with the model."""
    client = _RecordingStubVisionClient()
    interpreter = MediaComprehender(
        client=client, describe_models={"image": _IMAGE_MODEL},
        text_model=_TEXT_MODEL)

    await interpreter.describe(
        data=b"\x89PNG...", media_type="image/png", label="sticker")
    await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    sticker, photo = client.prompts
    assert sticker != photo
    assert "chat sticker" in sticker
    assert "do not interpret the sender's mood or intent" in sticker.lower()
    assert "chat sticker" not in photo


async def test_the_model_that_wrote_the_text_comes_back_with_it(make_interpreter):
    """`MediaDescription.model` is what `media_objects.describe_model` records —
    the one column an operator reads to find out which describer produced what.

    Pinned on the PORT, over both implementations: pinned only against the fake,
    mutating `MediaComprehender`'s returns to a wrong model would leave the
    entire media suite green."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got.model == _IMAGE_MODEL, (
        "an available comprehension names the model that actually wrote it"
    )


async def test_the_text_lane_names_the_text_model_not_the_describer(
    make_interpreter,
):
    """The case the field exists for. A voice note's title and description are
    written entirely by the text model; deriving the column from the media type
    would record the AUDIO describer — `""` on the shipped config — for the one
    modality whose text has a single named author.

    So the two verbs must not report the same model, and that is checkable on
    the port rather than on one adapter."""
    interpreter = make_interpreter(available=True, text="a cartoon cat", reason="")

    described = await interpreter.describe(
        data=b"\x89PNG...", media_type="image/png")
    summarized = await interpreter.summarize(text="喂你今日得唔得閒啊")

    assert summarized.model != described.model, (
        "the text pass and the describer are different models, and the column "
        "that records which one wrote the text has to be able to tell them "
        "apart"
    )
    # THE CONFIGURED VALUE, not merely a different string. «They differ» is
    # satisfied by any wrong answer — a mutation hardcoding `"WRONG-MODEL"` in
    # the real adapter passed that assertion, which is the whole failure this
    # test was written against one layer down.
    assert summarized.model == _TEXT_MODEL
    assert described.model == _IMAGE_MODEL


async def test_an_unavailable_result_names_no_model(make_interpreter):
    """Nothing wrote the text, because there is no text. A model name beside an
    unavailable result would put a describer's name on a read that never
    happened."""
    interpreter = make_interpreter(available=False, text="", reason="timeout")

    got = await interpreter.describe(data=b"\x89PNG...", media_type="image/png")

    assert got.model == ""
