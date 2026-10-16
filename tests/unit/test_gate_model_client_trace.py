"""What the model call puts on the span it runs under.

The gate is a GENERATION in Langfuse, and a generation with no token counts
reports as costing nothing, which is the number the cost of running this path
would be judged against.

The debrief/standing-fold path runs through a real Strands `Agent`
(`nora.agent.structured.ask_text`), which annotates its own usage
automatically; the gate's raw call annotates through
`nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`, thin
wrappers around Strands' own `Tracer`. This file tests the GATE's wiring into
that pair (`tests/unit/test_debrief.py`'s trace-attribute tests cover the
debrief side of the delegation).

**One disclosed behaviour**, not fixed here: `Tracer.end_model_invoke_span`
writes `gen_ai.usage.*` unconditionally from the `Usage` dict it is given —
there is no "provider reported nothing, so record nothing" branch.
`nora.agent.telemetry.end_raw_model_span` writes zero in that case, which
asserts something weaker than "the provider said nothing" — see
`test_a_provider_that_reports_no_usage_now_records_zero` below, which asserts
that weaker guarantee.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.anyio


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


_DEFAULT_USAGE = _Usage(999, 7, 1006)


class _FakeCompletions:
    """A stand-in for `client.chat.completions`, shaped like the real one —
    including the `usage` object, because that is the field under test.

    It RECORDS the request rather than only answering it: the request shape is
    the thing that decides the verdict, so a fake that answered every call the
    same way and kept nothing would make this test decorative.
    """

    def __init__(self, *, usage: _Usage | None = _DEFAULT_USAGE) -> None:
        self.seen: dict = {}
        #: Settable, so a test can make the provider report a CUT-OFF. A fake
        #: that answers `"stop"` to every call cannot tell a client that reads
        #: the field from one that hardcodes the word.
        self.finish_reason: str | None = "stop"
        self._usage = usage

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
            usage=self._usage,
        )


class _FakeClient:
    def __init__(self, *, usage: _Usage | None = _DEFAULT_USAGE) -> None:
        self.completions = _FakeCompletions(usage=usage)

    @property
    def chat(self):
        return self


def _gate_client(client):
    from nora.app import composition

    return composition._RelevanceGateModelClient(
        client=client, model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )


async def test_the_gate_client_annotates_what_the_provider_returned(monkeypatch):
    """The wiring, not just the wrapper: a client that computed the counts
    and never handed them to `end_raw_model_span` would pass no test at all
    if this one did not exist.

    Patches `nora.app.composition`'s own imported names — the same way
    `tests/unit/test_debrief.py`'s `_Client.trace_attributes` verifies
    `_complete`'s delegation without needing a real Strands span — rather
    than reading back a real Strands-produced span: `tests/conftest.py`'s
    `exported_spans` fixture patches `nora.obs.tracing.trace.get_tracer`,
    which Strands' own `Tracer` never calls (it reads
    `trace_api.get_tracer_provider()` directly), so it cannot see spans
    `start_raw_model_span`/`end_raw_model_span` open. See
    `tests/architecture/test_raw_model_calls_use_strands_telemetry.py` for
    the test that DOES read back a real Strands span, against a patched
    provider.
    """
    from nora.app import composition

    captured: dict = {}

    def _fake_start(**kwargs):
        return object()  # opaque — `end_raw_model_span` never inspects it

    def _fake_end(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(composition, "start_raw_model_span", _fake_start)
    monkeypatch.setattr(composition, "end_raw_model_span", _fake_end)

    client = _FakeClient()
    gate_client = _gate_client(client)
    got = await gate_client.complete(system="rules", prompt="room")

    assert got.content == '{"verdict": "no_go", "relevance_score": 0.0}'
    assert got.finish_reason == "stop"
    assert captured["input_tokens"] == 999
    assert captured["output_tokens"] == 7
    assert captured["total_tokens"] == 1006
    assert captured["finish_reason"] == "stop"
    assert captured["content"] == '{"verdict": "no_go", "relevance_score": 0.0}'


async def test_a_provider_that_reports_no_usage_now_records_zero(monkeypatch):
    """The NEW, WEAKER guarantee, stated so it cannot be mistaken for the old
    one. `Tracer.end_model_invoke_span` has no "provider reported nothing, so
    write nothing" branch — see this file's own module docstring for why
    this is accepted rather than fixed."""
    from nora.app import composition

    captured: dict = {}
    monkeypatch.setattr(composition, "start_raw_model_span", lambda **kw: object())
    monkeypatch.setattr(composition, "end_raw_model_span", lambda **kw: captured.update(kw))

    client = _FakeClient(usage=None)
    gate_client = _gate_client(client)
    await gate_client.complete(system="rules", prompt="room")

    assert captured["input_tokens"] is None
    assert captured["output_tokens"] is None
    assert captured["total_tokens"] is None


async def test_start_raw_model_span_swallows_its_own_failure():
    """`nora.agent.telemetry.start_raw_model_span` itself, not the gate's
    wiring — a broken tracer provider must return `None`, never raise, for
    `end_raw_model_span`'s own `None`-is-a-no-op contract to be reachable at
    all. `monkeypatch`ing `strands.telemetry.tracer.get_tracer` is enough:
    no need to reach into Strands' own provider machinery for this half."""
    import strands.telemetry.tracer as tracer_module

    from nora.agent.telemetry import start_raw_model_span

    class _BrokenTracer:
        def start_model_invoke_span(self, **kwargs):
            raise RuntimeError("exporter is unreachable")

    tracer_module._tracer_instance = _BrokenTracer()
    try:
        span = start_raw_model_span(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            model_id="m",
        )
    finally:
        tracer_module._tracer_instance = None

    assert span is None


