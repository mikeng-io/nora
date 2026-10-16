"""A trace says WHICH CONVERSATION it belongs to, and WHAT KIND of work each
span is, in the vocabulary Langfuse and OTel GenAI read.

Without a `sessionId`, Langfuse's `/api/public/sessions` is empty although every
engagement root carries `nora.conversation_id`; and untyped `SPAN`s
(`store.*` reads, stage spans, one `execute_event_loop_cycle` per model call)
would bury the few that are model calls unless they are separated by name.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import nora.agent.telemetry as telemetry
from nora.agent.telemetry import TraceIdentityProcessor
from nora.obs.tracing import build_sampler


@pytest.fixture
def rig():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=build_sampler())
    provider.add_span_processor(TraceIdentityProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider.get_tracer("t"), exporter
    provider.shutdown()


def _attrs(exporter: InMemorySpanExporter) -> dict[str, dict]:
    return {s.name: dict(s.attributes or {}) for s in exporter.get_finished_spans()}


def test_a_span_carrying_a_conversation_id_gets_the_session_keys(rig):
    tracer, exporter = rig
    with tracer.start_as_current_span("engagement", attributes={"nora.conversation_id": "g1/c2"}):
        pass
    got = _attrs(exporter)["engagement"]
    assert got["session.id"] == "g1/c2"
    assert got["gen_ai.conversation.id"] == "g1/c2"


def test_a_child_inherits_the_session_from_its_parent(rig):
    """The Strands spans and the `store.*` reads are opened below the root and
    never carry the id themselves; Langfuse wants it on each span."""
    tracer, exporter = rig
    with (
        tracer.start_as_current_span(
            "engagement", attributes={"nora.conversation_id": "g1/c2"},
        ),
        tracer.start_as_current_span("invoke_agent nora"),
        tracer.start_as_current_span("store.notes.standing"),
    ):
        pass
    got = _attrs(exporter)
    assert got["invoke_agent nora"]["session.id"] == "g1/c2"
    assert got["store.notes.standing"]["session.id"] == "g1/c2"


def test_an_attribute_set_after_the_span_started_is_not_seen(rig):
    """Strands opens a span, then sets `trace_attributes` on it — past
    `on_start`. So the bare `conversation_id` of `trace_attributes` stamps
    nothing on a Strands span; it gets its session from its parent. Pinned so
    nobody re-adds that key believing it works (see the installed
    strands/telemetry/tracer.py)."""
    tracer, exporter = rig
    with tracer.start_as_current_span("invoke_agent nora") as sp:
        sp.set_attribute("conversation_id", "g1/c2")
    assert "session.id" not in _attrs(exporter)["invoke_agent nora"]


def test_a_detached_span_does_not_take_another_rooms_session(rig):
    """Inheritance follows trace parentage, not the ambient span: a span started
    under an empty context inside room A's span is a new trace root and gets no
    session. The failure this guards is room B's trace filed under room A."""
    from opentelemetry.context import Context

    tracer, exporter = rig
    with (
        tracer.start_as_current_span("engagement", attributes={"nora.conversation_id": "A"}),
        tracer.start_as_current_span("detached", context=Context()),
    ):
        pass
    assert _attrs(exporter)["engagement"]["session.id"] == "A"
    assert "session.id" not in _attrs(exporter)["detached"]


def test_a_span_with_no_conversation_gets_no_session(rig):
    tracer, exporter = rig
    with tracer.start_as_current_span("dream:culture"):
        pass
    assert "session.id" not in _attrs(exporter)["dream:culture"]


def test_a_dreamer_unit_takes_its_pass_as_its_session(rig):
    """A Dreamer unit has no room. Its root carries the tick that dispatched it,
    so one pass's units group as one Langfuse session (`dream_loop`)."""
    tracer, exporter = rig
    with (
        tracer.start_as_current_span(
            "dream:culture", attributes={"nora.dreamer.pass_id": "dream.pass:T1"},
        ),
        tracer.start_as_current_span("invoke_agent dreamer:culture"),
    ):
        pass
    got = _attrs(exporter)
    assert got["dream:culture"]["session.id"] == "dream.pass:T1"
    assert got["invoke_agent dreamer:culture"]["session.id"] == "dream.pass:T1"


@pytest.mark.parametrize("bad", ["x" * 201, "房間/頻道", ""])
def test_an_id_langfuse_would_drop_is_not_stamped(rig, bad):
    """Langfuse drops a session id over 200 characters or non-ASCII — silently.
    Not stamping it keeps the trace in no session, which is what it would end
    up in anyway, and says so in the data rather than hiding it."""
    tracer, exporter = rig
    with tracer.start_as_current_span("engagement", attributes={"nora.conversation_id": bad}):
        pass
    assert "session.id" not in _attrs(exporter)["engagement"]


@pytest.mark.parametrize(("name", "kind"), [
    ("store.notes.standing", "retriever"),
    ("store.memory.peer_card", "retriever"),
    ("engagement.admission", "guardrail"),
    ("engagement.turn", "chain"),
    ("engagement.deliver", "chain"),
    ("agent.prefill", "chain"),
    ("execute_event_loop_cycle", "chain"),
])
def test_a_span_is_typed_by_what_it_is(rig, name, kind):
    tracer, exporter = rig
    with tracer.start_as_current_span(name):
        pass
    assert _attrs(exporter)[name]["langfuse.observation.type"] == kind


@pytest.mark.parametrize(
    "name", ["invoke_agent nora", "chat", "execute_tool web_fetch", "engagement"],
)
def test_a_span_that_is_not_in_the_table_is_left_to_langfuse(rig, name):
    """Model calls, agents and tools are typed by Langfuse from the GenAI
    attributes (AGENT, GENERATION, TOOL); typing them
    here would put a second opinion over the SDK's."""
    tracer, exporter = rig
    with tracer.start_as_current_span(name):
        pass
    assert "langfuse.observation.type" not in _attrs(exporter)[name]


def test_a_type_the_span_already_declared_is_kept(rig):
    tracer, exporter = rig
    with tracer.start_as_current_span(
        "store.x", attributes={"langfuse.observation.type": "tool"},
    ):
        pass
    assert _attrs(exporter)["store.x"]["langfuse.observation.type"] == "tool"


def test_a_broken_attribute_read_never_costs_the_span(rig, monkeypatch):
    """Never load-bearing: the same rule `span()` and provider setup hold."""
    tracer, exporter = rig

    def boom(*_a, **_k):
        raise RuntimeError("tracer exploded")

    monkeypatch.setattr(telemetry, "_session_id_for", boom)
    with tracer.start_as_current_span("engagement", attributes={"nora.conversation_id": "a"}):
        pass
    assert "engagement" in _attrs(exporter)


def test_the_provider_that_setup_registers_carries_the_processor(monkeypatch):
    """As in `test_setup_agent_tracing_registers_the_provider_
    globally`: a class that exists and is tested alone proves nothing about the
    provider the deployment runs. Read the EFFECT through the provider setup
    hands to `set_tracer_provider`."""
    registered: list[TracerProvider] = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", registered.append, raising=True)
    telemetry.setup_agent_tracing(
        sampler=build_sampler(), service_name="nora", exporter="otlp_http",
        endpoint="http://127.0.0.1:1/v1/traces", headers_env="",
    )
    exporter = InMemorySpanExporter()
    provider = registered[0]
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("t").start_as_current_span(
        "engagement", attributes={"nora.conversation_id": "g1/c2"},
    ):
        pass
    assert _attrs(exporter)["engagement"]["session.id"] == "g1/c2"
