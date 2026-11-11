"""The operator's memory-graph entrypoints export fresh generated SDK calls."""
from __future__ import annotations

import json
from contextlib import suppress
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from nora.agent import structured
from nora.subject.eval_evidence import EvidenceError, read_events
from scripts import memory_graph_spike as spike
from tests.integration.test_grading_contract import (
    attach_archive_contract,
    attach_replay_contract,
    completion,
)
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


def test_budget_file_failure_retains_terminal_trace_counters(tmp_path):
    from tempfile import TemporaryFile

    from evals.backbone import _prefill_capture
    from nora.domain.run_budget import active_run_budget
    from nora.subject.eval_evidence import RunEvidence

    output = tmp_path / "generated-budget"
    output.mkdir(mode=0o700)
    (output / "run_budget.json").write_text("existing evidence")
    report = {"status": "ERROR"}
    previous = active_run_budget.get()
    _prefill_capture()
    with TemporaryFile() as lock:
        evidence = RunEvidence(run_id="generated-budget", metadata={}, lock_fd=lock.fileno(),
                               private_root=output)
        with (
            pytest.raises(FileExistsError),
            evidence.capture(trace.get_tracer_provider()),
            spike.measured_run_budget(SimpleNamespace(stop_after_tokens=100),
                                      evidence, output, report),
        ):
            budget = active_run_budget.get()
            reservation = budget.admit(input_bytes=10, declared_output_tokens=20)
            budget.reconcile(input_tokens=3, output_tokens=2)
            budget.release(reservation)
        rows = read_events(evidence.path)
    final = [e for e in rows if e["kind"] == "run_budget_final"]
    assert len(final) == 1
    assert final[0]["requests_used"] == 1 and final[0]["input_tokens_used"] == 3
    assert any(e["kind"] == "run_failed" for e in rows)
    assert active_run_budget.get() is previous
    assert (output / "run_budget.json").read_text() == "existing evidence"


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




