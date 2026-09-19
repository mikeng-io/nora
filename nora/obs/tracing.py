"""OTel tracing setup (spec C.9). Strands instruments the agent loop/tools/model calls
itself; this module only configures the exporter and resource attributes.

The OTLP exporter is HTTP/protobuf, not gRPC: Langfuse's collector endpoint
(`/api/public/otel/v1/traces`) speaks HTTP, and `opentelemetry-exporter-otlp-
proto-grpc` was never installed to begin with (Phase 4's mypy override covered
exactly that missing import). `otlp_http` replaces the old `otlp` config value
outright rather than joining it — a gRPC option that nothing installs and
nothing can ever reach is worse than no option, and there was never a
production config using it (the live `config.toml` has always been `file`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal, get_args

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporter
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
)
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from opentelemetry.trace.span import TraceState
from opentelemetry.util.re import parse_env_headers
from opentelemetry.util.types import Attributes

log = logging.getLogger("nora.tracing")

#: The attribute a ROOT span uses to say what kind of work is under it, and the
#: only thing `build_sampler` reads. A CLOSED set as a `Literal` so the
#: vocabulary is DERIVED from the code rather than hand-copied into the sampler.
#:
#: * `generation` — a model call can happen under this root. The trace is what
#:   the collector exists for; it is kept whether or not this particular run
#:   reached the model (a tag refused at admission is still a tag, and DEC-141's
#:   whole point is that it leaves a trace SAYING it was refused).
#: * `plumbing` — deterministic stage sequencing with no model call anywhere
#:   under it. Never exported. Its signal belongs on a counter.
TraceKind = Literal["generation", "plumbing"]

#: The attribute name itself. `nora.` prefixed like every other attribute this
#: module's callers set, so nothing in the collector has to know it is special.
TRACE_KIND = "nora.trace_kind"

_TRACE_KINDS: frozenset[str] = frozenset(get_args(TraceKind))

#: What `capture_context` hands back and `attached` takes. Aliased here so a
#: pipeline that carries one across a queue names THIS module rather than
#: `opentelemetry` — the same reason `nora.app.agent_runner` types Strands'
#: return as `Any`: a stage should not have to import a tracing vendor to hold
#: an opaque handle it never inspects.
TraceCarrier = Context


class _PlumbingIsNotATrace(Sampler):
    """THE ROOT DECISION (R-76): a trace reaches the collector when the work
    under it can contain a generation.

    Langfuse is an LLM-observability tool. Everything Nora does that is not a
    model call — sequencing ingest stages, writing a row, resolving an
    identity, counting a refusal — is plumbing, and plumbing's questions
    («how many», «how often», «with what outcome») are Prometheus' questions.
    `nora/ingest/pipeline.py` opens six spans and there is no model call under
    any of them; it is also the highest-volume entry point in the system, so it
    was 97 of the operator's last 100 traces (measured 2026-08-28) — a
    collector that is 97% plumbing cannot be read for the turns that matter.

    **This is a decision about ROOTS, not about every span**, and that
    distinction is the whole design. `engagement.admission` has no generation in
    it either, and it must keep being exported: as a CHILD of a trace that has
    one, it is how a turn that ended before the model call ends visibly at a
    named stage. Composed under `ParentBased`, so a child never re-decides — it
    inherits whatever its root was given, which is also what keeps a QUEUED
    tag's turn (re-attached to a captured context whose root span has already
    ended) in the same trace as the root that admitted it.

    **DEFAULT: EXPORT.** An undeclared root is kept, and the direction is
    deliberate. Getting it wrong towards export costs noise, which is visible
    and complains about itself; getting it wrong towards silence costs a
    missing trace for a model call, which is an invisible gap in exactly the
    traces this rule exists to protect. Only an explicit `plumbing` drops. An
    undeclared value is LOGGED and kept, for the same reason.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        declared = (attributes or {}).get(TRACE_KIND)
        if declared is not None and declared not in _TRACE_KINDS:
            log.warning(
                "span %s declares trace kind %r, which is not in the vocabulary %s "
                "— exporting it anyway; add it to TraceKind",
                name, declared, sorted(_TRACE_KINDS),
            )
            declared = None
        decision = Decision.DROP if declared == "plumbing" else Decision.RECORD_AND_SAMPLE
        return SamplingResult(decision, attributes, trace_state)

    def get_description(self) -> str:
        return "NoraPlumbingIsNotATrace"


