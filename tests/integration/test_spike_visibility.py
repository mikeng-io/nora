"""The operator's memory-graph entrypoints export fresh generated SDK calls."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from nora.subject.eval_evidence import read_events
from scripts import memory_graph_spike as spike


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






