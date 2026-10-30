"""Bridge queue telemetry — the no-I/O half (tests/README.md: unit is
no-IO logic; the Redis/PG/collector halves live in
`tests/integration/test_bridge_observability.py`).

Every span assertion reads `exported_spans` — the production-sampler
provider — because «did the root admit» is an EXPORT question, not a
was-the-helper-called question.
"""

from __future__ import annotations

import json
import logging
from typing import get_args

import pytest
from opentelemetry.trace import SpanKind, StatusCode

from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt

PLATFORM = "signal"


def _finished(exporter, name: str):
    found = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(found) == 1, f"expected one {name}, got {len(found)}"
    return found[0]


def _tp(trace_id: int = 0xA1, span_id: int = 0xB2) -> str:
    return f"00-{trace_id:032x}-{span_id:016x}-01"


# --------------------------------------------------------------------------
# W3C link contexts
# --------------------------------------------------------------------------

class TestRemoteContext:
    def test_valid_traceparent(self):
        ctx = bt.remote_context(_tp())
        assert ctx is not None and ctx.is_remote
        assert ctx.trace_id == 0xA1 and ctx.span_id == 0xB2

    @pytest.mark.parametrize("bad", [
        None, "", "garbage", "00-abc-def-01",              # too few/malformed
        "ff-" + "a" * 32 + "-" + "b" * 16 + "-01",         # version ff = invalid
        "00-" + "0" * 32 + "-" + "b" * 16 + "-01",         # all-zero trace id
        "00-" + "a" * 32 + "-" + "0" * 16 + "-01",         # all-zero span id
        "00-" + "a" * 32 + "-" + "b" * 16,                 # missing flags
        12,                                                 # wrong type
    ])
    def test_malformed_is_no_link(self, bad):
        assert bt.remote_context(bad) is None

    def test_tracestate_carried(self):
        ctx = bt.remote_context(_tp(), "vendor=opaque")
        assert ctx is not None and "vendor" in ctx.trace_state

    def test_bad_tracestate_degrades_not_drops(self):
        ctx = bt.remote_context(_tp(), "\x00\x01")
        assert ctx is not None


# --------------------------------------------------------------------------
# Spans: bounded roots, validated links, production sampling
# --------------------------------------------------------------------------

class TestProducerSpan:
    def test_exports_a_producer_root_and_carrier(self, exported_spans):
        with bt.producer_span(PLATFORM, event_id="ev-1") as carrier:
            assert carrier["traceparent"].startswith("00-")
        s = _finished(exported_spans, "queue.enqueue")
        assert s.kind == SpanKind.PRODUCER
        assert s.attributes["nora.trace_kind"] == "queue"
        assert s.parent is None  # its own trace — never nested
        assert s.attributes["nora.bridge.event_id"] == "ev-1"

    def test_carrier_matches_the_span(self, exported_spans):
        with bt.producer_span(PLATFORM) as carrier:
            pass
        s = _finished(exported_spans, "queue.enqueue")
        ctx = bt.remote_context(carrier["traceparent"])
        assert ctx.trace_id == s.context.trace_id
        assert ctx.span_id == s.context.span_id


class TestAttemptSpan:
    def test_links_producer_and_previous_attempt(self, exported_spans):
        producer = bt.remote_context(_tp(0x1, 0x11))
        previous = bt.remote_context(_tp(0x2, 0x22))
        with bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="ev-9",
            attempt_id="w-7:2", generation=2,
            producer=producer, previous_attempt=previous,
            queue_wait_ms=4200,
        ):
            pass
        s = _finished(exported_spans, "queue.attempt")
        assert s.kind == SpanKind.CONSUMER
        assert s.parent is None                      # a fresh root, not a child
        assert len(s.links) == 2
        linked = {(link.context.trace_id, link.context.span_id)
                  for link in s.links}
        assert (0x1, 0x11) in linked and (0x2, 0x22) in linked
        assert s.attributes["nora.bridge.queue_wait_ms"] == 4200
        assert s.attributes["nora.bridge.generation"] == 2
        assert s.attributes["nora.bridge.linked_producer"] is True

    def test_missing_links_are_absent_not_fabricated(self, exported_spans):
        with bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="e",
            attempt_id="w-1:0", generation=0,
            producer=None, previous_attempt=None,
        ):
            pass
        s = _finished(exported_spans, "queue.attempt")
        assert len(s.links) == 0
        assert s.attributes["nora.bridge.linked_producer"] is False


