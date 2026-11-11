"""Persist the real eval/SDK trajectory using only a local synthetic model transport."""
from __future__ import annotations

import json
import logging
import os
import stat

import httpx
import pytest
from opentelemetry import trace
from strands import tool
from strands_evals import Case, Experiment

import evals.__main__ as cli
import evals.backbone as backbone
from nora.agent import factory
from nora.agent.surface import ToolScope, ToolShelf, ToolSurface
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
from tests.integration.test_spike_visibility import configured_sink
from tests.integration.test_spike_visibility import otlp_wire as otlp_wire


def test_real_case_request_tools_results_logs_and_report_are_durable(evidence_root, monkeypatch):
    requests = []

    async def drive(case, spec):
        shelf = ToolShelf()
        shelf.stock({"later_tool": object()})
        ToolSurface(scope=ToolScope(platform="signal"), tools=[], resident=("next_number",),
                    shelf=shelf, admitted=("next_number", "later_tool"), render=lambda *a: [])
        logging.getLogger("generated.eval").warning("generated warning")
        await model_round_trip(requests)
        return {"output": "6", "environment_state": [
            backbone.EnvironmentState(name=backbone.NORA_SAID, state=1),
        ]}

    monkeypatch.setattr(backbone, "_drive_uncaptured", drive)
    experiment = Experiment(cases=[Case(name="generated-case", input="5")],
                            evaluators=[backbone.AbsenceGrader()])
    monkeypatch.setattr(cli, "experiment_for", lambda *a, **k: (
        experiment, backbone.nora_task(backbone.Spec(source_url="", entry="tag"))))
    monkeypatch.setattr(cli, "census", lambda _: "generated census")
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()):
            cli._run_one("generated", source_url="", judge=None, sample=1)
            evidence.exit_code = 0
    data = rows(evidence)
    assert len(requests) == 2  # actual SDK formatting and tool execution ran
    dispatched = [r for r in data if r["kind"] == "event" and r["name"] == "nora.model.request"]
    assert [json.loads(r["attributes"]["nora.model.request.body"]) for r in dispatched] == requests
    saved = [e for r in data if r["kind"] == "span_finished" for e in r["events"]
             if e["name"] == "nora.model.request"]
    assert saved == []  # content belongs only to the private record
    assert any(r["kind"] == "case_result" and
               r["result"]["environment_state"][0]["state"] == 1 for r in data)
    assert any(r["kind"] == "corpus_report" and r["report"]["cases"] for r in data)
    assert any(r["kind"] == "log" and r["message"] == "generated warning"
               and r["severity_number"] == 13 for r in data)
    surfaces = [json.loads(r["attributes"]["nora.tools.surface"])
                for r in data if r["kind"] == "event" and r["name"] == "nora.tools.surface"]
    assert surfaces == [{"admitted": ["later_tool", "next_number"],
                         "resident": ["next_number"], "deferred": ["later_tool"],
                         "platform": "signal", "grant": None,
                         "deferred_schemas": {"later_tool": {
                             "status": "unavailable",
                             "note": "Deferred tool schema unavailable."}}}]
    case = next(r for r in data if r["kind"] == "case_started")
    assert all(r["case_id"] == case["case_id"] for r in data
               if r["kind"] in ("case_result", "case_finished"))
    assert next(r for r in data if r["kind"] == "run_finished")["exit_code"] == 0
    assert stat.S_IMODE(evidence.path.stat().st_mode) == 0o700
    assert stat.S_IMODE((evidence.path / "events.jsonl").stat().st_mode) == 0o600
    assert "generated-private-auth-value" not in (evidence.path / "events.jsonl").read_text()


def test_started_case_and_failure_survive_interrupt_and_lock_releases(evidence_root):
    with pytest.raises(SystemExit), eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()), evidence.case(
                "interrupted", input="generated", metadata={}):
            evidence.record("call_started", invocation_id="generated", attempt_id="generated:0:1")
            raise SystemExit(143)
    data = rows(evidence)
    assert any(r["kind"] == "case_started" for r in data)
    assert next(r for r in data if r["kind"] == "run_failed")["exit_code"] == 143
    assert not any(r["kind"] == "run_finished" for r in data)
    from nora.subject.eval_evidence import validate_events
    assert validate_events(data)["status"] == "ERROR"
    with eval_host_lock() as fd:
        assert os.fstat(fd).st_ino > 0


