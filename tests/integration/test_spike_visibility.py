"""The operator's memory-graph entrypoints export fresh generated SDK calls."""
from __future__ import annotations

import hashlib
import json
from contextlib import suppress
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
from tests.integration.test_grading_contract import (
    attach_archive_contract,
    attach_replay_contract,
    completion,
)
from tests.unit.test_memory_graph_spike import generated_replay_environment
from tests.unit.test_replay_scoring import generated_completion


@pytest.fixture
def otlp_wire(monkeypatch):
    wire = SimpleNamespace(payloads=[], fail=False, fail_on_root=False)

    def rejects(data):
        request = ExportTraceServiceRequest.FromString(data)
        root = any(s.name == "eval.run" for r in request.resource_spans
                   for scope in r.scope_spans for s in scope.spans)
        return wire.fail or (wire.fail_on_root and root)

    def send(session, url, **kwargs):
        import requests
        assert url == "http://127.0.0.1:14318/v1/traces"
        wire.payloads.append(kwargs["data"])
        response = requests.Response()
        response.status_code = 400 if rejects(kwargs["data"]) else 200
        response._content = b""
        return response

    def urllib_send(pool, method, url, **kwargs):
        from urllib3.response import HTTPResponse
        assert method == "POST" and url == "http://127.0.0.1:14318/v1/traces"
        wire.payloads.append(kwargs["body"])
        return HTTPResponse(body=b"", status=400 if rejects(kwargs["body"]) else 200)

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
    from tests.integration.test_eval_observability import model_round_trip
    real_scoring = spike.smoke_fact_support
    env = generated_replay_environment(tmp_path, monkeypatch)
    args, runner = env.args, env.runner
    args.smoke = True
    args.stop_after_tokens = 200  # eight generated SDK requests must reach the exporter
    env.cfg.llm.timeout_seconds = 5
    configured_sink(example_config_path, args.config)
    # Reuse the existing generated snapshot boundaries, preserving the real
    # _run selection/inspection/writer/scoring loop and both real fact judges.
    monkeypatch.setattr(spike, "smoke_fact_support", real_scoring)
    monkeypatch.setattr(spike, "model_endpoint", lambda path, purpose, model="": (
        "https://generated.invalid/v1", "generated-private-auth-value", model))
    runner.inspect.return_value = {"system": "Increment the supplied integer.", "agenda": "5 "}
    attach_replay_contract(args, tmp_path, runner.inspect.return_value, wakes=[
        {"id": 1, "at_ms": 1, "strata": ["text"]},
        {"id": 2, "at_ms": 2, "strata": ["attachment"]}])
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
        assert body["messages"][0]["content"] == "FROZEN_COVERAGE"
        dispatches.append(body["model"])
        response = completion(request, {"coverage": [], "diagnostic_misses": []})
        response.headers.update({"x-omniroute-model": body["model"],
                                 "x-omniroute-provider": "generated-judge-vendor"})
        return response

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
    assert json.loads((args.output / "report.json").read_text()) == report
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
    frozen = next(e for e in events if e["kind"] == "grading_contract")
    assert frozen["text"] == args.grading_contract.read_text()
    assert frozen["sha256"] == hashlib.sha256(frozen["text"].encode()).hexdigest()
    assert any(e.name == "nora.eval.grading_contract" for span in spans for e in span.events)
    starts = [e for e in events if e["kind"] == "case_started"]
    cohort = spike.fingerprint(args.output / "sample.json")
    assert all(e["metadata"]["frozen_sample_sha256"] == cohort for e in starts)
    assert all("sample_sha256" not in e["metadata"] for e in starts)
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
    # Source provenance belongs to the executing checkout, even when launched
    # from a private output directory with no repository or lockfile.
    from pathlib import Path
    source_head = spike.revision(Path(spike.__file__).resolve().parents[1])["head"]  # noqa: ASYNC240
    monkeypatch.chdir(tmp_path)
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

    async def body(directory, judges, **kwargs):
        if body_name == "rescore_facts":
            assert kwargs["contract"].receipt()["entrypoint"] == "rescore"
        await judges.call("generated-judge-a", "Generated diagnostic instruction", {})
        return {"status": "REPORTED"}

    monkeypatch.setattr(spike, body_name, body)
    otlp_wire.fail = export_fails
    original_build = structured.build_structured_model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(spike, "build_structured_model", lambda **kw: original_build(
            **kw, http_client=transport))
        args = SimpleNamespace(
            config=config, output=tmp_path / "output", rescore=tmp_path / "source",
            decision_judge=tmp_path / "source", judge_a="generated-judge-a",
            judge_b="generated-judge-b")
        attach_archive_contract(args, tmp_path,
                                "rescore" if entry == "run_rescore" else "diagnostic")
        report = await getattr(spike, entry)(args)
    if export_fails:
        assert otlp_wire.payloads and report["evidence"]["status"] == "ERROR"
        assert dispatches == []
        return
    assert report["evidence"]["status"] == "PASS" and len(dispatches) == 1
    events = read_events(next((tmp_path / "output").glob("*-*")))
    metadata = next(e["metadata"] for e in events if e["kind"] == "run_started")
    assert source_head and metadata["head"] == source_head
    assert metadata["config_sha256"] == spike.fingerprint(config)
    assert metadata["source_locator_sha256"]
    assert any(attrs(s).get("gen_ai.operation.name") == "chat" for s in exported_spans(otlp_wire))
    assert "eval trace: " in capsys.readouterr().err