class TestOperationSpan:
    def test_bounded_ops_export_as_own_traces(self, exported_spans):
        with bt.operation_span("ack", platform=PLATFORM):
            pass
        s = _finished(exported_spans, "queue.ack")
        assert s.attributes["nora.trace_kind"] == "queue"
        assert s.parent is None

    def test_unknown_op_fails_loud(self):
        with pytest.raises(ValueError, match="unknown queue op"), \
                bt.operation_span("xdel", platform=PLATFORM):
            pass


# --------------------------------------------------------------------------
# Sanitized exceptions — the type is code; the message is data
# --------------------------------------------------------------------------

class TestSanitizedException:
    def test_error_records_type_only(self, exported_spans):
        planted = "s3cr3t-t0k3n"
        with pytest.raises(RuntimeError), bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="e",
            attempt_id="w-1:0", generation=0,
            producer=None, previous_attempt=None,
        ):
            raise RuntimeError(
                f"auth failed {planted} for https://u:p@h/x sender +85290000110")
        s = _finished(exported_spans, "queue.attempt")
        assert s.status.status_code == StatusCode.ERROR
        assert s.status.description == "RuntimeError"   # the TYPE survives
        exc_events = [e for e in s.events if e.name == "exception"]
        assert len(exc_events) == 1
        assert exc_events[0].attributes == {"exception.type": "RuntimeError"}
        dumped = (repr(dict(s.attributes))
                  + repr(s.status.description)
                  + repr(exc_events[0].attributes))
        assert planted not in dumped
        assert "85290000110" not in dumped and "https://" not in dumped
        # what the SDK's record_exception would have written, and we never do
        assert "exception.message" not in exc_events[0].attributes
        assert "exception.stacktrace" not in exc_events[0].attributes

    def test_cancellation_is_recorded_and_reraised(self, exported_spans):
        import asyncio
        with pytest.raises(asyncio.CancelledError), \
                bt.operation_span("claim", platform=PLATFORM):
            raise asyncio.CancelledError()
        s = _finished(exported_spans, "queue.claim")
        assert s.status.status_code == StatusCode.ERROR


# --------------------------------------------------------------------------
# JSON lifecycle lines: closed event vocab + hard field allowlist
# --------------------------------------------------------------------------

class TestLifecycleLog:
    def test_allowlist_drops_everything_private(self, caplog):
        metrics = Metrics()
        with caplog.at_level(logging.INFO, "nora.platform.bridge.telemetry"):
            bt.lifecycle(
                metrics, "enqueued", platform=PLATFORM,
                event_id="ev-3", stream_id="1-0", queued_ms=1700000000000,
                body="the message text", sender_id="+85290000110",
                account_key="acct", conversation_id="conv-9",
                payload={"raw": True}, exception=ValueError("x"),
            )
        rec = next(r for r in caplog.records
                   if r.message.startswith("bridge enqueued"))
        assert rec.event_id == "ev-3" and rec.stream_id == "1-0"
        assert rec.queued_ms == 1700000000000
        for dropped in ("body", "sender_id", "account_key",
                        "conversation_id", "payload", "exception"):
            assert not hasattr(rec, dropped), f"{dropped} leaked into the line"
        assert metrics.get("nora_bridge_queue_events_total",
                           platform=PLATFORM, event="enqueued") == 1

    def test_unknown_event_fails_loud(self):
        with pytest.raises(ValueError, match="unknown lifecycle event"):
            bt.lifecycle(None, "enq", platform=PLATFORM)

    def test_failure_events_log_warning(self, caplog):
        with caplog.at_level(logging.DEBUG, "nora.platform.bridge.telemetry"):
            bt.lifecycle(None, "attempt_failed", platform=PLATFORM,
                         event_id="e", reason="retryable_failure")
        rec = caplog.records[-1]
        assert rec.levelno == logging.WARNING

    def test_vocabulary_is_derived_once(self):
        assert frozenset(get_args(bt.LifecycleEvent)) == bt.LIFECYCLE_EVENTS
        assert frozenset(get_args(bt.WorkOutcome)) == bt.WORK_OUTCOMES
        assert frozenset(get_args(bt.ActionOutcome)) == bt.ACTION_OUTCOMES
        assert frozenset(
            get_args(bt.InspectionResult)) == bt.INSPECTION_RESULTS

    def test_inspection_result_is_exactly_the_three_states(self):
        # degradation is metadata on QueueInspection, never a fourth status
        assert get_args(bt.InspectionResult) == (
            "ok", "nothing_found", "unavailable")


