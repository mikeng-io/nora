"""Bounded queue lifecycle telemetry for the platform-bridge handoff
(design/platform-bridges/04 §operations).

Three surfaces, all bounded and none of them load-bearing:

- **Spans.** The `queue` root kind (`nora.obs.tracing.TraceKind`) admitted at
  creation: one short `queue.enqueue` PRODUCER span at capture, one fresh
  empty-parent `queue.attempt` CONSUMER root per work attempt, and bounded
  `queue.claim`/`queue.deliver`/`queue.ack` operation roots. Parents are never
  propagated — every span is its own trace, related only by validated W3C
  links (the producer's `traceparent` from the envelope, the previous
  attempt's record from the durable `bridge_work` receipt). A span covers one
  active phase only: never the queue wait, never a deploy. The model turn
  stays a SEPARATE generation trace that links back; nothing here makes it a
  queue child.
- **JSON lifecycle lines** through the existing stdlib/structlog pipeline, a
  closed event vocabulary and a hard field allowlist — no bodies, raw
  platform/conversation/sender/account ids, secrets or SDK exception text.
- **The durable attempt record** on the `bridge_work` receipt: attempt
  identity is `(work.id, generation)` — the existing claim fence, not a
  parallel ledger and not a contextvar. It is persisted BEFORE the work runs
  so a reclaim can link to an attempt that never finished, and it never
  prevents the work: a failed write returns `unavailable` (see
  `AttemptWriteResult` — never a reason to cancel the attempt).

Helpers never raise on instrumentation failure (same rule as
`nora.obs.tracing.span`). They DO raise on vocabulary misuse — a bad event
name or outcome is a code defect, loud at authoring time, matching
`SqlBridgeStore`'s own closed-state `ValueError`s.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal, get_args

import sqlalchemy as sa
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, SpanContext, SpanKind, Status, StatusCode
from opentelemetry.trace.span import TraceState

from nora.obs.tracing import TRACE_KIND

log = logging.getLogger("nora.platform.bridge.telemetry")

_TRACER = "nora.bridge"

# ---------------------------------------------------------------------------
# Closed vocabularies — derived once by `get_args`, the same rule every label
# vocabulary in this repo follows (the help text in `nora.obs.metric_specs`
# names these Literals rather than restating their contents).
# ---------------------------------------------------------------------------

#: Every per-event JSON lifecycle line carries one of these `event` names
#: (design 04 «Per-event structured lifecycle logs»). The same vocabulary is
#: the `event` label of `nora_bridge_queue_events_total`.
LifecycleEvent = Literal[
    "captured", "enqueued", "duplicate_suppressed", "claimed", "reclaimed",
    "attempt_started", "deferred", "attempt_failed", "delivery_started",
    "delivery_confirmed", "delivery_uncertain", "completed",
    "intentional_silence", "failed_retained", "acked", "ack_failed",
]
LIFECYCLE_EVENTS: frozenset[str] = frozenset(get_args(LifecycleEvent))

#: Terminal/non-terminal outcomes a work attempt can land on — the
#: `bridge_work.state` vocabulary minus `pending`/`running` (never a RESULT).
WorkOutcome = Literal[
    "done", "intentional_skip", "retryable_failure", "failed_retained",
    "uncertain", "cancelled",
]
WORK_OUTCOMES: frozenset[str] = frozenset(get_args(WorkOutcome))

#: `bridge_action` outcomes — the durable result boundary, never HTTP
#: acceptance (design 02-wire §actions).
ActionOutcome = Literal["confirmed", "rejected", "unsupported", "uncertain"]
ACTION_OUTCOMES: frozenset[str] = frozenset(get_args(ActionOutcome))

#: The `bridge_action.op` vocabulary (wire.py §actions:
#: `"send" | "react" | "edit" | "typing"`) — the real `op` label tuples
#: `prime` seeds `nora_bridge_actions_total` with. Kept beside the outcome
#: vocabulary so the counter's full label space lives in one place.
ACTION_OPS: frozenset[str] = frozenset({"send", "react", "edit", "typing"})

#: The R-27 three states — the ONLY outcome authority. Degradation is
#: metadata, not a fourth result: `QueueInspection` carries `partial` /
#: `coherent` / `truncated` / `db_status` flags and per-section `error`s
#: beside this status. An aggregate that cannot be measured fully is
#: `unavailable`, whatever measured stays visible in the sections.
InspectionResult = Literal["ok", "nothing_found", "unavailable"]
INSPECTION_RESULTS: frozenset[str] = frozenset(get_args(InspectionResult))

#: `stage` on `nora_bridge_stage_seconds`: the `bridge_work.kind` vocabulary
#: plus the bounded queue operations that are not work attempts.
QueueStage = Literal[
    "ingest", "stimulus", "memory_feed", "turn", "participation",
    "receipt", "trace_debrief", "deliver", "claim", "ack", "custody",
]
QUEUE_STAGES: frozenset[str] = frozenset(get_args(QueueStage))

#: The bounded operation-span names (`queue.claim`, `queue.deliver`,
#: `queue.ack`, `queue.custody` — the custody-transfer boundary).
#: `queue.enqueue` and `queue.attempt` have their own helpers.
QueueOp = Literal["claim", "deliver", "ack", "custody"]
QUEUE_OPS: frozenset[str] = frozenset(get_args(QueueOp))

#: `record_attempt_started`'s answer — the two non-successes are DIFFERENT
#: failures and the caller must not conflate them:
#:
#: * `fenced` — the generation fence rejected the write: the row moved to a
#:   newer attempt (the same fence the claim path uses). The caller's own
#:   claim logic decides what a stale fence means for the work; it is NOT a
#:   telemetry failure.
#: * `unavailable` — the write itself failed: telemetry is lost and the
#:   work proceeds untraced. Never cancel an attempt on this alone.
AttemptWriteResult = Literal["written", "fenced", "unavailable"]

# ---------------------------------------------------------------------------
# The lifecycle-log field allowlist. Anything not named here is DROPPED before
# the line is written — this is the wall that keeps bodies, raw
# platform/conversation/sender/account ids, secrets and SDK exception text out
# of the JSON stream (design 04 «Safe fields»). Event identity is the opaque
# `event_id`; source identity is hashed upstream and NEVER appears.
# ---------------------------------------------------------------------------

_SAFE_LOG_FIELDS: frozenset[str] = frozenset({
    "event_id", "stream", "stream_id", "attempt_id", "action_id", "tag_id",
    "owner_generation", "platform", "kind", "phase", "reason", "outcome",
    "state", "sent_ms", "received_ms", "queued_ms", "enqueued_ms",
    "started_ms", "finished_ms", "queue_wait_ms", "duration_ms", "idle_ms",
    "trace_id", "span_id", "producer_trace_id", "producer_span_id",
    "prev_trace_id", "prev_span_id", "attempts", "ordinal", "consumer",
})

#: Failure-shaped events log a WARNING; the rest log INFO. Severity is an
#: event property, not a per-callsite editorial choice.
_EVENT_LEVELS: dict[str, int] = {
    "attempt_failed": logging.WARNING,
    "delivery_uncertain": logging.WARNING,
    "failed_retained": logging.WARNING,
    "ack_failed": logging.WARNING,
}


# ---------------------------------------------------------------------------
# W3C link contexts
# ---------------------------------------------------------------------------

def remote_context(traceparent: str | None,
                   tracestate: str | None = None) -> SpanContext | None:
    """A W3C `traceparent` → a remote `SpanContext`, or `None`.

    `None` covers every degraded input — missing, malformed, all-zero ids —
    and the caller links nothing rather than guessing. Event identity and
    trace identity are different things (design 02): a missing context changes
    diagnostics only, never the work.
    """
    if not traceparent or not isinstance(traceparent, str):
        return None
    parts = traceparent.split("-")
    if len(parts) != 4:
        return None
    version, tid, sid, flags = parts
    try:
        if version == "ff" or len(tid) != 32 or len(sid) != 16:
            return None
        trace_id, span_id = int(tid, 16), int(sid, 16)
        trace_flags = trace.TraceFlags(int(flags, 16))
    except ValueError:
        return None
    if trace_id == 0 or span_id == 0:
        return None
    # from_header takes a LIST of header values, one per `tracestate`
    # response header — a bare string iterates to single characters. A
    # member that fails the W3C pattern returns an EMPTY state, not a raise.
    state = (TraceState.from_header([tracestate])
             if tracestate else TraceState.get_default())
    return SpanContext(trace_id=trace_id, span_id=span_id, is_remote=True,
                       trace_flags=trace_flags, trace_state=state)


def traceparent_of(ctx: SpanContext) -> str:
    """The W3C carrier string for a live span context."""
    return (f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-"
            f"{int(ctx.trace_flags):02x}")


def span_ref(span: Span | None) -> dict[str, str]:
    """`{"trace_id","span_id","traceparent","tracestate"}` for a live span,
    `{}` for none.

    The record a caller persists on the work receipt and stamps on the
    envelope's `trace` dict — the durable half of every later link.
    """
    try:
        if span is None:
            return {}
        ctx = span.get_span_context()
        if not ctx.is_valid:
            return {}
        return {"trace_id": f"{ctx.trace_id:032x}",
                "span_id": f"{ctx.span_id:016x}",
                "traceparent": traceparent_of(ctx),
                "tracestate": ctx.trace_state.to_header() or ""}
    except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
        # the TYPE name only — an exception's message/stack can carry SDK
        # internals (urls, ids, secrets) and this module logs neither
        log.debug("span ids not read (%s)", type(exc).__name__)
        return {}


def _links(*ctxs: SpanContext | None) -> list[Link]:
    """Validated remote contexts → OTel links. `None` inputs are absent
    links — never a fabricated context, never a parent."""
    return [Link(c) for c in ctxs if c is not None]


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------

def _start(name: str, *, kind: SpanKind, links: list[Link],
           attributes: dict[str, object]) -> Span | None:
    """Acquire one span under an explicit EMPTY parent (a fresh trace root),
    `nora.trace_kind=queue` set at START where the sampler reads it. `None`
    when the tracer itself is broken — callers run untraced, never failed.
    """
    try:
        return trace.get_tracer(_TRACER).start_span(
            name, context=Context(), links=links, kind=kind,
            attributes=_coerce_attrs({TRACE_KIND: "queue", **attributes}))
    except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
        log.debug("queue span %s could not start; running untraced (%s)",
                  name, type(exc).__name__)
        return None


def _coerce_attrs(attributes: dict[str, object]) -> dict[str, str | bool | int | float]:
    """Safe scalars ONLY — a non-primitive is DROPPED, never `str()`-ed.

    `str(value)` on an exception (or any object whose `__str__` renders a
    message) would carry SDK internals — urls, ids, secrets — through an
    allowlisted field NAME. The allowlist vets names; this vets values.
    """
    out: dict[str, str | bool | int | float] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            out[key] = value
        else:
            log.debug("queue span attribute %s not recorded "
                      "(non-primitive %s)", key, type(value).__name__)
    return out


def _bridge_attrs(fields: dict[str, object]) -> dict[str, object]:
    """Call-site kwargs → `nora.bridge.*` span attributes through the SAME
    allowlist the lifecycle line uses — one wall for both surfaces, so a
    field that must not reach a log line cannot reach an exported span."""
    return {f"nora.bridge.{k}": v for k, v in fields.items()
            if k in _SAFE_LOG_FIELDS and v is not None}


def record_safe_error(span: Span | None, exc: BaseException) -> None:
    """The sanitized exception record: the exception TYPE ONLY.

    Deliberately NOT `span.record_exception` — the SDK helper writes the
    message and stacktrace, which carry ids, URLs, bodies and secrets. The
    type name is code, not data; the bounded `reason`/`outcome` fields on the
    lifecycle line carry the rest. The exception itself propagates unchanged
    to the work seam — this only records.
    """
    try:
        if span is None:
            return
        type_name = type(exc).__name__
        span.set_status(Status(StatusCode.ERROR, type_name))
        span.add_event("exception", {"exception.type": type_name})
    except Exception as inner:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
        log.debug("queue span error not recorded (%s)",
                  type(inner).__name__)


@contextmanager
def producer_span(platform: str, *, name: str = "queue.enqueue",
                  **fields: object) -> Iterator[dict[str, str]]:
    """The one short capture/publication span a bridge emits per enqueue.

    Yields the W3C carrier fields (`traceparent`, `tracestate`) the caller
    stamps onto the envelope's `trace` dict — `{}` when untraced, which is a
    valid input downstream (`remote_context` maps it to no link).

    `fields` become `nora.bridge.*` attributes through the lifecycle
    allowlist — an unsafe name is dropped, never exported.
    """
    span = _start(name, kind=SpanKind.PRODUCER, links=[],
                  attributes={"nora.bridge.platform": platform,
                              **_bridge_attrs(fields)})
    try:
        yield span_ref(span)
    except BaseException as exc:
        record_safe_error(span, exc)
        raise
    finally:
        if span is not None:
            try:
                span.end()
            except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
                log.debug("queue span did not close cleanly (%s)",
                          type(exc).__name__)


@contextmanager
def attempt_span(*, platform: str, work_kind: str, event_id: str,
                 attempt_id: str, generation: int,
                 producer: SpanContext | None,
                 previous_attempt: SpanContext | None,
                 stream_id: str | None = None,
                 queue_wait_ms: int | None = None,
                 tag_id: str | None = None) -> Iterator[Span | None]:
    """The fresh empty-parent CONSUMER root for ONE work attempt.

    Links: the original producer AND the previous attempt's persisted record —
    both as `Link`s, never as a parent. A linked remote context is not a
    parent (design 04): this attempt is its own trace, and no span stays open
    over a queue wait or a deploy.
    """
    links = _links(producer, previous_attempt)
    span = _start("queue.attempt", kind=SpanKind.CONSUMER, links=links,
                  attributes={
                      "nora.bridge.platform": platform,
                      "nora.bridge.work_kind": work_kind,
                      "nora.bridge.event_id": event_id,
                      "nora.bridge.attempt_id": attempt_id,
                      "nora.bridge.generation": generation,
                      "nora.bridge.stream_id": stream_id,
                      "nora.bridge.queue_wait_ms": queue_wait_ms,
                      "nora.bridge.tag_id": tag_id,
                      "nora.bridge.linked_producer": producer is not None,
                      "nora.bridge.linked_prev_attempt":
                          previous_attempt is not None,
                  })
    try:
        yield span
    except BaseException as exc:
        record_safe_error(span, exc)
        raise
    finally:
        if span is not None:
            try:
                span.end()
            except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
                log.debug("queue span did not close cleanly (%s)",
                          type(exc).__name__)


@contextmanager
def operation_span(op: QueueOp, *, platform: str,
                   links: list[Link] | None = None,
                   **fields: object) -> Iterator[Span | None]:
    """One bounded `queue.<op>` root — claim/reclaim, a bridge delivery
    attempt, an ACK — linked to the same event/action's contexts.

    Not activated as current on purpose: queue spans are self-contained roots
    and nothing should nest under one by accident. `fields` become
    `nora.bridge.*` attributes through the lifecycle allowlist.
    """
    if op not in QUEUE_OPS:
        raise ValueError(f"unknown queue op {op!r}")
    span = _start(f"queue.{op}", kind=SpanKind.CONSUMER,
                  links=list(links or []),
                  attributes={"nora.bridge.platform": platform,
                              **_bridge_attrs(fields)})
    try:
        yield span
    except BaseException as exc:
        record_safe_error(span, exc)
        raise
    finally:
        if span is not None:
            try:
                span.end()
            except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
                log.debug("queue span did not close cleanly (%s)",
                          type(exc).__name__)


# ---------------------------------------------------------------------------
# JSON lifecycle lines + the counters that count them
# ---------------------------------------------------------------------------

def lifecycle(metrics: Any, event: LifecycleEvent, *, platform: str,
              level: int | None = None, **fields: object) -> None:
    """One allowlisted JSON line + one `nora_bridge_queue_events_total` tick.

    The line and the counter answer different questions — the line is the
    forensic record of THIS transition (`docker logs`), the counter is the
    aggregate (`how many reclaims`). Passing an unsafe field costs only that
    field: the allowlist drops it rather than the line.
    """
    if event not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event {event!r}")
    try:
        # `event` is structlog's RESERVED message slot — `EventRenamer`
        # rewrites it into `message`, so the same name would eat the field.
        # `queue_event` carries the vocabulary name through the pipeline.
        safe: dict[str, object] = {"queue_event": event,
                                   "platform": platform}
        for key, value in fields.items():
            if key not in _SAFE_LOG_FIELDS or value is None:
                continue
            # safe scalars only — `str()` on an exception would carry its
            # message through an allowlisted field NAME
            if isinstance(value, (str, bool, int, float)):
                safe[key] = value
        log.log(level if level is not None
                else _EVENT_LEVELS.get(event, logging.INFO),
                f"bridge {event}", extra=safe)
    except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
        log.debug("bridge lifecycle line %s not emitted (%s)",
                  event, type(exc).__name__)
    if metrics is not None:
        metrics.inc("nora_bridge_queue_events_total",
                    platform=platform, event=event)


def attempt_metrics(metrics: Any, *, platform: str, kind: str,
                    outcome: str, duration_ms: float | None = None) -> None:
    """`nora_bridge_work_attempts_total{platform,kind,outcome}` + the attempt
    duration on `nora_bridge_stage_seconds{platform,stage,outcome}` when the
    caller measured it."""
    if metrics is None:
        return
    if outcome not in WORK_OUTCOMES:
        raise ValueError(f"unknown work outcome {outcome!r}")
    metrics.inc("nora_bridge_work_attempts_total",
                platform=platform, kind=kind, outcome=outcome)
    if duration_ms is not None:
        metrics.observe("nora_bridge_stage_seconds", duration_ms / 1000,
                        platform=platform, stage=kind, outcome=outcome)


def action_metrics(metrics: Any, *, platform: str, op: str,
                   outcome: str, duration_ms: float | None = None) -> None:
    """`nora_bridge_actions_total{platform,op,outcome}` — counted at the
    durable `bridge_action` result boundary, never at HTTP acceptance."""
    if metrics is None:
        return
    if outcome not in ACTION_OUTCOMES:
        raise ValueError(f"unknown action outcome {outcome!r}")
    metrics.inc("nora_bridge_actions_total",
                platform=platform, op=op, outcome=outcome)
    if duration_ms is not None:
        metrics.observe("nora_bridge_stage_seconds", duration_ms / 1000,
                        platform=platform, stage="deliver", outcome=outcome)


def observe_queue_wait(metrics: Any, *, platform: str, wait_ms: float) -> None:
    """`nora_bridge_queue_wait_seconds{platform}` — source-independent
    enqueue→claim time (`queued_ms` → attempt start)."""
    if metrics is not None:
        metrics.observe("nora_bridge_queue_wait_seconds", wait_ms / 1000,
                        platform=platform)


# ---------------------------------------------------------------------------
# The durable attempt record (bridge_work receipt truth)
# ---------------------------------------------------------------------------

def attempt_identity(work_id: int, generation: int) -> str:
    """The durable attempt id — `(work.id, generation)`, the existing claim
    fence. No invented uuid: the row already proves uniqueness."""
    return f"w-{work_id}:{generation}"


def attempt_record(*, generation: int, attempt_id: str,
                   ref: Mapping[str, str], started_ms: int) -> dict[str, Any]:
    """The record persisted BEFORE the work runs — `ref` is `span_ref`'s
    output for the attempt's own span."""
    return {"generation": generation, "attempt_id": attempt_id,
            "trace_id": ref.get("trace_id", ""),
            "span_id": ref.get("span_id", ""),
            "traceparent": ref.get("traceparent", ""),
            "started_ms": started_ms}