async def test_a_broken_tracer_never_costs_a_verdict_already_obtained():
    """Tracing is never load-bearing (`nora.obs.tracing.span`'s own rule,
    restated for the raw-span pair in `nora.agent.telemetry`'s module
    docstring) — a positive control PROVING it through the gate's REAL,
    unpatched wiring, not by patching the wrapper functions to bypass their
    own guard (which would prove nothing about whether that guard works).

    Breaks the underlying `strands.telemetry.tracer` singleton instead —
    `nora.app.composition._RelevanceGateModelClient.complete` calls the real
    `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`, which
    call the real (now broken) `get_tracer()` internally, and must swallow
    the failure THEMSELVES.

    PROBE: remove either `try/except` inside `nora.agent.telemetry.
    start_raw_model_span`/`end_raw_model_span` and this test raises instead
    of asserting — a broken tracer would then discard an already-obtained
    verdict, exactly the failure mode `_ask`'s `except Exception:
    _fail_closed("call_failed")` cannot distinguish from a real provider
    outage.
    """
    import strands.telemetry.tracer as tracer_module

    class _BrokenTracer:
        def start_model_invoke_span(self, **kwargs):
            raise RuntimeError("exporter is unreachable")

        def end_model_invoke_span(self, **kwargs):
            raise RuntimeError("span already closed")

    saved = tracer_module._tracer_instance
    tracer_module._tracer_instance = _BrokenTracer()
    try:
        client = _FakeClient()
        gate_client = _gate_client(client)
        got = await gate_client.complete(system="rules", prompt="room")
    finally:
        tracer_module._tracer_instance = saved

    assert got.content == '{"verdict": "no_go", "relevance_score": 0.0}'
    assert got.finish_reason == "stop"


async def test_the_gate_asks_for_json_with_the_instruction_in_the_system_role():
    """The request shape.

    The ROLE SPLIT: with the instruction and the untrusted transcript
    concatenated into one user string, a member typing `[assistant]:` would sit
    in exactly the role the operator's rules occupy. The `json_object` RESPONSE
    FORMAT and the absence of any tool machinery are required because the SDK's
    structured output — which converts the schema into a tool specification —
    holds fewer safety cases than this shape, at a higher prompt-token cost.

    `model`, `max_tokens` and `temperature` are asserted here too. The gate's
    ceiling is small on purpose — a gate with room to write paragraphs has room
    to start drafting the reply it must never draft — and its model is its OWN
    combo, never the tag lane's.

    `tool_choice` must NOT appear. A raw completion sends no tools, so there
    is nothing for one to select; sending `tool_choice: None` (as Strands does
    when nothing sets it) makes a thinking-mode provider refuse the call
    outright.
    """
    client = _FakeClient()
    gate_client = _gate_client(client)
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
    client = _FakeClient()
    client.completions.finish_reason = "length"
    gate_client = _gate_client(client)
    got = await gate_client.complete(system="the rules", prompt="the room")
    assert got.finish_reason == "length"

    # And a provider that omits it says NOTHING, rather than "stop".
    client.completions.finish_reason = None
    got = await gate_client.complete(system="the rules", prompt="the room")
    assert got.finish_reason == ""