def build_sampler() -> Sampler:
    """The sampler `setup_tracing` installs, exposed so a TEST provider can be
    built with the SAME rule production runs.

    `tests/conftest.py`'s `exported_spans` fixture uses this. Without it a test
    provider would export spans production drops, and every assertion about
    «what reaches the collector» would be measuring a provider that no
    deployment has — the silent-match failure, in the instrument that exists to
    find silent matches.
    """
    return ParentBased(root=_PlumbingIsNotATrace())


def setup_tracing(
    *, enabled: bool, exporter: str, endpoint: str, headers_env: str = "",
    service_name: str = "nora",
) -> None:
    if not enabled:
        return
    try:
        span_exporter = _build_exporter(exporter, endpoint, headers_env)
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=build_sampler(),
        )
        provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        # Tracing is never load-bearing — a bad endpoint, an unparsable headers_env
        # value, or (once spans actually flow) an unreachable collector must never
        # take the app down. Setup-time failures land here; export-time failures
        # (the network call itself) happen later on BatchSpanProcessor's own
        # background thread, which the OTel SDK already isolates from the app —
        # see tests/test_tracing.py's PROBE-derived test for the latter.
        log.warning("tracing setup failed; continuing without tracing", exc_info=True)


@contextmanager
def span(name: str, **attributes: object) -> Iterator[None]:
    """One named span, and **never load-bearing** — the same rule `setup_tracing`
    keeps one function up.

    Until 2026-08-27 `nora/` opened **zero** spans: every span reaching Langfuse
    came from Strands, from inside the agent loop, so a turn that failed BEFORE
    the model call produced no trace at all and was indistinguishable from a turn
    that never happened (`design/dreamer-agent-loop/02-instrument-blindness`
    Layer 7). This is the skeleton that closes it — one span per turn, children
    named for the stages that already exist, so a turn ending early ends
    **visibly, at a named stage**.

    **It catches `BaseException`, records, and RE-RAISES.** Three properties, and
    all three are load-bearing:

    - `BaseException`, not `Exception`, because **cancellation is not an
      `Exception`** — a wrapper that only catches `Exception` leaves the cancel
      path unrecorded, which is the defect the Standing debrief shipped and its
      review caught. A cancelled turn is exactly the turn worth seeing.
    - It **re-raises, always**. An observation that changes control flow is not an
      observation. A span must never turn a failing turn into a passing one, and
      must never swallow a cancel.
    - The *recording itself* is wrapped, because a broken tracer must not break a
      turn. If setting an attribute throws, the turn continues untraced.

    A no-op when tracing is off: `get_tracer` returns a non-recording tracer, so
    this costs a context manager and nothing else.

    **Attributes are passed AT START, not set afterwards**, and that is what
    makes `build_sampler`'s rule reachable at all: a `Sampler` sees only what
    the span was started with, so a `nora.trace_kind` set one line later is a
    kind the sampling decision has already been made without. They are coerced
    to safe scalars FIRST (`_safe_attributes`, which drops a value it cannot
    render rather than raising) so that a broken attribute still costs only
    that attribute — never the span, and never the body.
    """
    try:
        cm = trace.get_tracer("nora").start_as_current_span(
            name, attributes=_safe_attributes(attributes),
        )
        sp = cm.__enter__()
    except Exception:
        # ACQUISITION is guarded too, not just recording. The debrief stage runs
        # inside a `finally` AFTER the tag pipeline's outer `except BaseException`
        # has already run, so anything raised there escapes `run()` and takes out
        # the whole tag rather than just the trace. A span must never be able to
        # do that. Untraced body still runs.
        log.debug("span %s could not start; running untraced", name, exc_info=True)
        yield
        return
    try:
        try:
            yield
        except BaseException as exc:
            try:
                sp.set_attribute("nora.outcome", type(exc).__name__)
                sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                if isinstance(exc, Exception):
                    sp.record_exception(exc)
            except Exception:
                log.debug("span error not recorded for %s", name, exc_info=True)
            raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            # Same reason: a failing exporter on span end must not surface as a
            # tag failure. See the acquisition guard above.
            log.debug("span %s did not close cleanly", name, exc_info=True)