def test_unwritable_evidence_stops_before_work_and_never_finishes_ok(evidence_root, monkeypatch):
    work = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        def disk_full(*_):
            raise OSError("generated disk full")
        monkeypatch.setattr(os, "write", disk_full)
        with pytest.raises(EvidenceError), evidence.capture(trace.get_tracer_provider()):
            work.append("unrecorded work")
    assert work == [] and evidence.failure
    assert not rows(evidence)


def test_a_private_artifact_is_required_before_any_case(evidence_root):
    evidence_root.mkdir(mode=0o755)
    with eval_host_lock() as fd, pytest.raises(EvidenceError, match="0700"):
        capture(fd)
    assert not list(evidence_root.iterdir())


def test_mid_run_disk_failure_invalidates_later_work_and_releases_lock(evidence_root, monkeypatch):
    reached = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with pytest.raises(EvidenceError), evidence.capture(trace.get_tracer_provider()):
            with evidence.case("first", input="generated", metadata={}):
                evidence.result({"output": "saved"})
            original_write = os.write
            def disk_full(*args):
                if args[0] == evidence._fd:
                    raise OSError("generated full disk")
                return original_write(*args)
            monkeypatch.setattr(os, "write", disk_full)
            with evidence.case("second", input="generated", metadata={}):
                reached.append("unrecorded second case")
    assert reached == []
    data = rows(evidence)
    assert any(r["kind"] == "case_result" for r in data)
    assert not any(r["kind"] == "run_finished" for r in data)
    with eval_host_lock():
        pass


def test_cli_capture_failure_refuses_before_execution(
        evidence_root, monkeypatch, example_config_path, tmp_path, otlp_wire,
        generated_cli_contract):
    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    reached, writes = [], []
    def disk_full(*args):
        writes.append(args[0])
        raise OSError("generated full disk")
    monkeypatch.setattr(os, "write", disk_full)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: reached.append(True) or 0)
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection",
                                    "--deployment-config", str(config)])
    assert cli.main() == 2
    assert reached == [] and writes


@pytest.mark.parametrize("failure", ["root_is_file", "unreadable_config"])
def test_cli_preparation_oserror_is_a_clean_refusal(
        evidence_root, monkeypatch, capsys, failure, example_config_path, tmp_path, otlp_wire,
        generated_cli_contract):
    config = tmp_path / "generated.toml"
    configured_sink(example_config_path, config)
    injected = []
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection",
                                    "--deployment-config", str(config)])
    reached = []
    if failure == "root_is_file":
        evidence_root.write_text("generated regular file")
    else:
        def denied(*_):
            injected.append(True)
            raise PermissionError("generated unreadable config")
        monkeypatch.setattr(cli, "fingerprint", denied)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: reached.append(True) or 0)
    assert cli.main() == 2
    assert reached == []
    assert "refusing to run: cannot prepare private eval evidence" in capsys.readouterr().err
    if failure == "unreadable_config":
        assert injected


def test_no_private_capture_event_outside_eval(evidence_root):
    from nora.agent.telemetry import record_eval_model_request

    backbone._prefill_capture()
    exporter = backbone._prefill_capture()
    exporter.clear()
    with trace.get_tracer(__name__).start_as_current_span("ordinary-test"):
        record_eval_model_request({"model": "generated", "tools": []})
    spans = exporter.get_finished_spans()
    assert spans
    assert not any(e.name == "nora.model.request" for span in spans for e in span.events)


def test_default_evidence_is_mandatory_without_remote_export(evidence_root, monkeypatch, tmp_path):
    monkeypatch.delenv("NORA_EVAL_EXCHANGES")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "private-state"))
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()):
            evidence.exit_code = 0
    assert evidence.path.parent == tmp_path / "private-state/nora/evals"
    assert rows(evidence)[0]["kind"] == "run_started"
    assert any(r["kind"] == "run_finished" for r in rows(evidence))


@pytest.mark.asyncio
async def test_request_write_failure_prevents_model_dispatch(evidence_root, monkeypatch):
    requests = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with pytest.raises(EvidenceError), evidence.capture(trace.get_tracer_provider()):
            original_write = os.write
            def fail_request(fd, data):
                if fd == evidence._fd and b'nora.model.request' in bytes(data):
                    raise OSError("generated write refusal")
                return original_write(fd, data)
            monkeypatch.setattr(os, "write", fail_request)
            await model_round_trip(requests)
    assert requests == []
    assert any(r["kind"] == "run_started" for r in rows(evidence))
    assert not any(r["kind"] == "run_finished" for r in rows(evidence))