class TestFieldValuesAreSanitized:
    """The allowlist vets field NAMES; the values must also be safe — a
    non-primitive is dropped, never `str()`-ed (an exception's `__str__` is
    its message)."""

    def test_object_values_dropped_from_lifecycle_and_spans(
            self, caplog, exported_spans):
        planted = RuntimeError("auth failed s3cr3t-value")
        with caplog.at_level(logging.INFO, "nora.platform.bridge.telemetry"):
            bt.lifecycle(None, "enqueued", platform=PLATFORM,
                         event_id=planted, reason=planted)
        rec = next(r for r in caplog.records
                   if r.message.startswith("bridge enqueued"))
        assert not hasattr(rec, "event_id")
        assert not hasattr(rec, "reason")
        assert "s3cr3t" not in caplog.text

        with bt.operation_span("ack", platform=PLATFORM,
                               event_id=planted, reason=planted):
            pass
        s = _finished(exported_spans, "queue.ack")
        assert "nora.bridge.event_id" not in s.attributes
        assert "nora.bridge.reason" not in s.attributes
        assert "s3cr3t" not in repr(dict(s.attributes))

    def test_lifecycle_serializes_through_the_production_json_pipeline(
            self, capsys):
        """caplog proves the field allowlist; THIS proves the line survives
        the real structlog formatter as JSON — safe context in, unsafe
        context out, with the redactor OFF (empty `secrets`) so it cannot be
        what saved us."""
        from nora.obs.logging import setup_logging
        root = logging.getLogger()
        saved = root.handlers[:]
        setup_logging(level="INFO", format="json", secrets=[])
        try:
            bt.lifecycle(None, "enqueued", platform=PLATFORM,
                         event_id="ev-json", stream_id="1-7",
                         queued_ms=99, body="raw body text",
                         sender_id="+85290000112")
        finally:
            for h in root.handlers[:]:
                root.removeHandler(h)
                h.close()
            for h in saved:
                root.addHandler(h)
        out = capsys.readouterr().out
        line = json.loads(
            next(ln for ln in out.splitlines() if "enqueued" in ln))
        # `queue_event` survives the pipeline; a bare `event` key would have
        # been eaten by structlog's EventRenamer into `message`
        assert line["queue_event"] == "enqueued"
        assert line["message"] == "bridge enqueued"
        assert line["event_id"] == "ev-json"
        assert line["stream_id"] == "1-7"
        assert line["queued_ms"] == 99
        assert line["platform"] == PLATFORM
        assert "body" not in line and "sender_id" not in line
        assert "raw body text" not in out and "+85290000112" not in out


# --------------------------------------------------------------------------
# Metrics helpers + priming
# --------------------------------------------------------------------------

