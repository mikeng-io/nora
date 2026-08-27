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

from nora.obs.tracing import span

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
    named stage, rather than as a trace that simply stops."""
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