@pytest.mark.parametrize("authorized", [False, True])
def test_two_round_private_record_sdk_and_each_public_sink(
        evidence_root, monkeypatch, capsys, authorized):
    from io import StringIO

    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    from strands_evals.types.trace import Session

    from nora.subject.eval_evidence import read_events, validate_events

    sentinel = "GENERATED_PRIVATE_REPLAY_SENTINEL"
    requests = []
    external = StringIO()
    provider = backbone._prefill_capture()
    provider = trace.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=external)))
    log_output = StringIO()
    handler = logging.StreamHandler(log_output)
    logging.getLogger().addHandler(handler)

    async def drive(case, spec):
        logging.getLogger("generated.eval").warning(sentinel)
        await model_round_trip(requests, sentinel=sentinel, rounds=3)
        return {"output": "6", "environment_state": []}

    monkeypatch.setattr(backbone, "_drive_uncaptured", drive)
    task, traced = backbone.traced_nora_task(backbone.Spec(source_url="", entry="tag"))
    try:
        with eval_host_lock() as fd:
            evidence = capture(fd)
            from opentelemetry.sdk.trace.export import SpanExportResult

            from nora.agent.telemetry import evaluation_export
            monkeypatch.setattr(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export",
                lambda *args: SpanExportResult.SUCCESS)
            export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                "endpoint": "http://127.0.0.1:14318/v1/traces"}) if authorized else None
            with evidence.capture(provider, export=export):
                result = task(Case(name="generated-private", input="5", session_id="generated"))
        events = read_events(evidence.path)
        receipt = validate_events(events)
        assert receipt["status"] == "PASS", receipt
        assert len(requests) == 3
        assert sum(e["kind"] == "tool_finished" for e in events) == 2
        from scripts.memory_graph_spike import evidence_exchanges
        assert all(e["source_rows"] == {"1": {"text": "generated source"}}
                   for e in evidence_exchanges(events))
        assert isinstance(result["trajectory"], Session)
        assert sentinel in result["trajectory"].model_dump_json()
        assert sentinel in (evidence.path / "events.jsonl").read_text()
        assert sentinel not in external.getvalue()  # non-private OTel exporter
        assert sentinel not in log_output.getvalue()  # non-private log exporter
        assert sentinel not in capsys.readouterr().out  # stdout
        assert sentinel not in json.dumps(receipt)  # public receipt
        # These are the workflow's actual upload roots. Private capture is
        # outside all of them; only the content-free receipt is uploadable.
        from pathlib import Path

        import yaml
        repo = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((repo / ".github/workflows/ci.yml").read_text())
        uploads = [step["with"]["path"] for job in workflow["jobs"].values()
                   for step in job.get("steps", [])
                   if "actions/upload-artifact@" in step.get("uses", "")]
        assert uploads
        for patterns in uploads:
            for pattern in patterns.splitlines():
                root = repo / pattern.split("*")[0]
                assert evidence.path != root and root not in evidence.path.parents
                for artifact in repo.glob(pattern):
                    if artifact.is_file():
                        assert sentinel.encode() not in artifact.read_bytes()  # CI upload sink
        responses = [e for e in events if e["kind"] == "transport_response"]
        assert responses and all(e["headers"]["x-omniroute-model"] == "generated-served"
                                 for e in responses)
        assert any(e["response"].get("usage", {}).get("completion_tokens_details", {}).get(
            "reasoning_tokens") == 2 for e in responses if e["response"].get("usage"))
        assert all(e["reasoning_availability"] == "observed" for e in events
                   if e["kind"] == "transport_finished")
        # Dropping any persisted event invalidates the record, even if its
        # matching lifecycle partner was also omitted by the producer.
        path = evidence.path / "events.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[:5] + lines[6:]) + "\n")
        with pytest.raises(EvidenceError, match="dropped_event"):
            read_events(evidence.path)
    finally:
        logging.getLogger().removeHandler(handler)
        del traced


def test_production_composition_has_no_capture_hooks_or_private_events():
    from nora.agent.replay_capture import hooks, record
    from nora.obs.tracing import eval_capture_enabled
    assert not eval_capture_enabled()
    assert hooks() == []
    record("generated_noop", content="GENERATED_PRODUCTION_SENTINEL")


def test_tag_and_cognition_factories_leave_capture_disabled(monkeypatch):
    from nora.agent.replay_capture import ReplayHooks
    seen = []
    monkeypatch.setattr(factory, "Agent", lambda **kwargs: seen.append(kwargs) or kwargs)
    guard = object()
    retry = factory.build_retry_policy(timeout_seconds=5)
    common = {"model": object(), "system_prompt": "generated", "tools": [], "guard": guard,
              "trace_attributes": {}, "retry": retry}
    factory.create_agent(**common, pre_fill=[])
    factory.create_dream_agent(**common)  # Cognition's production constructor
    assert len(seen) == 2
    assert all(row["hooks"] == [guard] for row in seen)
    assert not any(isinstance(h, ReplayHooks) for row in seen for h in row["hooks"])