class TestMetricsHelpers:
    def test_attempt_and_action_counters(self):
        m = Metrics()
        bt.attempt_metrics(m, platform=PLATFORM, kind="turn",
                           outcome="retryable_failure", duration_ms=250)
        bt.action_metrics(m, platform=PLATFORM, op="send_reply",
                          outcome="confirmed")
        assert m.get("nora_bridge_work_attempts_total",
                     platform=PLATFORM, kind="turn",
                     outcome="retryable_failure") == 1
        assert m.get("nora_bridge_actions_total",
                     platform=PLATFORM, op="send_reply",
                     outcome="confirmed") == 1
        stage = m.observations("nora_bridge_stage_seconds",
                               platform=PLATFORM, stage="turn",
                               outcome="retryable_failure")
        assert stage == [0.25]

    def test_outcome_outside_the_vocab_fails_loud(self):
        m = Metrics()
        with pytest.raises(ValueError, match="unknown work outcome"):
            bt.attempt_metrics(m, platform=PLATFORM, kind="turn",
                               outcome="sorta_ok")
        with pytest.raises(ValueError, match="unknown action outcome"):
            bt.action_metrics(m, platform=PLATFORM, op="send_reply",
                              outcome="sent")

    def test_queue_wait_histogram(self):
        m = Metrics()
        bt.observe_queue_wait(m, platform=PLATFORM, wait_ms=2000)
        assert m.observations("nora_bridge_queue_wait_seconds",
                              platform=PLATFORM) == [2.0]


# --------------------------------------------------------------------------
# The durable attempt record (receipt truth, no ledger)
# --------------------------------------------------------------------------

class TestAttemptRecord:
    def test_identity_is_the_claim_fence(self):
        assert bt.attempt_identity(42, 3) == "w-42:3"

    def test_record_round_trips_to_a_link(self, exported_spans):
        with bt.producer_span(PLATFORM) as carrier:
            pass
        ctx = bt.remote_context(carrier["traceparent"])
        ref = {"trace_id": f"{ctx.trace_id:032x}",
               "span_id": f"{ctx.span_id:016x}",
               "traceparent": carrier["traceparent"]}
        rec = bt.attempt_record(generation=1, attempt_id="w-9:1",
                                ref=ref, started_ms=100)
        assert bt.attempt_link(rec).trace_id == ctx.trace_id
        assert bt.last_attempt({"telemetry_last_attempt": rec}) == rec
        assert bt.last_attempt({}) is None
        assert bt.last_attempt(None) is None

    async def test_write_failure_is_unavailable_not_fenced(self, caplog):
        """A dead engine is telemetry loss (`unavailable`), NOT a stale
        fence — the integrator must never cancel work on it. The debug line
        carries the exception TYPE only; its message would carry internals.
        """
        class _DeadBegin:
            async def __aenter__(self):
                raise RuntimeError("db down — dsn u:p@h/db s3cr3t-db")

            async def __aexit__(self, *a):
                return False

        class _DeadEngine:
            def begin(self):
                return _DeadBegin()

        with caplog.at_level(logging.DEBUG,
                             "nora.platform.bridge.telemetry"):
            result = await bt.record_attempt_started(
                _DeadEngine(), work_id=1, generation=1, record={})
        assert result == "unavailable"
        assert "s3cr3t" not in caplog.text and "u:p@h" not in caplog.text
        assert "Traceback" not in caplog.text   # no exc_info anywhere
        assert "RuntimeError" in caplog.text    # the TYPE survived

    def test_finished_fields_merge_for_the_fenced_write(self):
        rec = {"generation": 2, "attempt_id": "w-1:2", "traceparent": "t",
               "started_ms": 10}
        out = bt.attempt_finished_fields(
            2, record=rec, outcome="done", finished_ms=99,
            reason="completed")
        assert out["telemetry_last_attempt"]["finished_ms"] == 99
        assert out["telemetry_attempt_2"]["outcome"] == "done"
        assert out["telemetry_attempt_2"]["started_ms"] == 10

    def test_generation_root_link_fields(self):
        rec = {"traceparent": _tp(0x5, 0x6), "trace_id": "0" * 31 + "5",
               "span_id": "0" * 15 + "6"}
        fields = bt.generation_link_fields(
            attempt=rec, event_id="ev-1", attempt_id="w-1:0",
            queue_wait_ms=10)
        assert len(fields["links"]) == 1
        assert fields["links"][0].context.trace_id == 0x5
        assert fields["attributes"]["nora.bridge.event_id"] == "ev-1"
        # a missing attempt links nothing — never a fabricated context
        assert bt.generation_link_fields(
            attempt=None, event_id="e", attempt_id="a")["links"] == []
