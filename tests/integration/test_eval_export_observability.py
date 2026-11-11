"""Eval export/visibility durability: SDK sinks, truncation limits and
terminal-state export, against the shared generated environment."""

from __future__ import annotations

import json

import pytest
from opentelemetry import trace
from strands_evals import Case, Experiment

import evals.__main__ as cli
import evals.backbone as backbone
from nora.subject.eval_evidence import EvidenceError
from nora.subject.sandbox import eval_host_lock
from tests.integration.eval_observability_env import (
    capture,
    model_round_trip,
    rows,
)
from tests.integration.eval_observability_env import (
    evidence_root as evidence_root,
)
from tests.integration.test_grading_contract import generated_cli_contract as generated_cli_contract
from tests.integration.test_spike_visibility import otlp_wire as otlp_wire


@pytest.mark.parametrize("export_fails", [False, True])
async def test_authorized_signoz_sdk_export(evidence_root, monkeypatch, tmp_path, export_fails):
    """Real SDK/tool trajectory and a judge-shaped call through the OTLP encoder."""
    from io import StringIO

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    from nora.agent.telemetry import EvaluationExportError, evaluation_export
    from nora.subject.eval_evidence import read_events, validate_events

    payloads = []
    def send(session, url, **kwargs):
        assert url == "http://127.0.0.1:14318/v1/traces"
        payloads.append(kwargs["data"])
        import requests
        response = requests.Response()
        response.status_code = 400 if export_fails else 200
        response._content = b""
        return response

    monkeypatch.setattr("requests.Session.request", send)
    def urllib_send(pool, method, url, **kwargs):
        assert method == "POST"
        assert url == "http://127.0.0.1:14318/v1/traces"
        payloads.append(kwargs["body"])
        from urllib3.response import HTTPResponse
        return HTTPResponse(body=b"", status=400 if export_fails else 200)
    monkeypatch.setattr("urllib3.PoolManager.request", urllib_send)
    backbone._prefill_capture()
    provider = trace.get_tracer_provider()
    unauthorized = StringIO()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=unauthorized)))
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    requests_seen = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        if export_fails:
            with (pytest.raises((EvidenceError, EvaluationExportError)),
                  evidence.capture(provider, export=export),
                  evidence.case("generated-failure", input="5", metadata={})):
                await model_round_trip(requests_seen)
        else:
            with evidence.capture(provider, export=export):
                with evidence.case("generated-writer", input="5", metadata={}):
                    await model_round_trip(requests_seen, sentinel="GENERATED_EXPORT_SENTINEL")
                with evidence.case("generated-judge", input="grade generated answer", metadata={}):
                    # Independent invocation, same real SDK. No paid judge.
                    await model_round_trip([], sentinel="GENERATED_JUDGE_REASON",
                                           rounds=1, judge=True)
    assert payloads  # negative sink checks cannot pass through an idle exporter
    assert "GENERATED_EXPORT_SENTINEL" not in unauthorized.getvalue()
    if export_fails:
        assert validate_events(read_events(evidence.path))["status"] == "ERROR"
        return
    decoded = [ExportTraceServiceRequest.FromString(p) for p in payloads]
    spans = [s for p in decoded for rs in p.resource_spans
             for ss in rs.scope_spans for s in ss.spans]
    def attrs(span):
        return {a.key: a.value.string_value or a.value.int_value for a in span.attributes}
    roots = [s for s in spans if s.name == "eval.run"]
    cases = [s for s in spans if s.name == "eval.case"]
    models = [s for s in spans if attrs(s).get("gen_ai.operation.name") == "chat"]
    tools = [s for s in spans if attrs(s).get("gen_ai.operation.name") == "execute_tool"]
    assert len(roots) == 1 and len(cases) == 2 and len(models) == 3 and len(tools) == 1
    assert all(s.parent_span_id == roots[0].span_id for s in cases)
    assert len({s.trace_id for s in spans}) == 1
    assert all(attrs(s)["nora.eval.run_id"] == "generated-run" for s in spans)
    assert all(attrs(s).get("nora.eval.case_id") for s in models + tools)
    assert all(attrs(s).get("gen_ai.input.messages") for s in models)
    assert all(attrs(s).get("gen_ai.output.messages") for s in models)
    assert all(attrs(s)["gen_ai.system_instructions"] for s in models)
    assert any("Judge the generated answer" in attrs(s)["gen_ai.system_instructions"]
               for s in models)
    assert sum("next_number" in attrs(s)["gen_ai.tool.definitions"] for s in models) == 2
    assert sum(attrs(s)["gen_ai.usage.input_tokens"] for s in models) == 36
    assert all(attrs(s)["gen_ai.request.model"] == "generated-model" for s in models)
    wire = "".join(str(p) for p in decoded)
    assert "generated-served" in wire and "generated-provider" in wire
    assert "GENERATED_JUDGE_REASON" in wire and "tool_finished" in wire
    assert "generated-private-auth-value" not in wire
    assert validate_events(read_events(evidence.path))["status"] == "PASS"
    # Synthetic OTLP only, usable for a separate collector/backend readback.
    combined = ExportTraceServiceRequest()
    for payload in decoded:
        combined.resource_spans.extend(payload.resource_spans)
    (tmp_path / "synthetic-eval.pb").write_bytes(combined.SerializeToString())
    (tmp_path / "trace-id.txt").write_text(roots[0].trace_id.hex())