async def record_attempt_started(engine: Any, *, work_id: int,
                                 generation: int,
                                 record: Mapping[str, Any]
                                 ) -> AttemptWriteResult:
    """Persist the attempt record on the `bridge_work` receipt BEFORE the
    work runs — fenced by `generation`, so a stale owner cannot overwrite the
    live attempt's link target.

    Stored under `telemetry_attempt_<generation>` (a NEW top-level result key
    per attempt, so the `finish_work`-shaped shallow `||` merge never clobbers
    an earlier attempt) and mirrored at `telemetry_last_attempt` for the
    O(1) previous-attempt read. Never raises. The result distinguishes the
    two failures (`AttemptWriteResult`): `fenced` is the claim fence
    answering "not yours", `unavailable` is telemetry loss — the work
    proceeds either way, and an `unavailable` write is NEVER a reason to
    cancel the attempt.
    """
    patch = json.dumps({f"telemetry_attempt_{generation}": dict(record),
                        "telemetry_last_attempt": dict(record)})
    try:
        async with engine.begin() as conn:
            res = await conn.execute(sa.text(
                "UPDATE bridge_work SET"
                " result = COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb),"
                " updated_at = now()"
                " WHERE id = :id AND generation = :g"),
                {"patch": patch, "id": work_id, "g": generation})
            return "written" if res.rowcount == 1 else "fenced"
    except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
        log.debug("attempt record write failed for work %s "
                  "(proceeds untraced; %s)", work_id, type(exc).__name__)
        return "unavailable"


