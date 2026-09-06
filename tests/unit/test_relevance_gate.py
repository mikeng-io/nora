"""The Relevance Gate — one cheap call, transcript + standing only,
fail-closed (design/dreamer-agent-loop/36).

Every test here drives the real `RelevanceGate` against a scripted model
client; nothing sleeps and nothing reaches a provider. The fake answers
DIFFERENTLY per scenario rather than one canned reply — a fake that says the
same thing to every prompt cannot fail the tests that matter here.
"""

from __future__ import annotations

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing
from nora.ingest.relevance_gate import (
    GATE_ERROR_KINDS,
    Completion,
    RelevanceGate,
    parse_verdict,
    render_context,
)
from nora.obs.metrics import Metrics


class _Client:
    """Scripted model. `answer` may be a string (the body) or an exception."""

    def __init__(self, answer: object, finish_reason: str = "stop") -> None:
        self.answer = answer
        self.finish_reason = finish_reason
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return "nora-ambient-gate"

    async def complete(self, *, prompt: str) -> Completion:
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return Completion(content=str(self.answer), finish_reason=self.finish_reason)


class _Records:
    def __init__(self, rows: list[TranscriptRow] | None = None, fail: bool = False):
        self.rows = rows or []
        self.fail = fail

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        if self.fail:
            raise RuntimeError("audit down")
        return self.rows[:limit]


class _StandingStore:
    def __init__(self, body: str | None = None, fail: bool = False) -> None:
        self.body = body
        self.fail = fail

    async def read(self, *, platform: str, conversation_id: str) -> Standing | None:
        if self.fail:
            raise RuntimeError("standing down")
        if self.body is None:
            return None
        return Standing(
            platform=platform, conversation_id=conversation_id,
            body=self.body, fold_count=1, updated_at=1,
        )

    async def write(self, *, standing: Standing) -> None:  # pragma: no cover
        raise AssertionError("the gate never writes")


def _row(body: str, *, who: str = "Miles", is_nora: bool = False, rid: int = 1):
    return TranscriptRow(
        id=rid, sender_id="+85211", sender_name=who, ts=rid, body=body,
        is_nora=is_nora, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


def _gate(client: _Client, **kw) -> RelevanceGate:
    return RelevanceGate(
        client=client,
        records=kw.pop("records", _Records([_row("anyone know the AWS date?")])),
        metrics=kw.pop("metrics", Metrics()),
        **kw,
    )


# --- the prompt is a CLOSED set -------------------------------------------


async def test_the_prompt_carries_the_transcript_and_standing_and_nothing_else():
    """Design 36 §2: transcript + standing are the ONLY two inputs.

    Asserted as an ALLOW-list over the rendered prompt rather than "no
    doctrine appears", because a deny-list only catches the blocks somebody
    remembered to name — the failure this guards is a future block being
    added, which by definition nobody named here.
    """
    client = _Client('{"verdict": "go", "relevance_score": 0.7}')
    gate = _gate(
        client,
        records=_Records([_row("anyone know when re:Invent is?")]),
        standing=_StandingStore("They are planning a trip."),
    )
    await gate.decide(platform="discord", conversation_id="g/1")

    prompt = client.prompts[0]
    assert "anyone know when re:Invent is?" in prompt
    assert "They are planning a trip." in prompt
    # The instrument's own instruction, plus the two blocks. Any THIRD source
    # of room content would have to appear here to matter, and does not.
    body = prompt.split("Context (nothing else is available to you):", 1)[1]
    body = body.split("Rules:", 1)[0]
    assert "- transcript" in body and "- standing" in body
    assert body.count("- ") == 2, f"a third injected block appeared: {body!r}"


async def test_the_prompt_never_carries_doctrine_or_tools():
    """The negative half, stated separately because it is the specific
    failure design 36 exists to prevent: the superseded design injected the
    whole doctrine and every tool onto the determination path, and the trace
    that killed it showed a weather tool call."""
    client = _Client('{"verdict": "no_go", "relevance_score": 0.1}')
    await _gate(client).decide(platform="discord", conversation_id="g/1")
    prompt = client.prompts[0].lower()
    for forbidden in ("doctrine", "send_reply", "tool", "you are nora"):
        assert forbidden not in prompt, f"{forbidden!r} reached the gate prompt"


def test_the_transcript_renders_oldest_first():
    """`RecordStore.recent` returns newest-first; a transcript read backwards
    is a different conversation, and the gate's whole job is reading one."""
    rendered = render_context(
        (_row("and the second thing", rid=2), _row("first thing", rid=1)), None,
    )
    assert rendered.index("first thing") < rendered.index("and the second thing")


def test_a_room_with_no_standing_renders_none_not_an_empty_block():
    rendered = render_context((_row("hi"),), None)
    assert "(none)" in rendered


# --- parsing, fail-closed --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "verdict", "source"),
    [
        ('{"verdict": "go", "relevance_score": 0.9}', "go", "model"),
        ('{"verdict": "no_go", "relevance_score": 0.0}', "no_go", "model"),
        # A GO with no score is still a GO: the score is reference only, so
        # rejecting the verdict over it would let a reference field enforce.
        ('{"verdict": "go"}', "go", "model"),
    ],
)
def test_a_well_formed_body_parses(raw, verdict, source):
    got = parse_verdict(raw)
    assert (got.verdict, got.source) == (verdict, source)


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("not json at all", "unparseable"),
        # Valid JSON that is not an object. `json.loads` succeeds on every one
        # of these, so parsing alone is not evidence of an answer.
        ("null", "unparseable"),
        ("[]", "unparseable"),
        ("3", "unparseable"),
        ('{"relevance_score": 0.9}', "bad_verdict"),
        ('{"verdict": "maybe"}', "bad_verdict"),
        ('{"verdict": true}', "bad_verdict"),
    ],
)
def test_every_malformed_body_fails_closed(raw, kind):
    got = parse_verdict(raw)
    assert (got.verdict, got.source, got.error_kind) == ("no_go", "fail_closed", kind)
    assert got.admits is False