#: The two spellings of "this span is a model call", emitted TOGETHER by
#: `generation` and nowhere else. PUBLIC so the architecture test DERIVES the
#: vocabulary it forbids elsewhere rather than hand-copying it (AGENTS.md).
#:
#: `gen_ai.*` is the OTel GenAI semantic convention — what a future collector,
#: and Jaeger's own attribute search, read. `langfuse.observation.*` is what
#: TODAY'S collector renders a generation from: without it Langfuse shows the
#: span as a plain span with no model, no latency attribution and no place to
#: put input/output, which is the difference between a trace of a model call
#: and a rectangle that happens to be named after one.
#:
#: They are two spellings of one fact and neither is guessed. Both were verified
#: against a live trace (`relevance_gate`, 2026-09-08) before this constant
#: existed to hold them: it is the block that trace proved, lifted out of the
#: one call site that had it.
GENERATION_ATTRIBUTES = ("langfuse.observation.type", "gen_ai.operation.name")


@contextmanager
def generation(
    name: str,
    *,
    model: str = "",
    root: bool = False,
    prompt: str = "",
    completion: str = "",
    attributes: dict[str, object] | None = None,
) -> Iterator[None]:
    """One MODEL CALL, spelled the one way Langfuse renders as a generation.

    **This exists because the spelling was copied, and copies drift.** Two call
    sites hand-rolled the attribute block — `nora/ingest/relevance_gate.py` and
    `nora/engagement/stages/debrief.py` — and by the time there were two they
    had already diverged: the debrief carried `langfuse.observation.input` and
    `output`, the gate carried neither, so one generation was readable in the
    collector and the other was a name with a latency. Everything else that
    calls a model carried no spelling at all and rendered as a bare span. Four
    dialects for one fact (measured against 1041 live traces, 2026-09-09).

    **`root=True` is the R-76 declaration, and it is not the default.** A
    generation that hangs under a turn's root inherits that root's sampling
    decision and must NOT re-declare anything — `ParentBased` gives a child its
    parent's decision, so `nora.trace_kind` on a non-root is inert (and reads,
    to the next person, as though it were doing something). Pass `root=True`
    only where this span genuinely opens its own trace: work reached off an
    ingest path that is itself plumbing, or spawned work that has detached with
    `attached(None)`.

    **`prompt`/`completion` must ALREADY BE SCREENED** — `nora.agent.security`'s
    `trace_safe` is the screen, and it cannot be applied here: `nora.obs` sits
    BELOW `nora.agent` in the import contract (`pyproject.toml`, "layered
    architecture"), so this module cannot reach the egress predicates and must
    not pretend to. A trace is an egress — the collector is a third party over
    the network — so a caller that passes raw model text past this parameter
    has moved an identifier onto the wire. `tests/architecture/
    test_generation_spans_use_the_helper.py` is what keeps the dialect here;
    the screen is the caller's, stated in `docs/observability.md`.

    **`attributes` is a DICT, not `**kwargs`**, unlike `span`'s. `span` has no
    keyword parameters of its own, so a caller's `**{...}` cannot collide with
    one; this function has four, and a `dict[str, str]` splatted past them is
    a type error mypy reports at the CALL SITE (`root` expects a `bool`) rather
    than a name the caller can see. One explicit parameter costs a `attributes=`
    at three call sites and removes the collision entirely.

    Both halves are OPTIONAL and omitted rather than blanked when empty: an
    absent `langfuse.observation.input` reads as "this span does not carry the
    prompt", where an empty string reads as "the prompt was empty", and those
    are different claims about the same turn.
    """
    attrs: dict[str, object] = {
        "langfuse.observation.type": "generation",
        "gen_ai.operation.name": "chat",
    }
    # OMITTED, not blanked, when the model is not known yet. `""` renders in
    # Langfuse as a generation whose model is the empty string — a positive
    # claim that is false — where an absent attribute reads as "not stated".
    # It is genuinely not always known at span start: the media comprehension
    # learns which describer answered FROM the result (a voice note's title is
    # written by the text model, not the audio one), so it opens the span
    # without a model and calls `annotate_generation(model=...)` on the way out.
    if model:
        attrs["langfuse.observation.model.name"] = model
        attrs["gen_ai.request.model"] = model
    if root:
        attrs[TRACE_KIND] = "generation"
    if prompt:
        attrs["langfuse.observation.input"] = prompt
    if completion:
        attrs["langfuse.observation.output"] = completion
    attrs.update(attributes or {})
    with span(name, **attrs):
        yield


