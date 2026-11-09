"""The operator's memory-graph entrypoints export fresh generated SDK calls."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from nora.agent import structured
from nora.agent.telemetry import EvaluationExportError
from nora.obs.tracing import record_eval_event
from nora.subject.eval_evidence import EvidenceError, read_events
from scripts import memory_graph_spike as spike
from tests.integration.test_eval_observability import model_round_trip
from tests.unit.test_memory_graph_spike import generated_replay_environment
from tests.unit.test_replay_scoring import generated_completion


@pytest.fixture
def otlp_wire(monkeypatch):
    wire = SimpleNamespace(payloads=[], fail=False)

    def send(session, url, **kwargs):
        import requests
        assert url == "http://127.0.0.1:14318/v1/traces"
        wire.payloads.append(kwargs["data"])
        response = requests.Response()
        response.status_code = 400 if wire.fail else 200
        response._content = b""
        return response

    def urllib_send(pool, method, url, **kwargs):
        from urllib3.response import HTTPResponse
        assert method == "POST" and url == "http://127.0.0.1:14318/v1/traces"
        wire.payloads.append(kwargs["body"])
        return HTTPResponse(body=b"", status=400 if wire.fail else 200)

    monkeypatch.setattr("requests.Session.request", send)
    monkeypatch.setattr("urllib3.PoolManager.request", urllib_send)
    return wire


def configured_sink(template, target):
    import tomlkit
    document = tomlkit.parse(template.read_text())
    document["obs"]["tracing"].update(
        enabled=True, exporter="otlp_http", endpoint="http://127.0.0.1:14318/v1/traces")
    target.write_text(tomlkit.dumps(document))


def exported_spans(wire):
    return [s for data in wire.payloads
            for resource in ExportTraceServiceRequest.FromString(data).resource_spans
            for scope in resource.scope_spans for s in scope.spans]


def attrs(span):
    return {a.key: a.value.string_value or a.value.int_value for a in span.attributes}


@pytest.mark.parametrize("export_fails", [False, True])
async def test_memory_graph_replay_exports_writer_and_judges(
        tmp_path, monkeypatch, example_config_path, otlp_wire, export_fails, capsys):
    real_scoring = spike.smoke_fact_support
    env = generated_replay_environment(tmp_path, monkeypatch)
    args, runner = env.args, env.runner
    args.smoke = True
    env.cfg.llm.timeout_seconds = 5
    configured_sink(example_config_path, args.config)
    # Reuse the existing generated snapshot boundaries, preserving the real
    # _run selection/inspection/writer/scoring loop and both real fact judges.
    monkeypatch.setattr(spike, "smoke_fact_support", real_scoring)
    monkeypatch.setattr(spike, "model_endpoint", lambda path, purpose, model="": (
        "https://generated.invalid/v1", "generated-private-auth-value", model))
    runner.inspect.return_value = {"system": "Increment the supplied integer.", "agenda": "5 "}
    dispatches = []
    writer_requests = []

    async def replay(**kwargs):
        dispatches.append("writer")
        with trace.get_tracer(__name__).start_as_current_span("generated.writer"):
            record_eval_event("nora.session.started", {"body": json.dumps({
                "session_id": kwargs["now_ms"], "system": "Increment the supplied integer.",
                "agenda": "5 ", "items": []})})
            await model_round_trip(writer_requests if not writer_requests else [])
            record_eval_event("nora.session.terminal", {"body": json.dumps({
                "session_id": kwargs["now_ms"]})})
        return SimpleNamespace(session_id=kwargs["now_ms"], closed=[])

    runner.replay.side_effect = replay
    def judge_response(request):
        body = json.loads(request.content)
        assert body["model"] in {args.judge_a, args.judge_b} and not body["stream"]
        assert "passing moments" in body["messages"][0]["content"]
        dispatches.append(body["model"])
        return generated_completion(body, {"x-omniroute-model": body["model"],
                                           "x-omniroute-provider": "generated-judge-vendor"})

    original_build = structured.build_structured_model
    # Each SDK-owned provider closes its client after the call; share the
    # deterministic response function, not a client already closed by judge A.
    monkeypatch.setattr(spike, "build_structured_model", lambda **kw: original_build(
        **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(judge_response))))
    otlp_wire.fail = export_fails
    if export_fails:
        with pytest.raises((EvidenceError, EvaluationExportError)):
            await spike.run(args)
        assert otlp_wire.payloads  # reached and rejected an actual encoded preflight
        assert dispatches == []
        runner.inspect.assert_not_awaited()
        runner.replay.assert_not_awaited()
        return
    report = await spike.run(args)

    assert report["evidence"]["status"] == "PASS"
    assert dispatches == ["writer", args.judge_a, args.judge_b] * 2
    spans = exported_spans(otlp_wire)
    cases = [s for s in spans if s.name == "eval.case"]
    roots = [s for s in spans if s.name == "eval.run"]
    assert len(roots) == 1 and len(cases) == 2
    assert all(s.parent_span_id == roots[0].span_id for s in cases)
    assert {attrs(s)["nora.eval.case.name"] for s in cases} == {
        "wake:generated-unit:1", "wake:generated-unit:2"}
    by_id = {s.span_id: s for s in spans}
    case_ids = {s.span_id for s in cases}
    groups = {s.span_id: [] for s in cases}
    models = [s for s in spans if attrs(s).get("gen_ai.operation.name") == "chat"]
    assert len(models) == 8
    for model in models:
        parent = model.parent_span_id
        while parent not in case_ids:
            parent = by_id[parent].parent_span_id
        groups[parent].append(model)
        assert attrs(model)["nora.eval.case_id"] == attrs(by_id[parent])["nora.eval.case_id"]
        assert attrs(model)["gen_ai.input.messages"] and attrs(model)["gen_ai.output.messages"]
    assert all(sorted(attrs(m)["gen_ai.request.model"] for m in group) == sorted([
        "generated-model", "generated-model", args.judge_a, args.judge_b])
        for group in groups.values())
    events = read_events(next(args.output.glob("spike-*")))
    starts = [e for e in events if e["kind"] == "case_started"]
    cohort = spike.fingerprint(args.output / "sample.json")
    assert all(e["metadata"]["sample_sha256"] == cohort for e in starts)
    assert all(e["metadata"]["snapshot_locator_sha256"] and e["metadata"]["replay_ms"]
               for e in starts)
    assert {e["wake_id"] for e in events if e["kind"] == "replay_session"} == {1, 2}
    trace_id = roots[0].trace_id.hex()
    assert "eval trace: " + trace_id in capsys.readouterr().err
    assert "generated-private-auth-value" not in str(spans)


@pytest.mark.parametrize("export_fails", [False, True])
@pytest.mark.parametrize("entry,body_name", [
    ("run_rescore", "rescore_facts"), ("run_diagnostic", "decision_judge")])
async def test_scoring_entrypoints_export_real_judge_call(
        tmp_path, monkeypatch, example_config_path, otlp_wire, entry, body_name, capsys,
        export_fails):
    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    monkeypatch.setattr(spike, "load", lambda _: SimpleNamespace(
        llm=SimpleNamespace(timeout_seconds=5)))
    monkeypatch.setattr(spike, "model_endpoint", lambda path, purpose, model="": (
        "https://generated.invalid/v1", "generated-key", model))
    dispatches = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "generated-judge-a" and not body["stream"]
        dispatches.append(body)
        return generated_completion(body, {})

    async def body(directory, judges):
        await judges.call("generated-judge-a", "Generated diagnostic instruction", {})
        return {"status": "REPORTED"}

    monkeypatch.setattr(spike, body_name, body)
    otlp_wire.fail = export_fails
    original_build = structured.build_structured_model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(spike, "build_structured_model", lambda **kw: original_build(
            **kw, http_client=transport))
        report = await getattr(spike, entry)(SimpleNamespace(
            config=config, output=tmp_path / "output", rescore=tmp_path / "source",
            decision_judge=tmp_path / "source", judge_a="generated-judge-a",
            judge_b="generated-judge-b"))
    if export_fails:
        assert otlp_wire.payloads and report["evidence"]["status"] == "ERROR"
        assert dispatches == []
        return
    assert report["evidence"]["status"] == "PASS" and len(dispatches) == 1
    assert any(attrs(s).get("gen_ai.operation.name") == "chat" for s in exported_spans(otlp_wire))
    assert "eval trace: " in capsys.readouterr().err


@pytest.mark.parametrize("entry", ["run", "run_rescore", "run_diagnostic"])
async def test_missing_sink_refuses_before_evidence_fd(tmp_path, monkeypatch, entry):
    def forbidden(*args, **kwargs):
        pytest.fail("evidence must not open before sink resolution")
    monkeypatch.setattr(spike, "RunEvidence", forbidden)
    with pytest.raises(EvidenceError, match="visibility unavailable"):
        await getattr(spike, entry)(SimpleNamespace(config=tmp_path / "missing.toml"))
