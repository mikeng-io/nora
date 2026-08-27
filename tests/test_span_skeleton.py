"""The turn skeleton (`02-instrument-blindness.md` Layer 7).

Until 2026-08-27 `nora/` opened ZERO spans — every span in Langfuse came from
Strands, inside the agent loop, so a turn failing BEFORE the model call left no
trace at all and was indistinguishable from a turn that never happened.

These tests use a real in-memory SDK exporter, so they assert what is EXPORTED
rather than that a helper was called. Deleting a `with span(...)` turns them red.
"""

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nora.obs.tracing import annotate, attached, capture_context, span

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _provider():
    """`trace.set_tracer_provider` is ONE-SHOT per process — a second call is
    ignored with a warning, so a per-test provider silently leaves every test
    after the first reading the FIRST exporter. Set it once, clear between tests.

    Found the hard way: these tests passed individually and failed as a file."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)
    yield
    # SHUT THE PROCESSOR DOWN. `set_tracer_provider` is one-shot, so the provider
    # itself cannot be un-set — it stays global for the whole pytest process. Left
    # running, its SimpleSpanProcessor exports SYNCHRONOUSLY on every span end for
    # every test that follows, and the two timing-sensitive tests elsewhere in the
    # suite (`test_a_slow_reaction_is_not_counted_as_tag_overhead`,
    # `test_prune_loop_survives_a_failing_sweep_and_keeps_looping`) then fail —
    # while passing in isolation, which is how this hid.
    provider.shutdown()


@pytest.fixture
def spans():
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def test_a_span_is_actually_exported(spans):
    with span("ingest.scope"):
        pass
    assert [s.name for s in spans.get_finished_spans()] == ["ingest.scope"]


def test_an_exception_is_recorded_and_re_raised(spans):
    """Re-raise is the property. An observation that changes control flow is not
    an observation — a span must never turn a failing turn into a passing one."""
    with pytest.raises(ValueError), span("ingest.audit"):
        raise ValueError("boom")
    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.outcome"] == "ValueError"
    assert recorded.status.status_code.name == "ERROR"


def test_cancellation_is_recorded_and_re_raised(spans):
    """`BaseException`, not `Exception`. Cancellation is not an `Exception`, so a
    wrapper catching only `Exception` leaves the cancel path unrecorded — the
    defect the Standing debrief shipped and its review caught. A cancelled turn
    is exactly the turn worth seeing."""
    async def run():
        with span("engagement.invoke"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.outcome"] == "CancelledError"


def test_a_broken_attribute_does_not_break_the_body(spans):
    """A tracer that cannot record must not stop a turn."""
    class Unstringable:
        def __str__(self): raise RuntimeError("nope")

    ran = False
    with span("ingest", bad=Unstringable()):
        ran = True
    assert ran


def test_children_nest_under_the_turn(spans):
    """The skeleton's whole point: a turn that ends early ends VISIBLY, at a
    named stage, rather than as a trace that simply stops.

    **This test OPENS ITS OWN TWO SPANS**, so it proves the `span` helper nests
    and nothing more. It stayed green for the whole of issue #64, while the
    engagement pipeline had no root span at all and every stage was its own
    trace — a test that constructs the thing it verifies cannot see that. The
    pipeline's real topology is asserted against the real pipeline in
    `tests/unit/test_engagement_pipeline.py::test_every_stage_span_hangs_under_the_tag_root`;
    read THAT one as the evidence, and this one as a unit test of the helper.
    """
    with span("ingest"), span("ingest.scope"):
        pass
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert finished["ingest.scope"].parent.span_id == finished["ingest"].context.span_id


def test_a_span_that_cannot_start_still_runs_the_body(monkeypatch):
    """Acquisition is guarded, not just recording.

    The debrief stage runs inside a `finally` AFTER the tag pipeline's outer
    `except BaseException` has already run, so anything raised there escapes
    `run()` and takes out the whole TAG rather than just the trace. A span must
    never be able to do that. (Constraint from the session that owns that path.)
    """
    import nora.obs.tracing as t

    def boom(_name):
        raise RuntimeError("tracer is broken")

    monkeypatch.setattr(t.trace, "get_tracer", boom)
    ran = False
    with t.span("engagement.debrief"):
        ran = True
    assert ran, "a broken tracer must not stop the body from running"


def test_a_span_that_cannot_close_does_not_escape(monkeypatch):
    """Same reason, the other end: a failing exporter on span end must not
    surface as a tag failure."""
    import nora.obs.tracing as t

    class BadCM:
        def __enter__(self): return _NoopSpan()
        def __exit__(self, *a): raise RuntimeError("exporter down")

    class _Tracer:
        def start_as_current_span(self, _name): return BadCM()

    monkeypatch.setattr(t.trace, "get_tracer", lambda _n: _Tracer())
    with t.span("engagement.deliver"):
        pass  # must not raise


class _NoopSpan:
    def set_attribute(self, *a): pass
    def set_status(self, *a): pass
    def record_exception(self, *a): pass


# ---------------------------------------------------------------------------
# `annotate`, `capture_context`, `attached` — the three helpers issue #64's fix
# needed and the span skeleton did not have. Each one is exercised here for the
# property that makes it safe at the call site that uses it, because that call
# site (`EngagementPipeline`) runs them in a `finally` PAST its own outer
# handler: an exception from an observation there skips the lock release and
# wedges the conversation forever, with no error anywhere.
# ---------------------------------------------------------------------------


def test_annotate_adds_to_the_span_that_is_current(spans):
    """The attributes `span` cannot take, because they are only known on the way
    out — an outcome, a stop reason, a latency."""
    with span("engagement.turn"):
        annotate(**{"nora.outcome": "silent", "nora.delivered": False})
    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.outcome"] == "silent"
    assert recorded.attributes["nora.delivered"] is False


def test_annotate_outside_any_span_is_a_no_op(spans):
    """The unit tests for a stage call it with no span open at all. A helper
    that raised there would make the stage untestable in isolation."""
    annotate(**{"nora.outcome": "written"})
    assert spans.get_finished_spans() == ()


def test_annotate_on_an_ended_span_does_not_raise(spans):
    """MEASURED, 2026-08-28, and it is why `EngagementPipeline` annotates the
    per-turn span it opens rather than the dispatch root: the root is already
    closed when a QUEUED tag runs, and the SDK drops a late attribute with a log
    line and no error. Silent, so it has to be designed around rather than
    caught."""
    with span("engagement"):
        pass
    annotate(**{"nora.outcome": "send_reply"})     # must not raise
    (recorded,) = spans.get_finished_spans()
    assert "nora.outcome" not in (recorded.attributes or {})


def test_annotate_survives_a_broken_tracer(monkeypatch):
    """Same rule as `span`'s own attribute guard: a tracer that cannot record
    must not stop a turn."""
    import nora.obs.tracing as t

    def boom():
        raise RuntimeError("tracer is broken")

    monkeypatch.setattr(t.trace, "get_current_span", boom)
    annotate(**{"nora.outcome": "send_reply"})     # must not raise


def test_attached_replaces_the_inherited_context_rather_than_nesting_in_it(spans):
    """THE PROPERTY THE QUEUE NEEDS. `asyncio.create_task` copies contextvars
    and OTel's context is a contextvar, so a queued tag spawned from the
    previous turn's `finally` inherits that turn's span. `attached` must
    OVERWRITE that, not add to it — otherwise two people's messages share a
    trace and the trace asserts a causal relation that does not exist.

    PROBE: implement `attached` with a `start_as_current_span(context=...)` that
    merges rather than replaces, and the child keeps the ambient parent.
    """
    with span("carrier.root"):
        carrier = capture_context()

    with span("ambient.turn"), attached(carrier), span("late.child"):
        pass

    finished = {s.name: s for s in spans.get_finished_spans()}
    assert finished["late.child"].parent.span_id == \
        finished["carrier.root"].context.span_id
    assert finished["late.child"].context.trace_id == \
        finished["carrier.root"].context.trace_id
    assert finished["late.child"].context.trace_id != \
        finished["ambient.turn"].context.trace_id


def test_attached_with_no_carrier_starts_a_fresh_trace(spans):
    """The fail-closed direction: a lost capture becomes a trace of its own,
    never a span adopted by whatever task happened to be running."""
    with span("ambient.turn"), attached(None), span("orphan"):
        pass

    finished = {s.name: s for s in spans.get_finished_spans()}
    assert finished["orphan"].parent is None
    assert finished["orphan"].context.trace_id != \
        finished["ambient.turn"].context.trace_id


def test_attached_runs_its_body_even_when_the_context_api_is_broken(monkeypatch):
    """It wraps `_run_admitted`, whose `finally` releases R-10's
    per-conversation lock and dispatches R-30's queue. An exception escaping
    here would skip `release_and_dispatch` and the conversation would answer
    nobody, forever, with no error anywhere."""
    import nora.obs.tracing as t

    monkeypatch.setattr(
        t.otel_context, "attach",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("context is broken")),
    )
    ran = False
    with attached(None):
        ran = True
    assert ran


def test_attached_survives_a_failing_detach(monkeypatch):
    """The other end, for the same reason — `detach` is in the `finally` that
    the lock release sits behind."""
    import nora.obs.tracing as t

    monkeypatch.setattr(
        t.otel_context, "detach",
        lambda _tok: (_ for _ in ()).throw(RuntimeError("context is broken")),
    )
    with attached(None):
        pass          # must not raise on exit


def test_capture_context_degrades_to_an_empty_context(monkeypatch):
    """A capture that cannot read the current context returns something
    `attached` accepts, rather than raising into the dispatch path."""
    import nora.obs.tracing as t

    monkeypatch.setattr(
        t.otel_context, "get_current",
        lambda: (_ for _ in ()).throw(RuntimeError("context is broken")),
    )
    carrier = capture_context()
    with attached(carrier):
        pass          # must not raise
