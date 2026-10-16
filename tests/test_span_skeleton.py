"""The turn skeleton.

`nora/` opens spans of its own. Spans opened only by Strands, inside the agent loop, would
leave a turn failing BEFORE the model call with no trace at all, indistinguishable from a
turn that never happened.

These tests use a real in-memory SDK exporter, so they assert what is EXPORTED rather than
that a helper was called. Deleting a `with span(...)` turns them red.
"""

import asyncio

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nora.obs.tracing import (
    TRACE_KIND,
    annotate,
    attached,
    build_sampler,
    capture_context,
    span,
)

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _provider():
    """A provider of this file's own, reached by patching `get_tracer` — the same shape as
    `tests/conftest.py`'s `exported_spans`, and for the reason it gives:
    `trace.set_tracer_provider` is ONE-SHOT per process.

    A GLOBAL provider installed here would depend on this being the first file to do so.
    `evals.backbone._prefill_capture()` installs one too (it must: it reads a span the
    composed subject emits through the global tracer), so any run in which an eval
    integration test collected first would leave every test here reading a provider with
    the wrong sampler and the wrong exporter. Patching the lookup keeps the capture local
    and order-free.

    Built with `build_sampler()`, production's own rule — this file is where the rule's
    own tests live, and a provider that sampled everything could not see it at all."""
    from nora.obs import tracing as _tracing

    provider = TracerProvider(sampler=build_sampler())
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    patch = pytest.MonkeyPatch()
    patch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
    yield
    patch.undo()
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
    """`BaseException`, not `Exception`. Cancellation is not an `Exception`, so a wrapper
    catching only `Exception` leaves the cancel path unrecorded. A cancelled turn is
    exactly the turn worth seeing."""
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
    """The skeleton's whole point: a turn that ends early ends VISIBLY, at a named stage,
    rather than as a trace that simply stops.

    **This test OPENS ITS OWN TWO SPANS**, so it proves the `span` helper nests and
    nothing more: a test that constructs the thing it verifies cannot see a pipeline with
    no root span. The pipeline's real topology is asserted against the real pipeline in
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
# `annotate`, `capture_context`, `attached` — the three helpers the span skeleton needs.
# Each one is exercised here for the property that makes it safe at the call site that
# uses it, because that call site (`EngagementPipeline`) runs them in a `finally` PAST its
# own outer handler: an exception from an observation there skips the lock release and
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
    """`EngagementPipeline` annotates the per-turn span it opens rather than the dispatch
    root: the root is already closed when a QUEUED tag runs, and the SDK drops a late
    attribute with a log line and no error. Silent, so it has to be designed around rather
    than caught."""
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


# ---------------------------------------------------------------------------
# R-76 — WHICH TRACES REACH THE COLLECTOR.
#
# Langfuse is an LLM-observability tool: a trace belongs in it when the work
# under its root can contain a generation. Everything else — sequencing stages,
# writing a row, counting a refusal — is plumbing, and plumbing's questions are
# Prometheus'.
#
# THE DECISION IS ABOUT ROOTS, NOT ABOUT SPANS. `engagement.admission` has no
# model call in it either and must keep being exported, because as a CHILD of a
# trace that has one it is how a turn that ended early ends visibly at a named
# stage. The sampler is composed under `ParentBased` so a child never
# re-decides; the tests below pin both directions of that.
# ---------------------------------------------------------------------------


def test_a_plumbing_root_takes_its_whole_subtree_with_it(spans):
    """The drop is total and it reaches descendants, not just direct children —
    `nora/ingest/pipeline.py` is two levels deep in places and all of it must go.

    POSITIVE CONTROL in the same test: an undeclared root opened right
    afterwards IS exported, so «nothing came out» cannot be «the exporter was
    dead».

    PROBE: change `Decision.DROP` to `RECORD_AND_SAMPLE` in the sampler.
    """
    with (
        span("plumbing.root", **{TRACE_KIND: "plumbing"}),
        span("plumbing.child"),
        span("plumbing.grandchild"),
    ):
        pass
    assert spans.get_finished_spans() == ()

    with span("something.undeclared"):
        pass
    assert [s.name for s in spans.get_finished_spans()] == ["something.undeclared"]


def test_a_generation_root_keeps_the_plumbing_underneath_it(spans):
    """THE HALF THAT IS EASY TO GET WRONG, and the reason this is a rule about
    roots. A stage span with no model call in it is not the same thing as a
    plumbing TRACE: under a root that has a generation, that stage is exactly
    what makes an early exit legible.

    A child here declares itself `plumbing` and is exported anyway, because
    `ParentBased` never consults the delegate for a child. A sampler applied
    per-span instead would swallow the stage spans out of the middle of a real
    turn — every trace would have holes in it where the guards ran.

    PROBE: build the provider with the bare `_PlumbingIsNotATrace()` instead of
    `build_sampler()`'s `ParentBased(root=...)` and the child vanishes.
    """
    with (
        span("turn.root", **{TRACE_KIND: "generation"}),
        span("turn.stage", **{TRACE_KIND: "plumbing"}),
    ):
        pass

    assert {s.name for s in spans.get_finished_spans()} == {"turn.root", "turn.stage"}
    assert len({s.context.trace_id for s in spans.get_finished_spans()}) == 1


def test_a_root_that_declares_nothing_is_exported(spans):
    """FAIL-OPEN, and deliberately so. Getting this wrong towards export costs
    noise, which is visible and complains about itself; getting it wrong towards
    silence costs a missing trace for a model call — an invisible gap in exactly
    the traces the rule exists to protect. A new entry point that forgets to
    declare must show up, not disappear.

    PROBE: invert the sampler's default so only `generation` is kept.
    """
    with span("a.new.entry.point"):
        pass
    assert [s.name for s in spans.get_finished_spans()] == ["a.new.entry.point"]


def test_an_unknown_trace_kind_is_exported_and_reported(spans, caplog):
    """The vocabulary is a `Literal` and the check is at RUNTIME, because the
    sampler reads a value that arrives as a plain attribute. An unrecognised
    kind is LOGGED and the trace is kept — withholding it would make a typo
    indistinguishable from a deliberate drop, which is the one failure mode this
    rule cannot afford.

    PROBE: delete the membership check and `"plumbin"` starts silently
    exporting — no, silently NOT dropping — with nothing said either way.
    """
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="nora.tracing"),
        span("typo.root", **{TRACE_KIND: "plumbin"}),
    ):
        pass

    assert [s.name for s in spans.get_finished_spans()] == ["typo.root"]
    assert "is not in the vocabulary" in caplog.text


def test_the_sampler_reads_the_attributes_a_span_started_with(spans):
    """`span` passes its attributes to `start_as_current_span` rather than
    setting them afterwards, and that is load-bearing rather than tidy: a
    `Sampler` is handed only the start attributes, so a `trace_kind` set one
    line later is a kind the decision was already made without — and the code
    would look correct while the rule never fired.

    PROBE: move the attribute dict out of `start_as_current_span` back into a
    `set_attribute` loop after `__enter__` and this reddens.
    """
    with span("declared.at.start", **{TRACE_KIND: "plumbing"}):
        annotate(**{TRACE_KIND: "generation"})     # too late to matter, by design
    assert spans.get_finished_spans() == ()


def test_an_attribute_that_cannot_be_rendered_costs_only_itself(spans):
    """Coercion is PER KEY. Setting attributes in a loop inside one guard would let the first
    unstringable value silently abandon every attribute after it, including a `trace_kind`
    that decides whether the trace exists.

    PROBE: wrap `_safe_attributes`' body in a single `try` and `keeper` is lost with the
    broken one.
    """
    class Unstringable:
        def __str__(self): raise RuntimeError("nope")

    with span("partial", bad=Unstringable(), keeper="kept"):
        pass

    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["keeper"] == "kept"
    assert "bad" not in recorded.attributes


# ---------------------------------------------------------------------------
# `traced_read` — `nora/infra/**` store reads. The ONE wrapper
# `nora.engagement.context.TranscriptInjection` and `nora.app.runtime`'s Dreamer cursor
# reads both go through, tested here in isolation from either call site: what it names the
# span, what it records on success, and that a raising read still ends up `span`'s own
# ERROR path.
# ---------------------------------------------------------------------------


async def test_traced_read_names_the_span_store_dot_store_dot_method(spans):
    async def _read():
        return ["a", "b"]

    from nora.obs.tracing import traced_read

    result = await traced_read("record", "recent", _read())

    assert result == ["a", "b"]
    assert [s.name for s in spans.get_finished_spans()] == ["store.record.recent"]


async def test_traced_read_records_row_count_and_duration_on_success(spans):
    async def _read():
        return [1, 2, 3]

    from nora.obs.tracing import traced_read

    await traced_read("notes", "standing", _read())

    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.store.row_count"] == 3
    assert recorded.attributes["nora.store.duration_ms"] >= 0


async def test_traced_read_uses_count_when_the_result_has_no_len(spans):
    """`MemoryStore.representation` answers a `Recollection`, which has no `__len__`, so
    `_row_count` alone would leave it unmeasured forever even though the read plainly has
    a count. `count=` is the caller's own answer to "how many", for a result `_row_count`
    cannot read.

    PROBE: make `traced_read` ignore its `count` argument (always fall back to
    `_row_count`) and this reddens — `nora.store.row_count` disappears.
    """
    class _Recollection:
        def __init__(self, conclusions):
            self.conclusions = conclusions

    async def _read():
        return _Recollection(("a", "b", "c"))

    from nora.obs.tracing import traced_read

    await traced_read(
        "memory", "representation", _read(), count=lambda r: len(r.conclusions),
    )

    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.store.row_count"] == 3


async def test_traced_read_leaves_row_count_off_a_scalar_result(spans):
    """A watermark, a `bool`, a `Standing | None` — none of these is a row
    count, and `traced_read` must not invent one for them.

    PROBE: change `_row_count` to return `1` for anything that is not `None`
    and this reddens.
    """
    async def _read():
        return 42

    from nora.obs.tracing import traced_read

    await traced_read("language", "watermark_for", _read())

    (recorded,) = spans.get_finished_spans()
    assert "nora.store.row_count" not in recorded.attributes
    assert "nora.store.duration_ms" in recorded.attributes


async def test_traced_read_records_zero_rows_for_none(spans):
    """`None` — a `Standing` store's «no row yet» — is a real zero, not «not
    measurable»: `_row_count` treats it differently from a `bool` or an `int`
    on purpose."""
    async def _read():
        return None

    from nora.obs.tracing import traced_read

    await traced_read("standing", "read", _read())

    (recorded,) = spans.get_finished_spans()
    assert recorded.attributes["nora.store.row_count"] == 0


async def test_traced_read_ends_its_span_with_error_status_on_a_raising_read(spans):
    """`span`'s own failure path, not a second one `traced_read` adds — see
    that function's docstring for why a second `except` here would be a
    place for the two to drift.

    PROBE: add a `try/except Exception: return default` around `await coro`
    in `traced_read` and this reddens — the span goes back to OK.
    """
    async def _read():
        raise RuntimeError("db down")

    from nora.obs.tracing import traced_read

    with pytest.raises(RuntimeError):
        await traced_read("record", "recent", _read())

    (recorded,) = spans.get_finished_spans()
    assert recorded.status.status_code.name == "ERROR"
    assert recorded.attributes["nora.outcome"] == "RuntimeError"
    assert "nora.store.row_count" not in recorded.attributes