def annotate_generation(
    *,
    model: str = "",
    completion: str = "",
    finish_reason: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    attributes: dict[str, object] | None = None,
) -> None:
    """The half of a generation only known on the way OUT, in the same spelling.

    `span` takes its attributes at entry (it has to — the sampler reads them),
    and a completion does not exist yet at that point. This is `annotate` with
    the two names that matter pre-filled, so a call site does not re-derive
    them and cannot spell them a third way.

    Same screening contract as `generation`: `completion` is text a model wrote
    and reaches a third party over the network. Screen it first.

    **The token counts belong here rather than in a caller's return type.**
    Usage is the provider's fact and no caller's verdict depends on it; widening
    a port to carry a number only the collector reads would be the measurement
    changing the thing it measures. Strands emits `gen_ai.usage.*` itself, so
    only the raw `chat.completions.create` paths need this — and every one of
    them exported as a generation costing zero until 2026-09-08.
    """
    out: dict[str, object] = {}
    if model:
        out["langfuse.observation.model.name"] = model
        out["gen_ai.request.model"] = model
    if completion:
        out["langfuse.observation.output"] = completion
    if finish_reason:
        out["gen_ai.response.finish_reasons"] = finish_reason
    if input_tokens is not None and output_tokens is not None and total_tokens is not None:
        # FIVE NAMES FOR THREE NUMBERS, deliberately. Langfuse reads the
        # `input`/`output`/`total` triple; `prompt_tokens`/`completion_tokens`
        # are the names its older mapping uses, and a collector upgrading (or
        # downgrading) under us must not silently price every call at zero
        # again. Written only when ALL THREE arrived: a provider that omits
        # usage records NOTHING rather than zero, because writing 0 asserts the
        # call was free — the same argument `finish_reason=... or ""` makes one
        # field over.
        out["gen_ai.usage.input_tokens"] = input_tokens
        out["gen_ai.usage.output_tokens"] = output_tokens
        out["gen_ai.usage.total_tokens"] = total_tokens
        out["gen_ai.usage.prompt_tokens"] = input_tokens
        out["gen_ai.usage.completion_tokens"] = output_tokens
    out.update(attributes or {})
    annotate(**out)


def _safe_attributes(attributes: dict[str, object]) -> dict[str, str | bool | int | float]:
    """Coerce to what OTel accepts, PER KEY, and never raise.

    Per key rather than in one `try`, because the old shape set attributes in a
    loop inside a single guard: the first unstringable value abandoned every
    attribute after it, silently. A value that cannot be rendered now costs only
    itself. (`tests/test_span_skeleton.py` drives this with a `__str__` that
    raises.)
    """
    out: dict[str, str | bool | int | float] = {}
    for key, value in attributes.items():
        try:
            out[key] = value if isinstance(value, (str, bool, int, float)) else str(value)
        except Exception:  # a broken attribute must not break a turn
            log.debug("span attribute %s not recorded", key, exc_info=True)
    return out


def annotate(**attributes: object) -> None:
    """Add attributes to the span that is CURRENT right now, and never raise.

    `span` takes its attributes at entry, which is the wrong end for anything a
    stage only learns on the way out — an outcome, a stop reason, a latency. The
    alternative was to make `span` yield its span object, and that pushes the
    "a broken tracer must not break a turn" guard out to every call site; here
    it stays in one place, exactly as it is for the attributes `span` itself
    takes.

    **A no-op on a span that has already ended**, and that is a real state
    rather than a theoretical one: the tag path's root span is closed at the
    moment a QUEUED tag is enqueued, so a worker annotating "the root" would be
    writing into nothing. The OTel SDK logs «Setting attribute on ended span»
    and records neither the value nor an error (measured, 2026-08-28), which is
    exactly why `EngagementPipeline` annotates the per-turn span it opens
    itself and never the dispatch root.
    """
    try:
        sp = trace.get_current_span()
        for key, value in attributes.items():
            safe = value if isinstance(value, (str, bool, int, float)) else str(value)
            sp.set_attribute(key, safe)
    except Exception:  # a broken tracer must not break a turn
        log.debug("span annotation not recorded", exc_info=True)


