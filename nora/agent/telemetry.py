"""strands.telemetry, wrapped — the only other module besides
`nora.agent.structured` that may import `strands`
(`tests/test_boundaries.py::test_strands_import_boundary` allows the whole
`nora/agent/` package, nothing narrower).

**What lives here versus in `nora.obs.tracing`.** The R-76 sampler
(`_PlumbingIsNotATrace`/`build_sampler`), the plumbing-span helper (`span`),
context propagation (`capture_context`/`attached`), and per-turn attribute
building (`trace_attributes`) are pure OpenTelemetry with no Strands
dependency, and stay in `nora.obs.tracing` — most of the codebase calls them
and none of it may import `strands`. This module owns only the two things
that genuinely need `strands.telemetry` itself: provider/exporter setup
(`setup_agent_tracing`) and the span pair a RAW `chat.completions.create` call
needs to open/close in the SDK's own vocabulary
(`start_raw_model_span`/`end_raw_model_span`) — `nora.app.composition`'s
`_RelevanceGateModelClient` imports these thin wrappers rather than reaching
`strands.telemetry.tracer.get_tracer()` itself, the same pattern
`ask_text`/`ask_structured` already use for a full Agent invocation.

**The vision describer does NOT use this pair, and cannot.**
`nora.infra.vision.client`'s raw call has no model-invoke span of its own at
all — `nora.infra` sits below both `nora.obs` and `nora.agent` in the
layered contract, so it can reach neither this module nor `nora.obs.tracing`
to open one. `nora.ingest.comprehension` (which CAN reach this module, being
above `nora.agent`) still does not wrap the call in a `start_raw_model_span`
either — the describer's model name only reaches the trace after the fact,
via a plain `annotate()` on the outer `comprehend` span, using the model
name `MediaDescription` already carries back on its return value. This is a
known gap (a vision read exports zero token usage); widening `MediaDescription`
to also carry usage so a caller could annotate it is the deferred port-shape
question `tests/architecture/test_raw_model_calls_carry_their_own_machinery.py`
records next to the vision client's exemption from its own `usage`
requirement.

Spans are built on Strands' own `Tracer.start_model_invoke_span` /
`end_model_invoke_span`, which already implement the OTel GenAI semantic
conventions, the content-redaction lever, and Langfuse-observation-type wiring:
upstream-maintained rather than a bespoke dialect this repo keeps in sync.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.trace import Span
from strands.telemetry import StrandsTelemetry
from strands.telemetry.tracer import get_tracer
from strands.types.content import Message, Messages
from strands.types.event_loop import Metrics, StopReason, Usage

log = logging.getLogger("nora.agent.telemetry")

#: OpenAI's `finish_reason` vocabulary, translated to Strands' `StopReason`
#: (a CLOSED `Literal`, unlike OpenAI's plain `str`) so `end_raw_model_span`
#: can hand the SDK's own tracer a value it actually declares. Unmapped values
#: fall through to `"end_turn"` — the same "getting it wrong towards keeping
#: the trace legible costs less than getting it wrong towards dropping it"
#: judgement `nora.obs.tracing`'s sampler makes, one field over: a stop reason
#: is metadata on an already-open span, never a value a caller's control flow
#: depends on.
_STOP_REASON_FROM_FINISH_REASON: dict[str, StopReason] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filtered",
    "tool_calls": "end_turn",
    "function_call": "end_turn",
}


#: Langfuse drops a session id longer than this, or with a non-ASCII character,
#: silently (langfuse.com/docs/observability/features/sessions).
_SESSION_ID_MAX = 200

#: The attribute a span carries its room under, WHEN IT IS STARTED WITH IT:
#: the roots and stages that pass it to `nora.obs.tracing.span`. Strands spans
#: never start with it: Strands opens a span and sets `trace_attributes` after,
#: past `on_start`, so they take their session from the parent instead (see the
#: installed `strands/telemetry/tracer.py`).
#: A Dreamer unit has no room; its session is the tick that dispatched it
#: (`nora.app.runtime.dream_loop`), so one pass's units read as one session.
_CONVERSATION_KEYS = ("nora.conversation_id", "nora.dreamer.pass_id")

#: First match wins. Langfuse's recognised `langfuse.observation.type` values;
#: a span not in the table is left to Langfuse, which types the model calls,
#: agents and tools from their GenAI attributes. What goes here is the plumbing
#: UNDER a model call that R-76 keeps on purpose, so an operator can filter a
#: trace to the LLM work without reading span names.
_OBSERVATION_TYPES: tuple[tuple[str, str], ...] = (
    ("engagement.admission", "guardrail"),
    ("store.", "retriever"),
    ("engagement.", "chain"),
    ("agent.prefill", "chain"),
    ("execute_event_loop_cycle", "chain"),
)


def _session_id_for(span: Any, parent_context: Any) -> str | None:
    """The room a span belongs to: its own start attribute, else its parent's stamp.

    A session id is valid when Langfuse will keep it. One it would drop is not
    stamped, so the trace's absence from a session is in the data, not a
    silent loss downstream."""
    attrs = getattr(span, "attributes", None) or {}
    for key in _CONVERSATION_KEYS:
        value = attrs.get(key)
        if value is not None:
            return _valid_session_id(str(value))
    parent = trace.get_current_span(parent_context)
    parent_attrs = getattr(parent, "attributes", None) or {}
    inherited = parent_attrs.get("session.id")
    return _valid_session_id(str(inherited)) if inherited is not None else None


def _valid_session_id(value: str) -> str | None:
    if not value or len(value) > _SESSION_ID_MAX or not value.isascii():
        return None
    return value


class TraceIdentityProcessor(SpanProcessor):
    """Stamp every span with its conversation and its observation type.

    **One seam, not a stamp at each call site.** Engagement roots carry
    `nora.conversation_id`, but Langfuse groups sessions only by `session.id`.
    OTel GenAI names the same thing `gen_ai.conversation.id` ("session, thread"); Langfuse reads
    `session.id`, and asks for it on each span rather than only the root. A
    child inherits from its parent at start, so the Strands spans and the
    `store.*` reads, which never carry the id themselves, end up in the same
    session.

    **Never load-bearing**, the rule `nora.obs.tracing.span` and provider setup
    hold: a failure here costs the stamp, never the span or the turn.
    """

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        try:
            session = _session_id_for(span, parent_context)
            if session is not None:
                span.set_attribute("session.id", session)
                span.set_attribute("gen_ai.conversation.id", session)
            attrs = getattr(span, "attributes", None) or {}
            if "langfuse.observation.type" not in attrs:
                name = getattr(span, "name", "") or ""
                for prefix, kind in _OBSERVATION_TYPES:
                    if name.startswith(prefix):
                        span.set_attribute("langfuse.observation.type", kind)
                        break
        except Exception:
            log.debug("trace identity not stamped", exc_info=True)


def setup_agent_tracing(
    *, sampler: Sampler, service_name: str, exporter: str, endpoint: str,
    headers_env: str,
) -> None:
    """Register the global `TracerProvider` through `StrandsTelemetry`,
    keeping Nora's own sampler.

    `StrandsTelemetry.__init__` takes an optional pre-built
    `tracer_provider` — the seam that lets Nora's R-76 sampler keep deciding
    what reaches the collector while Strands' own exporter-setup methods do
    the rest. Passing NO provider would build one with no sampler override
    (OTel's default, `ParentBased(root=ALWAYS_ON)`), which would export every
    ingest-plumbing span R-76 exists to drop.

    ## `set_tracer_provider` IS OURS TO CALL

    Strands does NOT register a supplied provider. From the installed source:

        if tracer_provider:
            self.tracer_provider = tracer_provider   # kept, never registered
        else:
            self._initialize_tracer()                # THIS sets the global

    Its own docstring states the same thing from the other side — "If None, a
    new one will be created *and set as global*" — so the registration is the
    thing you give up by supplying a provider, which is exactly what Nora does
    to keep its sampler.

    Without the explicit `set_tracer_provider` below, the process global stays
    `ProxyTracerProvider`, whose tracers are NO-OPS. Every `nora.obs.tracing.
    span(...)` in the codebase then records nothing and exports nothing —
    **and raises nothing, logs nothing and fails no test**, because a no-op
    tracer is a perfectly well-behaved tracer. The collector can be
    healthy and the try/except around this call silent while nothing is
    exported.
    """
    if exporter not in ("console", "file", "otlp_http"):
        raise ValueError(f"unknown tracing exporter: {exporter!r}")
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=sampler,
    )
    # First, so the stamp is on the span before any processor can read it; and
    # on the provider itself, so it reaches the Strands spans and Nora's own
    # alike (tests/unit/test_trace_identity.py reads it through the registered one).
    provider.add_span_processor(TraceIdentityProcessor())
    telemetry = StrandsTelemetry(tracer_provider=provider)
    if exporter == "console":
        telemetry.setup_console_exporter()
    elif exporter == "file":
        # The handle must outlive this function — the exporter writes to it
        # for the process's life — so `with open(...)` (which would close it
        # here) is the wrong shape, not the one SIM115 is asking for.
        telemetry.setup_console_exporter(
            out=open(endpoint, "a", encoding="utf-8"),  # noqa: SIM115
        )
    else:
        # `nora.obs.tracing.resolve_otlp_headers` is pure OTel/stdlib (DEC-8's
        # env-var-name-not-value parsing) — importing it here is `nora.agent`
        # reaching DOWN into `nora.obs`, the direction the layered contract
        # allows (see `nora/obs/tracing.py`'s module docstring).
        from ..obs.tracing import resolve_otlp_headers

        telemetry.setup_otlp_exporter(
            endpoint=endpoint, headers=resolve_otlp_headers(headers_env),
        )

    # LAST, and unconditional: after whichever exporter was attached above, so
    # the provider that becomes global is one that can actually ship a span.
    # See the docstring — supplying a provider is precisely what stops Strands
    # doing this, and nothing anywhere complains when it is missing.
    trace.set_tracer_provider(provider)


def start_raw_model_span(
    *, messages: Messages, model_id: str,
    system_prompt: str | None = None,
    custom_trace_attributes: Mapping[str, Any] | None = None,
) -> Span | None:
    """Open the `chat` span for a raw `chat.completions.create` call, and
    **never load-bearing** — the same rule `nora.obs.tracing.span`/`annotate`
    hold, restated here because this module's raw calls are on the relevance
    gate's hot path: a broken tracer must not turn into a suppressed
    admission decision.

    Currently used by exactly one caller —
    `nora.app.composition._RelevanceGateModelClient`, which stays off the
    Agent SDK because its verdict must not go through `ask_structured`
    (see `nora.agent.structured`'s own docstring for the reason).
    `nora.infra.vision.client` is also a raw call but does NOT use this —
    this module's own docstring explains why (`nora.infra` cannot reach it).

    Returns `None` on any failure — including one inside `Tracer.
    start_model_invoke_span` itself, which has no exception guard of its
    own — rather than letting the exception reach the caller's bounded model
    call. `end_raw_model_span`/`end_raw_model_span_with_error` both treat a
    `None` span as "nothing to close" and no-op.
    """
    try:
        return get_tracer().start_model_invoke_span(
            messages=messages, model_id=model_id, system_prompt=system_prompt,
            custom_trace_attributes=custom_trace_attributes,
        )
    except Exception:
        log.debug("raw model span could not start; continuing untraced", exc_info=True)
        return None


def end_raw_model_span(
    *, span: Span | None, content: str, finish_reason: str,
    input_tokens: int | None = None, output_tokens: int | None = None,
    total_tokens: int | None = None, latency_seconds: float | None = None,
) -> None:
    """Close a span opened by `start_raw_model_span`, in the SDK's own
    `Usage`/`Metrics`/`StopReason` vocabulary. **Never load-bearing** — see
    `start_raw_model_span`'s own docstring: a broken tracer, or one that
    never opened (a `None` span, from a guarded `start_raw_model_span`
    failure), must not turn an already-obtained model verdict into a
    discarded one.

    **Usage is written as zero when the provider omitted it.**
    `Tracer.end_model_invoke_span` writes `gen_ai.usage.*` unconditionally
    from the `Usage` dict it is given — there is no "omit if absent" branch.
    OpenAI-compatible providers return usage on essentially every response, so
    the omitted case is rare.
    """
    if span is None:
        return
    try:
        stop_reason = _STOP_REASON_FROM_FINISH_REASON.get(finish_reason, "end_turn")
        message: Message = {"role": "assistant", "content": [{"text": content}]}
        usage: Usage = {
            "inputTokens": input_tokens or 0,
            "outputTokens": output_tokens or 0,
            "totalTokens": total_tokens or 0,
        }
        metrics: Metrics = {
            "latencyMs": int((latency_seconds or 0.0) * 1000),
        }
        get_tracer().end_model_invoke_span(
            span=span, message=message, usage=usage, metrics=metrics,
            stop_reason=stop_reason,
        )
    except Exception:
        log.debug("raw model span could not end cleanly", exc_info=True)


def end_raw_model_span_with_error(
    *, span: Span | None, error_message: str, exception: Exception | None = None,
) -> None:
    """The failure half of `start_raw_model_span` — a raw call that raised
    before it returned anything to shape into a `Message`. **Never
    load-bearing**, same as `end_raw_model_span`: the ORIGINAL exception is
    always re-raised by the caller regardless of what happens here, so a
    failure recording the failure must never replace or swallow it."""
    if span is None:
        return
    try:
        get_tracer().end_span_with_error(span, error_message, exception)
    except Exception:
        log.debug("raw model span error not recorded", exc_info=True)


@contextmanager
def timed() -> Iterator[Callable[[], float]]:
    """`with timed() as elapsed: ...` — `elapsed()` returns seconds since
    entry, for `end_raw_model_span`'s `latency_seconds`. A function rather
    than a plain float because the caller reads it AFTER the body runs, and a
    closure is the least ceremony that allows that without a second variable
    at every call site."""
    start = time.monotonic()
    yield lambda: time.monotonic() - start