@pytest.mark.parametrize("endpoint", ["https://cloud.invalid/v1/traces",
    "http://127.0.0.1:3100/v1/traces", "http://127.0.0.1:14318/v1/traces?token=generated"])
def test_eval_export_refuses_unauthorized_sink(endpoint):
    from nora.agent.telemetry import EvaluationExportError, evaluation_export
    with pytest.raises(EvaluationExportError):
        evaluation_export({"enabled": True, "exporter": "otlp_http", "endpoint": endpoint})


def test_missing_model_request_is_capture_error():
    from nora.subject.eval_evidence import validate_events
    receipt = validate_events([{"kind": "transport_started"}, {"kind": "transport_finished"},
                               {"kind": "run_finished"}])
    assert receipt["status"] == "ERROR" and "request_capture_missing" in receipt["causes"]


@pytest.mark.parametrize("section", [
    'obs = "generated-invalid-table"',
    '[obs]\ntracing = "generated-invalid-table"',
    '[obs.tracing]\nendpoint = ["generated-invalid-url"]',
])
def test_malformed_visibility_config_is_a_clean_refusal(tmp_path, section):
    from nora.subject.eval_evidence import evaluation_export_for
    config = tmp_path / "generated.toml"
    config.write_text(section)
    with pytest.raises(EvidenceError, match="configured evaluation visibility unavailable"):
        evaluation_export_for(config)


async def test_dropped_eval_content_invalidates_visibility(evidence_root, monkeypatch):
    from opentelemetry.sdk.trace.export import SpanExportResult

    from nora.agent.telemetry import evaluation_export
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export",
        lambda *args: SpanExportResult.SUCCESS)
    backbone._prefill_capture()
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with (
            pytest.raises(EvidenceError),
            evidence.capture(trace.get_tracer_provider(), export=export),
            trace.get_tracer(__name__).start_as_current_span("generated-overflow") as span,
        ):
            from opentelemetry.sdk.util import BoundedList
            span._events = BoundedList(8)
            for i in range(200):
                span.add_event("generated-content", {"index": i})


def test_truncating_sdk_limits_refuse_before_work(evidence_root, monkeypatch):
    from nora.agent.telemetry import evaluation_export
    backbone._prefill_capture()
    provider = trace.get_tracer_provider()
    monkeypatch.setattr(provider._span_limits, "max_span_attribute_length", 8)
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    reached = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with pytest.raises(EvidenceError, match="truncation"), evidence.capture(
                provider, export=export):
            reached.append(True)
    assert reached == []


def test_template_sink_refusal_precedes_evidence_and_body(
        evidence_root, monkeypatch, example_config_path, capsys, generated_cli_contract):
    reached = []
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: reached.append(True))
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection",
                                    "--deployment-config", str(example_config_path)])
    assert cli.main() == 2
    assert reached == [] and not evidence_root.exists()
    assert "configured evaluation visibility unavailable" in capsys.readouterr().err