def capture_context() -> Context:
    """The current trace context, to be re-`attached` on whatever task picks the
    work up later. Never raises; an empty `Context` is the degraded answer.

    **This exists because `asyncio.create_task` DOES copy contextvars, not
    because it doesn't.** OTel's context is a contextvar, so a task inherits
    whatever span was current at `create_task` time — measured, 2026-08-28. On
    the tag path that inheritance is actively wrong: a QUEUED tag is spawned by
    `ConversationSlots.release_and_dispatch`, which runs in the PREVIOUS turn's
    `finally`, inside that turn's own span. Left alone, the queued tag's spans
    would nest under the tag before it in the queue — one trace containing two
    unrelated conversations' turns, which is worse than the rootless spans this
    replaces because it reads as a causal relation that does not exist.

    So the context is captured where the tag was ADMITTED and attached where it
    RUNS, and `attached` replaces the ambient context wholesale rather than
    adding to it.
    """
    try:
        return otel_context.get_current()
    except Exception:
        log.debug("trace context could not be captured", exc_info=True)
        return Context()


@contextmanager
def attached(carrier: Context | None) -> Iterator[None]:
    """Run the body under `carrier` instead of whatever context it inherited.

    `None` — and a capture that degraded to an empty `Context` — attaches an
    EMPTY context, which makes the body's spans roots of their own trace. That
    is the fail-closed direction on purpose: a tag whose context was lost is a
    trace on its own, never a tag silently filed under the previous tag's turn.

    Never raises, on either half. `detach` is guarded separately from `attach`
    because this wraps `_run_admitted`, whose `finally` is what releases R-10's
    per-conversation lock — an exception escaping here would skip
    `release_and_dispatch` and wedge the conversation forever, with no error
    anywhere. The same rule `span` holds, for the same reason.
    """
    token = None
    try:
        token = otel_context.attach(carrier if carrier is not None else Context())
    except Exception:
        log.debug("trace context could not be attached; running as inherited",
                  exc_info=True)
    try:
        yield
    finally:
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:
                log.debug("trace context could not be detached", exc_info=True)


def _resolve_headers(headers_env: str) -> dict[str, str] | None:
    """`headers_env` names an environment variable (DEC-8: `*_env`, never the
    value inlined, no `${VAR}` interpolation); its content is OTel's own
    `OTEL_EXPORTER_OTLP_HEADERS` shape — comma-separated `key=value` pairs,
    URL-encoded. Empty name or empty/unset value both mean "send no auth
    header", not an error.
    """
    if not headers_env:
        return None
    raw = os.environ.get(headers_env, "")
    if not raw:
        return None
    return dict(parse_env_headers(raw))


def _build_exporter(exporter: str, endpoint: str, headers_env: str) -> _SpanExporter:
    if exporter == "console":
        return ConsoleSpanExporter()
    if exporter == "file":
        return ConsoleSpanExporter(out=open(endpoint, "a", encoding="utf-8"))
    if exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=endpoint, headers=_resolve_headers(headers_env))
    raise ValueError(f"unknown tracing exporter: {exporter!r}")


def trace_attributes(ctx, doctrine) -> dict:
    """Provenance only — never message content (see the dedicated test for this).

    `note_index_entries` is a COUNT, which is why it belongs in a dict that
    otherwise holds only hashes and ids: it says how many notes were in front
    of her on this turn without saying what any of them is. The titles are
    already in the GENERATION span's prompt for a turn that had any; this
    number is the one fact a turn with NONE cannot otherwise report, and a
    trace that cannot say «she was shown nothing» reads identically to one
    where the tier was never wired.

    It is the per-turn counterpart of `nora_note_index_entries`, and the two
    answer different questions on purpose: the gauge says what the notebook
    holds NOW, this says what THIS turn saw. They diverge exactly when a turn
    matters — a reply written against an index that has since changed.

    Stamped on every span in the turn (Strands applies these to the agent span
    and each tool/model child), so filtering Langfuse to «turns that saw an
    empty notebook» costs no new instrumentation at the call sites.
    """
    return {
        "tag_id": ctx.tag_id,
        "conversation_id": ctx.message.conversation_id,
        "doctrine_sha256": doctrine.sha256,
        "note_index_entries": len(ctx.notes),
    }