async def test_structured_call_captures_beta_parse(evidence_root):
    from pydantic import BaseModel

    from nora.agent.structured import ask_structured
    from nora.subject.eval_evidence import read_events, validate_events

    class GeneratedResult(BaseModel):
        value: int

    requests = []

    async def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "generated-model"
        requests.append(body)
        if "response_format" in body:
            assert len(requests) == 2
            assert body["response_format"]["type"] == "json_schema"
            assert body["stream"] is False
            message = {"role": "assistant", "content": '{"value":6}'}
            finish = "stop"
        else:
            assert len(requests) == 1
            assert body["stream"] is False
            assert len(body["tools"]) == 1
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "generated-schema", "type": "function", "function": {
                    "name": body["tools"][0]["function"]["name"], "arguments": '{"value":6}'}}]}
            finish = "tool_calls"
        return httpx.Response(200, headers={"x-omniroute-model": "generated-structured",
                                           "x-omniroute-provider": "generated-provider"}, json={
            "id": "generated", "object": "chat.completion", "created": 0,
            "model": "generated-model", "choices": [{"index": 0, "finish_reason": finish,
                "message": {**message, "reasoning": "Generated structured reasoning"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = factory.create_client(base_url="https://generated.invalid/v1",
                                       api_key="generated", http_client=transport)
        model = factory.create_model_provider(model="generated-model", max_tokens=100,
                                              temperature=0, client=client, tool_choice="auto")
        model.update_config(stream=False)
        with eval_host_lock() as fd:
            evidence = capture(fd)
            with evidence.capture(trace.get_tracer_provider()):
                result = await ask_structured(
                    model=model, prompt="Generated question", schema=GeneratedResult,
                    system_prompt="Generated instruction", timeout_seconds=5)
                parsed = [e async for e in model.structured_output(
                    GeneratedResult, [{"role": "user", "content": [{"text": "Generated parse"}]}])]
                assert parsed[-1]["output"].value == 6
        await client.close()
    assert json.loads(result.content) == {"value": 6}
    events = read_events(evidence.path)
    responses = [e for e in events if e["kind"] == "transport_response"]
    assert len(responses) == len(requests) == 2
    assert responses[-1]["response"]["choices"][0]["message"]["parsed"] == {"value": 6}
    assert all(e["headers"]["x-omniroute-model"] == "generated-structured" for e in responses)
    assert validate_events(events)["status"] == "PASS"


async def test_review_judge_verdict_keeps_run_complete(evidence_root, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nora.subject.eval_evidence import read_events, validate_events
    from scripts.memory_graph_spike import Judges

    monkeypatch.setattr("scripts.memory_graph_spike.model_endpoint",
                        lambda path, purpose, model: ("", "", model))
    judges = Judges(SimpleNamespace(), ["generated-judge"], tmp_path / "generated.toml")

    async def borderline(model, instruction, payload):
        assert model == "generated-judge" and payload == {"generated": "borderline"}
        return json.dumps([{"criterion": "no_overreach_or_fabrication", "score": 0.5,
                            "confidence": 0.5, "rationale": "Generated ambiguity"}])

    monkeypatch.setattr(judges, "call", borderline)
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()):
            assert await judges.score({"generated": "borderline"}) == ["ERROR"]
    events = read_events(evidence.path)
    verdicts = [e for e in events if e["kind"] == "judge_verdict"]
    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "REVIEW"
    assert verdicts[0]["reason"] == "Generated ambiguity"
    assert validate_events(events)["status"] == "PASS"


async def test_strip_detector_probes_are_not_dispatched_requests(evidence_root):
    from nora.subject.eval_evidence import read_events
    requests = []
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()):
            await model_round_trip(requests, sentinel="Generated reasoning", reasoning_prefill=True)
    events = read_events(evidence.path)
    recorded = [e for e in events if e.get("name") == "nora.model.request"]
    assert len(recorded) == len(requests) == 2
    assert any(m.get("reasoning") == "Generated carried reasoning" for m in requests[0]["messages"])
    assert any(e["kind"] == "reasoning_carry" for e in events)


@pytest.mark.parametrize("name", ["nora.proposal.sources", "nora.tools.surface",
    "nora.session.instructions", "nora.session.started", "nora.session.terminal"])
def test_private_eval_events_never_attach_bodies_to_spans(evidence_root, name):
    from nora.obs.tracing import record_eval_event
    from nora.subject.eval_evidence import read_events
    sentinel = "GENERATED_SPAN_ABSENCE_SENTINEL"
    with eval_host_lock() as fd:
        evidence = capture(fd)
        with (evidence.capture(trace.get_tracer_provider()),
              trace.get_tracer(__name__).start_as_current_span("generated-event-sink")):
            record_eval_event(name, {"body": sentinel})
            # Session pairing matters only to the validator; keep both ends present.
            if name in {"nora.session.started", "nora.session.terminal"}:
                other = ("nora.session.terminal" if name.endswith("started")
                         else "nora.session.started")
                record_eval_event(other, {"body": "generated partner"})
    events = read_events(evidence.path)
    assert any(e.get("name") == name and e["attributes"]["body"] == sentinel for e in events)
    spans = [e for e in events if e["kind"] == "span_finished"]
    assert any(e["name"] == "generated-event-sink" for e in spans)
    assert not any(event["name"] == name for span in spans for event in span["events"])
    assert sentinel not in json.dumps(spans)


@pytest.mark.parametrize("cancel_before_first", [False, True])
async def test_cancelled_model_pass_has_its_own_complete_lifecycle(
        evidence_root, cancel_before_first):
    from strands.hooks import BeforeModelCallEvent, HookProvider

    from nora.agent.guards import ReplyGuard
    from nora.obs.metrics import Metrics
    from nora.subject.eval_evidence import read_events, validate_events

    class DenyModel(HookProvider):
        def register_hooks(self, registry, **kwargs):
            registry.add_callback(BeforeModelCallEvent, self.deny)

        def deny(self, event):
            event.cancel = "generated_policy_denial"

    executions = []
    requests = []

    @tool
    def generated_finish() -> str:
        """Finish the generated task."""
        executions.append("finished")
        return "generated_complete"

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert not cancel_before_first, "a denied pass must never reach the transport"
        assert len(requests) == 1, "the terminal tool must suppress the continuation request"
        assert body["tools"][0]["function"]["name"] == "generated_finish"
        return httpx.Response(200, json={
            "id": "generated", "created": 0, "object": "chat.completion",
            "model": "generated-model", "choices": [{"index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "generated-terminal", "type": "function", "function": {
                        "name": "generated_finish", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = factory.create_client(base_url="https://generated.invalid/v1",
                                       api_key="generated", http_client=transport)
        model = factory.create_model_provider(model="generated-model", max_tokens=100,
                                              temperature=0, client=client, tool_choice="auto")
        model.update_config(stream=False)
        guard = ReplyGuard(conversation_id="generated", budgets={"generated_finish": 1},
                           terminal={"generated_finish"}, metrics=Metrics())
        with eval_host_lock() as fd:
            evidence = capture(fd)
            with evidence.capture(trace.get_tracer_provider()):
                agent = factory.create_agent(
                    model=model, system_prompt="Generated task", tools=[generated_finish],
                    pre_fill=[], guard=guard,
                    tool_hooks=(DenyModel(),) if cancel_before_first else (),
                    trace_attributes={}, retry=factory.build_retry_policy(timeout_seconds=5))
                result = await factory.invoke(agent, "Generated input", invocation_state={},
                                              limits=factory.build_limits(
                                                  turns=4, output_tokens=200, total_tokens=1000))
                assert result.stop_reason == "end_turn"
        await client.close()
    assert len(requests) == (0 if cancel_before_first else 1)
    assert executions == ([] if cancel_before_first else ["finished"])
    events = read_events(evidence.path)
    assert validate_events(events)["status"] == "PASS"
    cancelled = next(e for e in events if e["kind"] == "call_cancelled")
    attempt = cancelled["attempt_id"]
    starts = [e for e in events if e["kind"] == "call_started" and e["attempt_id"] == attempt]
    ends = [e for e in events if e["kind"] == "call_finished" and e["attempt_id"] == attempt]
    assert len(starts) == len(ends) == 1
    assert starts[0]["cancelled"]
    assert starts[0]["event_id"] != ends[0]["event_id"]
    assert starts[0]["call_id"] == ends[0]["call_id"]
    assert not any(e["kind"].startswith("transport_") and e["attempt_id"] == attempt
                   for e in events)
    assert sum(e["kind"] == "transport_started" for e in events) == len(requests)
    # The preceding completed call, when present, cannot vouch for this pass.
    attempts = {e["attempt_id"] for e in events if e["kind"] == "call_started"}
    assert len(attempts) == len(requests) + 1