def test_full_corpus_and_long_sdk_stream_export_without_drops(
        evidence_root, monkeypatch, example_config_path, tmp_path, otlp_wire):
    from strands.telemetry.tracer import get_tracer

    from nora.agent.telemetry import evaluation_export
    from tests.integration.test_spike_visibility import attrs, exported_spans

    requests = []
    async def drive(case, spec):
        if case.name == "generated-128":
            await model_round_trip(requests, sentinel="GENERATED_LONG_TAIL", stream_chunks=200)
        return {"output": "6", "environment_state": [
            backbone.EnvironmentState(name=backbone.NORA_SAID, state=1)]}

    monkeypatch.setattr(backbone, "_drive_uncaptured", drive)
    experiment = Experiment(cases=[Case(name=f"generated-{i}", input=str(i)) for i in range(129)],
                            evaluators=[backbone.AbsenceGrader()])
    monkeypatch.setattr(cli, "experiment_for", lambda *a, **k: (
        experiment, backbone.nora_task(backbone.Spec(source_url="", entry="tag"))))
    monkeypatch.setattr(cli, "census", lambda _: "generated census")
    backbone._prefill_capture()
    provider = trace.get_tracer_provider()
    limits = provider._span_limits
    before_limit = limits.max_events
    before_processors = provider._active_span_processor._span_processors
    tracer = get_tracer()
    fields = ("use_latest_genai_conventions", "_span_attributes_only",
              "_include_tool_definitions", "_redaction_enabled")
    before_format = [getattr(tracer, k) for k in fields]
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(provider, export=export):
            cli._run_one("generated", source_url="", judge=None, sample=1)
            evidence.exit_code = 0
    assert provider._span_limits is limits and limits.max_events == before_limit
    assert provider._active_span_processor._span_processors is before_processors
    assert [getattr(tracer, k) for k in fields] == before_format
    spans = exported_spans(otlp_wire)
    assert spans and all(s.dropped_events_count == s.dropped_attributes_count
                         == s.dropped_links_count == 0 for s in spans)
    cases = [s for s in spans if s.name == "eval.case"]
    assert len(cases) == 129
    for case in cases:
        names = [e.name for e in case.events]
        assert "nora.eval.case_started" in names and "nora.eval.case_finished" in names
    root = next(s for s in spans if s.name == "eval.run")
    assert not any(e.name.startswith("nora.eval.case_") for e in root.events)
    assert any(e.name == "nora.eval.run_finished" for e in root.events)
    data = rows(evidence)
    assert len([e for e in data if e["kind"] == "transport_response"]) > 200
    private_requests = [json.loads(e["attributes"]["nora.model.request.body"])
                        for e in data if e.get("name") == "nora.model.request"]
    assert private_requests == requests and requests[-1]["messages"][-1]["role"] == "tool"
    assert "GENERATED_LONG_TAIL" in str(requests[-1]["messages"])
    models = [s for s in spans if attrs(s).get("gen_ai.operation.name") == "chat"]
    assert len(models) == 2  # many chunks are still one invocation, not invented spans
    final = next(s for s in models if "piece-199" in attrs(s).get("gen_ai.output.messages", ""))
    outputs = json.loads(attrs(final)["gen_ai.output.messages"])
    text = "".join(p.get("content", "") for p in outputs[0]["parts"] if p["type"] == "text")
    assert text == "".join(f"piece-{i} " for i in range(200)) + "6 GENERATED_LONG_TAIL"
    mirrored_chunks = [e for e in final.events if e.name == "nora.eval.transport_response"]
    assert len(mirrored_chunks) == 202
    assert "GENERATED_LONG_TAIL" in str(mirrored_chunks[-2])
    assert attrs(root)["nora.eval.capture.status"] == "complete"
    assert attrs(root)["nora.eval.export.status"] == "pending_root"
    assert next(e for e in data if e["kind"] == "export_result")["status"] == "acknowledged"


@pytest.mark.parametrize("failure", ["receipt", "model", "body"])
def test_failed_run_terminal_is_exported_while_root_records(
        evidence_root, otlp_wire, failure):
    from strands.telemetry.tracer import get_tracer

    from nora.agent.telemetry import evaluation_export
    from tests.integration.test_spike_visibility import attrs, exported_spans

    backbone._prefill_capture()
    provider = trace.get_tracer_provider()
    before_limit = provider._span_limits.max_events
    fields = ("use_latest_genai_conventions", "_span_attributes_only",
              "_include_tool_definitions", "_redaction_enabled")
    before_format = [getattr(get_tracer(), key) for key in fields]
    before_processors = provider._active_span_processor._span_processors
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with (
            pytest.raises((EvidenceError, ValueError)),
            evidence.capture(provider, export=export),
            evidence.case("generated-failure", input="generated", metadata={}),
        ):
            if failure == "receipt":
                evidence.record("judge_error", cause="generated_late_failure")
            elif failure == "model":
                evidence.record("call_started")
                evidence.record("call_finished", error_type="GeneratedModelError", retry=False)
            else:
                raise ValueError("generated body error")
    spans = exported_spans(otlp_wire)
    root = next(s for s in spans if s.name == "eval.run")
    assert attrs(root)["nora.eval.capture.status"] == "failed" and root.status.code == 2
    assert any(e.name == "nora.eval.run_failed" for e in root.events)
    assert not any(e.name == "nora.eval.run_finished" for e in root.events)
    assert provider._span_limits.max_events == before_limit
    assert [getattr(get_tracer(), key) for key in fields] == before_format
    assert provider._active_span_processor._span_processors is before_processors
    assert not any(e["kind"] == "run_finished" for e in rows(evidence))