def test_a_score_outside_the_range_is_clamped_not_rejected():
    assert parse_verdict('{"verdict": "go", "relevance_score": 9}').score == 1.0
    assert parse_verdict('{"verdict": "go", "relevance_score": -2}').score == 0.0
    assert parse_verdict('{"verdict": "go", "relevance_score": "hi"}').score == 0.0


def test_every_declared_error_kind_is_reachable():
    """A vocabulary with an unreachable member is a label that renders as a
    series which is always zero — the `nora_relevance_gate_errors_total`
    version of "a declared metric with no call site is a lie"."""
    reached = {
        parse_verdict("not json").error_kind,
        parse_verdict('{"verdict": "maybe"}').error_kind,
    }
    # `call_failed` and `truncated` are `_ask`'s, covered by their own tests
    # below; asserted together here so the constant cannot grow a member that
    # nothing produces.
    assert reached | {"call_failed", "truncated"} == set(GATE_ERROR_KINDS)


# --- the call itself -------------------------------------------------------


async def test_a_failed_call_is_a_counted_no_go_never_an_exception():
    metrics = Metrics()
    gate = _gate(_Client(RuntimeError("provider down")), metrics=metrics)
    got = await gate.decide(platform="discord", conversation_id="g/1")
    assert got.admits is False and got.error_kind == "call_failed"
    assert metrics.get(
        "nora_relevance_gate_errors_total", kind="call_failed", platform="discord",
    ) == 1
    # NOT on the verdict series: "the room was quiet" and "the gate is down"
    # must never be the same reading.
    assert metrics.get(
        "nora_relevance_gate_total", verdict="no_go", platform="discord",
    ) == 0


async def test_a_truncated_answer_is_not_parsed_even_when_it_would_parse():
    """`finish_reason="length"` with a body that happens to be valid JSON —
    the R-27 substitution debrief already paid for: a cut-off document can
    parse into something shorter and wrong."""
    metrics = Metrics()
    client = _Client('{"verdict": "go", "relevance_score": 0.9}', finish_reason="length")
    got = await _gate(client, metrics=metrics).decide(
        platform="discord", conversation_id="g/1",
    )
    assert got.admits is False and got.error_kind == "truncated"
    assert metrics.get(
        "nora_relevance_gate_errors_total", kind="truncated", platform="discord",
    ) == 1


async def test_a_real_verdict_moves_the_verdict_series_and_not_the_error_one():
    metrics = Metrics()
    client = _Client('{"verdict": "go", "relevance_score": 0.8}')
    got = await _gate(client, metrics=metrics).decide(
        platform="discord", conversation_id="g/1",
    )
    assert (got.admits, got.score, got.source) == (True, 0.8, "model")
    assert metrics.get(
        "nora_relevance_gate_total", verdict="go", platform="discord",
    ) == 1
    for kind in GATE_ERROR_KINDS:
        assert metrics.get(
            "nora_relevance_gate_errors_total", kind=kind, platform="discord",
        ) == 0


async def test_a_transcript_read_failure_is_counted_and_still_asks():
    """Fail-soft on the READ, fail-closed on the VERDICT. The gate still runs
    — the model is asked about an empty room and will say no — and the
    counter is what says the read failed rather than the room being quiet."""
    metrics = Metrics()
    client = _Client('{"verdict": "no_go", "relevance_score": 0.0}')
    gate = _gate(client, records=_Records(fail=True), metrics=metrics)
    got = await gate.decide(platform="discord", conversation_id="g/1")
    assert got.admits is False and got.source == "model"
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_context", platform="discord",
    ) == 1
    assert "(no messages)" in client.prompts[0]


async def test_a_standing_read_failure_is_counted_and_never_reads_as_no_standing():
    """R-27 at the tier below: an unreadable position reported as «there is
    none» is `unavailable` wearing `nothing_found`'s face."""
    metrics = Metrics()
    client = _Client('{"verdict": "go", "relevance_score": 0.5}')
    gate = _gate(client, standing=_StandingStore(fail=True), metrics=metrics)
    await gate.decide(platform="discord", conversation_id="g/1")
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_standing", platform="discord",
    ) == 1


async def test_no_standing_store_is_the_dormant_default_and_not_an_error():
    metrics = Metrics()
    client = _Client('{"verdict": "go", "relevance_score": 0.5}')
    await _gate(client, metrics=metrics).decide(
        platform="discord", conversation_id="g/1",
    )
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_standing", platform="discord",
    ) == 0