def attempt_finished_fields(generation: int, *, record: Mapping[str, Any],
                            outcome: str, finished_ms: int,
                            reason: str | None = None) -> dict[str, Any]:
    """The result-dict fragment a caller merges into `finish_work`'s `result`
    in the SAME fenced write — the completed attempt record re-emitted whole
    (`started` fields plus `finished_ms`/`outcome`/`reason`), and
    `telemetry_last_attempt` pointed at it.
    """
    done = {**dict(record), "outcome": outcome, "finished_ms": finished_ms}
    if reason is not None:
        done["reason"] = reason
    return {f"telemetry_attempt_{generation}": done,
            "telemetry_last_attempt": done}


def last_attempt(work_result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The previous attempt's persisted record from a claimed work row's
    `result`, or `None` — a fresh first attempt, or one whose predecessor's
    write was lost (linked as absent, never guessed)."""
    if not work_result:
        return None
    rec = work_result.get("telemetry_last_attempt")
    return dict(rec) if isinstance(rec, dict) else None


def attempt_link(record: Mapping[str, Any] | None) -> SpanContext | None:
    """A stored attempt record → the remote context a later attempt links."""
    if not record:
        return None
    return remote_context(record.get("traceparent") or None)


def generation_link_fields(*, attempt: Mapping[str, Any] | None,
                           event_id: str, attempt_id: str,
                           queue_wait_ms: int | None = None) -> dict[str, Any]:
    """What the model turn's SEPARATE generation root carries back to the
    queue attempt that spawned it: `links` = the attempt's context,
    `attributes` = safe event/attempt identity and queue timing (design 04:
    "linked to the relevant consumer attempt … not a child of a queue
    trace").
    """
    ctx = attempt_link(attempt)
    attrs: dict[str, object] = {
        "nora.bridge.event_id": event_id,
        "nora.bridge.attempt_id": attempt_id,
    }
    if queue_wait_ms is not None:
        attrs["nora.bridge.queue_wait_ms"] = queue_wait_ms
    if attempt is not None:
        attrs["nora.bridge.attempt_trace_id"] = attempt.get("trace_id", "")
        attrs["nora.bridge.attempt_span_id"] = attempt.get("span_id", "")
    return {"links": _links(ctx), "attributes": attrs}
