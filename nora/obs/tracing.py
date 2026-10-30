"""OTel plumbing: the R-76 sampler, the generic named-span helper, and context
propagation — everything here is pure OpenTelemetry with no `strands` import,
which is the point (`tests/test_boundaries.py::test_strands_import_boundary`
allows `strands` only under `nora/agent/`).

Provider/exporter setup lives in `nora.agent.telemetry.setup_agent_tracing`,
called from `nora/__main__.py` (the composition root) with `build_sampler()`
passed in as a plain value; see the comment above `span` for why this module
cannot make that call itself.

Raw-model-call spans are `nora.agent.telemetry.start_raw_model_span`/
`end_raw_model_span`, thin wrappers around Strands' own
`Tracer.start_model_invoke_span`/`end_model_invoke_span`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, TypeVar, get_args

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
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

from nora.secret import resolve

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
#: * `queue` — a bounded platform-bridge queue operation (enqueue, claim,
#:   attempt, deliver, ACK). The one operator-requested exception to the
#:   deterministic-root rule (design/platform-bridges/04): each root is a
#:   short single-operation span linked to its producer/previous attempt,
#:   never held open over a queue wait or a deploy. Kept at 100% at this
#:   installation's volume; the collector routes it to Jaeger and away from
#:   Langfuse, so it must never carry a model call or message content.
TraceKind = Literal["generation", "plumbing", "queue"]

#: The attribute name itself, `nora.` prefixed like every other attribute this
#: module's callers set.
TRACE_KIND = "nora.trace_kind"

_TRACE_KINDS: frozenset[str] = frozenset(get_args(TraceKind))

#: What `capture_context` hands back and `attached` takes. Aliased so a
#: pipeline that carries one across a queue names THIS module rather than
#: `opentelemetry`: a stage should not import a tracing vendor to hold an
#: opaque handle it never inspects.
TraceCarrier = Context


class _PlumbingIsNotATrace(Sampler):
    """THE ROOT DECISION (R-76): a trace reaches the collector when the work
    under it can contain a generation.

    Langfuse is an LLM-observability tool. Everything Nora does that is not a
    model call — sequencing ingest stages, writing a row, resolving an
    identity, counting a refusal — is plumbing, and plumbing's questions
    («how many», «how often», «with what outcome») are Prometheus' questions.
    `nora/ingest/pipeline.py` opens spans with no model call under any of
    them, and it is the highest-volume entry point, so exporting it would bury
    the turns that matter.

    **This is a decision about ROOTS, not about every span.**
    `engagement.admission` has no generation in it either, and it must keep
    being exported: as a CHILD of a trace that has one, it is how a turn that
    ended before the model call ends visibly at a named stage. Composed under
    `ParentBased`, so a child never re-decides — it inherits whatever its root
    was given, which also keeps a QUEUED tag's turn (re-attached to a captured
    context whose root span has already ended) in the same trace as the root
    that admitted it.

    **DEFAULT: EXPORT.** An undeclared root is kept. Erring towards export
    costs visible noise; erring towards silence costs an invisible missing
    trace for a model call. Only an explicit `plumbing` drops. An undeclared
    value is LOGGED and kept.
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

    `tests/conftest.py`'s `exported_spans` fixture uses this; without it a test
    provider would export spans production drops, and assertions about «what
    reaches the collector» would measure a provider no deployment has.
    """
    return ParentBased(root=_PlumbingIsNotATrace())


#: Provider setup is not here: `nora.agent` sits ABOVE `nora.obs` in the
#: layered-import contract (`pyproject.toml`, "layered architecture"), so this
#: module importing from `nora.agent`, even lazily inside a function, would be
#: the violation `uv run lint-imports` refuses. `nora/__main__.py` (the
#: composition root, outside the layer graph) calls
#: `nora.agent.telemetry.setup_agent_tracing` and passes it `build_sampler()`
#: as a plain value.


@contextmanager
def span(name: str, **attributes: object) -> Iterator[None]:
    """One named span, and **never load-bearing** — the same rule
    `nora.agent.telemetry.setup_agent_tracing` keeps at provider setup.

    Spans opened here (one per turn, children named for the stages) make a turn
    that fails BEFORE the model call end **visibly, at a named stage** instead
    of producing no trace.

    **It catches `BaseException`, records, and RE-RAISES.** Three properties, and
    all three are load-bearing:

    - `BaseException`, not `Exception`, because **cancellation is not an
      `Exception`** — a wrapper that only catches `Exception` leaves the cancel
      path unrecorded. A cancelled turn is exactly the turn worth seeing.
    - It **re-raises, always**. An observation that changes control flow is not an
      observation. A span must never turn a failing turn into a passing one, and
      must never swallow a cancel.
    - The *recording itself* is wrapped, because a broken tracer must not break a
      turn. If setting an attribute throws, the turn continues untraced.

    A no-op when tracing is off: `get_tracer` returns a non-recording tracer, so
    this costs a context manager and nothing else.

    **Attributes are passed AT START, not set afterwards**, and that is what
    makes `build_sampler`'s rule reachable at all: a `Sampler` sees only what
    the span was started with, so a `nora.trace_kind` set later would arrive
    after the sampling decision. They are coerced
    to safe scalars FIRST (`_safe_attributes`, which drops a value it cannot
    render rather than raising) so that a broken attribute still costs only
    that attribute — never the span, and never the body.
    """
    with _opened_span(name, links=(), parent=None, attributes=attributes):
        yield


@contextmanager
def root_span(name: str, *, links: Sequence[Link] = (),
              parent: Context | None = None,
              **attributes: object) -> Iterator[None]:
    """A fresh trace root — the same guards as `span`, with the parent and
    links a root needs spelled out because ambient context is WRONG here.

    The queue/generation seam is why this exists: a `queue.attempt` or the
    `engagement` generation root must NOT inherit whichever span happens to be
    current in the consumer's task (a claim span parenting a model trace is
    exactly the nesting design 04 forbids). `parent=None` means an EMPTY
    context — a true root — not ambient; a caller that wants ambient context
    uses `span`. `links` are the W3C `Link`s stamped at start, the only
    relationship a fresh root can hold to a context that is not its parent.
    """
    with _opened_span(name, links=links,
                      parent=parent if parent is not None else Context(),
                      attributes=attributes):
        yield


@contextmanager
def _opened_span(name: str, *, links: Sequence[Link],
                 parent: Context | None,
                 attributes: dict[str, object]) -> Iterator[None]:
    try:
        cm = trace.get_tracer("nora").start_as_current_span(
            name, context=parent, links=list(links),
            attributes=_safe_attributes(attributes),
        )
        sp = cm.__enter__()
    except Exception:
        # ACQUISITION is guarded too, not just recording: a span can be opened
        # inside a `finally` after the tag pipeline's outer
        # `except BaseException` has run, where anything raised escapes `run()`
        # and takes out the whole tag rather than just the trace. The untraced
        # body still runs.
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


def _safe_attributes(attributes: dict[str, object]) -> dict[str, str | bool | int | float]:
    """Coerce to what OTel accepts, PER KEY, and never raise.

    Per key rather than in one `try`, so a value that cannot be rendered costs
    only itself, not the attributes after it. (`tests/test_span_skeleton.py`
    drives this with a `__str__` that raises.)
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

    `span` takes its attributes at entry; this is for anything a stage only
    learns on the way out — an outcome, a stop reason, a latency. It keeps the
    "a broken tracer must not break a turn" guard in one place instead of at
    every call site.

    **A no-op on a span that has already ended**: the tag path's root span is
    closed when a QUEUED tag is enqueued, so a worker annotating "the root"
    would write into nothing. The OTel SDK logs «Setting attribute on ended
    span» and records neither the value nor an error, which is why
    `EngagementPipeline` annotates the per-turn span it opens itself and never
    the dispatch root.
    """
    try:
        sp = trace.get_current_span()
        for key, value in attributes.items():
            safe = value if isinstance(value, (str, bool, int, float)) else str(value)
            sp.set_attribute(key, safe)
    except Exception:  # a broken tracer must not break a turn
        log.debug("span annotation not recorded", exc_info=True)


def current_span_ids() -> tuple[str, str]:
    """The CURRENT span's `(trace_id, span_id)` as 32 and 16 hex characters,
    or `("", "")` when there is no recording span. Never raises.

    For an audit row that links to its trace as a debug view (the
    `computer_events` table): the row is the record, so an absent trace is an
    empty link, never a guessed one."""
    try:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return "", ""
        return format(context.trace_id, "032x"), format(context.span_id, "016x")
    except Exception:  # a broken tracer must not break a turn
        log.debug("span ids not read", exc_info=True)
        return "", ""


def current_trace_id() -> str | None:
    """The CURRENT span's trace id as 32 hex characters, or `None` when there
    is no recording span (tracing off, or called outside one). Never raises.

    For a row that must point at the trace that produced it (the summons
    ledger's `trace_id`, `nora.app.summons`): `None` says there is none rather
    than storing the all-zero id of a non-recording span."""
    try:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return format(context.trace_id, "032x")
    except Exception:  # a broken tracer must not break a turn
        log.debug("trace id not read", exc_info=True)
        return None


#: `traced_read`'s own generic parameter — whatever the wrapped read returns,
#: unchanged, is what it hands back.
T = TypeVar("T")


def _row_count(value: object) -> int | None:
    """`len(value)` when that means something as a row count, else `None` —
    never a guessed number.

    A store READ answers either a sized collection (`list[TranscriptRow]`,
    `tuple[Note, ...]`, `[]`) — where "how many rows" is `len` — or a scalar
    (`Standing | None`, an `int` watermark, a `bool`) — where a row count
    would be fabricated. `None` means the attribute is left off the span
    (`traced_read` only sets it when this returns a number).
    """
    if value is None:
        return 0
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


async def traced_read(
    store: str, method: str, coro: Awaitable[T],
    *, count: Callable[[T], int | None] | None = None,
) -> T:
    """Await `coro` — ONE store read — under `store.<store>.<method>`, with
    `nora.store.duration_ms` and (where the result is sized)
    `nora.store.row_count` recorded on success.

    `count`, when given, replaces `_row_count` as how the row count is
    derived from the result, for a result `_row_count` cannot read.
    `MemoryStore.representation` answers a `Recollection`, not a sized
    collection, so a caller passes `count=lambda r: len(r.conclusions)`.

    This is the one timing-and-naming wrapper for store-port reads on a turn's
    critical path (`nora.engagement.context.TranscriptInjection` and
    `nora.app.runtime`'s Dreamer cursor reads), so an unbounded or slow read is
    visible.

    **The failure path is `span`'s, not this function's.** `coro` raising
    propagates out of the `with span(...)` block before `annotate` runs, so
    `span`'s `BaseException` handler (record, `ERROR` status, re-raise) is what
    a failing read produces; this wrapper adds no `except` of its own.

    Attributes go through `annotate`, not `span(..., **attrs)`, because both
    are known only on the way OUT. `span`'s attributes are read by the R-76
    sampler at START, and `nora.trace_kind` must never be one of these:
    nothing here decides whether the read's trace is kept.
    """
    started = time.perf_counter()
    with span(f"store.{store}.{method}"):
        result = await coro
        attrs: dict[str, object] = {
            "nora.store.duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        rows = count(result) if count is not None else _row_count(result)
        if rows is not None:
            attrs["nora.store.row_count"] = rows
        annotate(**attrs)
        return result


def capture_context() -> Context:
    """The current trace context, to be re-`attached` on whatever task picks the
    work up later. Never raises; an empty `Context` is the degraded answer.

    **`asyncio.create_task` copies contextvars.** OTel's context is a
    contextvar, so a task inherits whatever span was current at `create_task`
    time. On the tag path that inheritance is wrong: a QUEUED tag is spawned by
    `ConversationSlots.release_and_dispatch`, which runs in the PREVIOUS turn's
    `finally`, inside that turn's own span. Left alone, the queued tag's spans
    would nest under the tag before it in the queue, reading as a causal
    relation that does not exist.

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


def resolve_otlp_headers(
    headers_env: str, credentials: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """`headers_env` names an environment variable (DEC-8: `*_env`, never the
    value inlined, no `${VAR}` interpolation); its content is OTel's own
    `OTEL_EXPORTER_OTLP_HEADERS` shape — comma-separated `key=value` pairs,
    URL-encoded. Empty name or empty/unset value both mean "send no auth
    header", not an error.

    Public (not `_resolve_headers`) because `nora.agent.telemetry` — the only
    module allowed to build the actual `OTLPSpanExporter` — needs this exact
    logic, and `nora.agent` importing this pure, strands-free function from
    `nora.obs` is the direction the layered contract allows.
    """
    if not headers_env:
        return None
    values = resolve(Path("secret.toml")) if credentials is None else credentials
    raw = values.get(headers_env, "")
    if not raw:
        return None
    return dict(parse_env_headers(raw))


def trace_attributes(ctx, doctrine) -> dict:
    """Provenance only — never message content (see the dedicated test for this).

    `note_index_entries` is a COUNT: how many notes were in front of her on
    this turn, without saying what any of them is. It is the one fact a turn
    with NO notes cannot otherwise report in its trace. It is the per-turn
    counterpart of `nora_note_index_entries`: the gauge says what the notebook
    holds NOW, this says what THIS turn saw.

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