@pytest.mark.parametrize("entry,body_name", [
    ("run_rescore", "rescore_facts"), ("run_diagnostic", "decision_judge"),
])
@pytest.mark.parametrize("outcome", ["complete", "cutoff", "unknown_usage", "body_error"])
async def test_scoring_entrypoints_share_the_measured_budget_and_persist_it(
        tmp_path, monkeypatch, example_config_path, otlp_wire, entry, body_name, outcome):
    from nora.domain.run_budget import RunBudget, active_run_budget

    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    monkeypatch.setattr(spike, "load", lambda _: SimpleNamespace(
        llm=SimpleNamespace(timeout_seconds=5)))
    monkeypatch.setattr(spike, "model_endpoint", lambda path, purpose, model="": (
        "https://generated.invalid/v1", "generated-key", model))
    dispatches = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == ["generated-a", "generated-b", "generated-a"][len(dispatches)]
        assert body["messages"][0]["content"] == "Generated score instruction"
        assert not body["stream"]
        dispatches.append(body)
        response = completion(request, {"generated": len(dispatches)})
        if outcome == "unknown_usage" and len(dispatches) == 2:
            data = response.json()
            data.pop("usage")
            response = httpx.Response(200, json=data)
        return response

    outer = RunBudget(max_requests=10)

    async def body(directory, judges, **kwargs):
        for index, model in enumerate(["generated-a", "generated-b", "generated-a"]):
            if index == 1 and outcome == "body_error":
                raise ValueError("generated scoring failure after first dispatch")
            # The real scorer carries on after individual judge failures; this
            # must not clear the terminal budget or admit the later call.
            with suppress(Exception):
                await judges.call(model, "Generated score instruction", {"attempt": index})
        return {"status": "REPORTED"}

    monkeypatch.setattr(spike, body_name, body)
    original_build = structured.build_structured_model
    monkeypatch.setattr(spike, "build_structured_model", lambda **kw: original_build(
        **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    args = SimpleNamespace(
        config=config, output=tmp_path / "output", rescore=tmp_path / "source",
        decision_judge=tmp_path / "source", judge_a="generated-a", judge_b="generated-b",
        stop_after_tokens=20 if outcome == "cutoff" else 100)
    attach_archive_contract(args, tmp_path,
                            "rescore" if entry == "run_rescore" else "diagnostic")
    token = active_run_budget.set(outer)
    try:
        report = await getattr(spike, entry)(args)
        assert active_run_budget.get() is outer
    finally:
        active_run_budget.reset(token)
    assert outer.requests == 0
    expected = {"complete": 3, "cutoff": 2, "unknown_usage": 2, "body_error": 1}[outcome]
    assert len(dispatches) == expected
    budget = report["run_budget"]
    assert budget["requests_used"] == expected
    known = 1 if outcome == "unknown_usage" else expected
    assert budget["input_tokens_used"] == known * 10
    assert budget["output_tokens_used"] == known * 5
    assert bool(budget["spent_reason"]) == (outcome in {"cutoff", "unknown_usage"})
    assert report["status"] == ("REPORTED" if outcome == "complete" else "ERROR")
    assert spike.report_exit(report) == (0 if outcome == "complete" else 2)
    assert json.loads((args.output / "run_budget.json").read_text()) == budget
    assert json.loads((args.output / "report.json").read_text()) == report
    events = read_events(next(args.output.glob("*-*")))
    final = [e for e in events if e["kind"] == "run_budget_final"]
    assert len(final) == 1
    assert all(final[0][key] == value for key, value in budget.items())
    assert any(e.name == "nora.eval.run_budget_final"
               for span in exported_spans(otlp_wire) for e in span.events)


@pytest.mark.parametrize("entry", ["run", "run_rescore", "run_diagnostic"])
async def test_missing_sink_refuses_before_evidence_fd(tmp_path, monkeypatch, entry):
    def forbidden(*args, **kwargs):
        pytest.fail("evidence must not open before sink resolution")
    monkeypatch.setattr(spike, "RunEvidence", forbidden)
    args = SimpleNamespace(config=tmp_path / "missing.toml", judge_a="generated-a",
                           judge_b="generated-b", rescore=tmp_path / "source",
                           decision_judge=tmp_path / "source")
    if entry == "run":
        attach_replay_contract(args, tmp_path)
    else:
        attach_archive_contract(args, tmp_path,
                                "rescore" if entry == "run_rescore" else "diagnostic")
    with pytest.raises(EvidenceError, match="visibility unavailable"):
        await getattr(spike, entry)(args)


@pytest.mark.parametrize("failure", ["receipt", "root_export"])
async def test_late_capture_failure_preserves_completed_spike_report(
        tmp_path, monkeypatch, example_config_path, otlp_wire, failure):
    from copy import deepcopy

    from nora.subject.eval_evidence import current
    from tests.integration.test_eval_observability import model_round_trip

    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    expected = {"status": "PASS", "sample_sha256": "generated-cohort", "seed": 17,
                "scores": [{"case": "generated", "status": "PASS", "score": 1}],
                "cases": ["generated-completed"], "strata": {"text": 1}}
    completed, dispatched = [], []

    async def completed_body(args):
        evidence = current()
        with evidence.case("generated-completed", input="5", metadata={}):
            await model_round_trip(dispatched)
        completed.append(True)
        if failure == "receipt":
            evidence.record("judge_error", cause="generated_late_failure")
        else:
            otlp_wire.fail_on_root = True  # preflight + model/case exports already succeeded
        return deepcopy(expected)

    monkeypatch.setattr(spike, "_run", completed_body)
    output = tmp_path / "output"
    args = SimpleNamespace(config=config, output=output)
    attach_replay_contract(args, tmp_path)
    result = await spike.run(args)
    assert completed == [True] and len(dispatched) == 2
    assert result["status"] == result["evidence"]["status"] == "ERROR"
    assert spike.report_exit(result) == 2
    assert {k: result[k] for k in expected if k != "status"} == {
        k: v for k, v in expected.items() if k != "status"}
    assert json.loads((output / "report.json").read_text()) == result
    events = read_events(next(output.glob("spike-*")))
    assert any(e["kind"] == "run_failed" for e in events)
    root = next(s for s in exported_spans(otlp_wire) if s.name == "eval.run")
    if failure == "receipt":
        assert attrs(root)["nora.eval.capture.status"] == "failed"
        assert any(e.name == "nora.eval.run_failed" for e in root.events)
    else:
        # This is a rejected wire payload, not a stored root or an acknowledged export.
        assert attrs(root)["nora.eval.export.status"] == "pending_root"
        assert not any(e["kind"] == "export_result" for e in events)


async def test_report_freeze_never_overwrites_existing_file(
        tmp_path, monkeypatch, example_config_path, otlp_wire):
    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    output = tmp_path / "output"

    async def body(args):
        spike.freeze(output / "report.json", {"retained": "generated-original"})
        return {"status": "PASS"}

    monkeypatch.setattr(spike, "_run", body)
    args = SimpleNamespace(config=config, output=output)
    attach_replay_contract(args, tmp_path)
    with pytest.raises(FileExistsError):
        await spike.run(args)
    assert json.loads((output / "report.json").read_text()) == {"retained": "generated-original"}
