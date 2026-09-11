"""What the model call puts on the span it runs under.

The gate is a GENERATION in Langfuse, and a generation with no token counts
reports as costing nothing. Read back live on 2026-09-08, the two real
verdicts in the deployment showed `usage: {input: 0, output: 0, total: 0}` —
and the debrief and the standing fold had done the same since they shipped.
That is the number the cost of running this path has to be judged against.

**Two seams, because there are two transports again.** The debrief and the
standing fold are on the Agent SDK, where the counts arrive as an accumulated
triple (`_annotate_usage_triple`); the gate came back off it on 2026-09-11
(#186) and reads them off a response object (`_annotate_usage`). Both are
covered here, and both must record NOTHING rather than zero when the provider
reported nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nora.app.composition import _annotate_usage, _annotate_usage_triple
from nora.obs.tracing import span

pytestmark = pytest.mark.anyio


def _attrs(exported, name: str) -> dict:
    (recorded,) = [s for s in exported.get_finished_spans() if s.name == name]
    return dict(recorded.attributes or {})


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _Response:
    def __init__(self, usage) -> None:
        self.usage = usage


async def test_the_token_usage_reaches_the_span_it_runs_under(exported_spans):
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        _annotate_usage_triple((1830, 12, 1842))

    attrs = _attrs(exported_spans, "relevance_gate")
    assert attrs["gen_ai.usage.input_tokens"] == 1830
    assert attrs["gen_ai.usage.output_tokens"] == 12
    assert attrs["gen_ai.usage.total_tokens"] == 1842


async def test_a_raw_responses_usage_reaches_the_span_it_runs_under(exported_spans):
    """The gate's own seam since it came back off the SDK. Same three numbers,
    read off the provider's response rather than out of a triple."""
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        _annotate_usage(_Response(_Usage(2055, 20, 2075)))

    attrs = _attrs(exported_spans, "relevance_gate")
    assert attrs["gen_ai.usage.input_tokens"] == 2055
    assert attrs["gen_ai.usage.output_tokens"] == 20
    assert attrs["gen_ai.usage.total_tokens"] == 2075


async def test_a_provider_that_reports_no_usage_records_no_usage(exported_spans):
    """Not zero, on EITHER seam. A provider that omitted the field has told us
    nothing, and writing 0 would assert the call was free — the same argument
    `finish_reason=... or ""` makes one field over."""
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        _annotate_usage_triple(None)
        _annotate_usage(_Response(None))

    assert "gen_ai.usage.total_tokens" not in _attrs(exported_spans, "relevance_gate")


class _FakeCompletions:
    """A stand-in for `client.chat.completions`, shaped like the real one —
    including the `usage` object, because that is the field under test.

    It RECORDS the request rather than only answering it: the gate came off
    the Agent SDK on 2026-09-11 (#186) precisely because the request shape is
    the thing that decides the verdict, so a fake that answered every call the
    same way and kept nothing would have made this test decorative.
    """

    def __init__(self) -> None:
        self.seen: dict = {}
        #: Settable, so a test can make the provider report a CUT-OFF. A fake
        #: that answers `"stop"` to every call cannot tell a client that reads
        #: the field from one that hardcodes the word.
        self.finish_reason: str | None = "stop"

    async def create(self, **kwargs):
        self.seen = kwargs
        return SimpleNamespace(
            # PADDED on purpose: the client strips, and a fixture that arrived
            # already-clean would let the strip be deleted silently.
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content='  {"verdict": "no_go", "relevance_score": 0.0}  ',
                ),
                finish_reason=self.finish_reason,
            )],
            usage=_Usage(999, 7, 1006),
        )


class _FakeClient:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()

    @property
    def chat(self):
        return self


async def test_the_gate_client_annotates_what_the_provider_returned(exported_spans):
    """The wiring, not just the helper: a client that computed the counts and
    never annotated them would pass both tests above."""
    from nora.app import composition

    client = _FakeClient()
    gate_client = composition._RelevanceGateModelClient(
        client=client, model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        got = await gate_client.complete(system="rules", prompt="room")

    assert got.content == '{"verdict": "no_go", "relevance_score": 0.0}'
    assert got.finish_reason == "stop"
    assert _attrs(exported_spans, "relevance_gate")["gen_ai.usage.total_tokens"] == 1006


async def test_the_gate_asks_for_json_with_the_instruction_in_the_system_role():
    """The request shape, which is what #186 was actually about.

    The ROLE SPLIT predates both transports on this client and survives both:
    with the instruction and the untrusted transcript concatenated into one
    user string, a member typing `[assistant]:` would sit in exactly the role
    the operator's rules occupy. (`4448c737` introduced that split for the
    DEBRIEF, which did concatenate; the gate already had it.) The
    `json_object` RESPONSE FORMAT and the absence of any tool machinery are
    what came back on 2026-09-11: measured over 40 labelled cases with one
    instruction on both arms, the SDK's structured output — which converts the
    schema into a tool specification — held 1/6 safety cases against this
    shape's 6/6, at 2.4x the prompt tokens.

    `model`, `max_tokens` and `temperature` are asserted here too. The gate's
    ceiling is small on purpose — a gate with room to write paragraphs has room
    to start drafting the reply it must never draft — and its model is its OWN
    combo, never the tag lane's; both are arguments in the client's docstring
    that nothing was checking.

    `tool_choice` must NOT appear. A raw completion sends no tools, so there
    is nothing for one to select; the SDK path declared it only because
    Strands sends `tool_choice: None` when nothing sets it and a thinking-mode
    provider then refuses the call outright.
    """
    from nora.app import composition

    client = _FakeClient()
    gate_client = composition._RelevanceGateModelClient(
        client=client, model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )
    await gate_client.complete(system="the rules", prompt="the room")

    sent = client.completions.seen
    assert sent["messages"] == [
        {"role": "system", "content": "the rules"},
        {"role": "user", "content": "the room"},
    ]
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["reasoning_effort"] == "none"
    assert sent["model"] == "nora-gate"
    assert sent["max_tokens"] == 512
    assert sent["temperature"] == 0.0
    assert "tool_choice" not in sent and "tools" not in sent


async def test_the_gate_reports_the_providers_own_finish_reason():
    """The VALUE, not the field's presence.

    `finish_reason` is the only thing standing between a truncated verdict and
    a parsed one — `RelevanceGate.decide` refuses to parse on `"length"` and
    fails closed as `truncated`. A client that hardcoded `"stop"` would satisfy
    the architecture test that requires the token to appear on the call path
    (it matches the identifier, not what flows into it) and would report every
    cut-off body as complete.
    """
    from nora.app import composition

    client = _FakeClient()
    client.completions.finish_reason = "length"
    gate_client = composition._RelevanceGateModelClient(
        client=client, model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )
    got = await gate_client.complete(system="the rules", prompt="the room")
    assert got.finish_reason == "length"

    # And a provider that omits it says NOTHING, rather than "stop".
    client.completions.finish_reason = None
    got = await gate_client.complete(system="the rules", prompt="the room")
    assert got.finish_reason == ""
