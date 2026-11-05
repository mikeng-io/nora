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
from nora.subject.eval_evidence import EvidenceError, RunEvidence
from nora.subject.sandbox import eval_host_lock


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    monkeypatch.setenv("NORA_EVAL_EXCHANGES", str(root))
    from nora.subject import sandbox

    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    return root


def rows(evidence):
    return [json.loads(line) for line in (evidence.path / "events.jsonl").read_text().splitlines()]


def capture(lock_fd):
    backbone._prefill_capture()
    return RunEvidence(run_id="generated-run", metadata={"corpus": "generated"}, lock_fd=lock_fd)


async def model_round_trip(requests):
    @tool
    def next_number(value: int) -> str:
        """Return the next integer.

        Args:
            value: Integer to increment.
        """
        return str(value + 1)

    async def respond(request):
        body = json.loads(request.content)
        from nora.subject.eval_evidence import current

        if evidence := current():
            assert any(r["kind"] == "event" and r["name"] == "nora.model.request"
                       and json.loads(r["attributes"]["nora.model.request.body"]) == body
                       for r in rows(evidence))
        requests.append(body)
        assert body["model"] == "generated-model"
        assert body["tools"][0]["function"]["name"] == "next_number"
        if len(requests) == 1:
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_generated",
                     "type": "function", "function": {"name": "next_number",
                                                        "arguments": '{"value":5}'}}]}
            stop = "tool_calls"
        else:
            assert len(requests) == 2
            assert any(m.get("role") == "tool" and m.get("content") == "6"
                       for m in body["messages"])
            delta, stop = {"role": "assistant", "content": "6"}, "stop"
        chunks = [
            {"id": "generated", "object": "chat.completion.chunk", "model": "generated-model",
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "generated", "object": "chat.completion.chunk", "model": "generated-model",
             "choices": [{"index": 0, "delta": {}, "finish_reason": stop}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}},
        ]
        data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = factory.create_client(base_url="https://generated.invalid/v1",
                                       api_key="generated-private-auth-value",
                                       http_client=transport)
        model = factory.create_model_provider(model="generated-model", max_tokens=100,
                                              temperature=0, client=client, tool_choice="auto")
        agent = factory.create_browse_agent(
            model=model, system_prompt="Increment the supplied integer.",
            tools=[next_number], trace_attributes={},
            retry=factory.build_retry_policy(timeout_seconds=5))
        result = await factory.invoke(agent, "5", invocation_state={}, limits=factory.build_limits(
            turns=4, output_tokens=200, total_tokens=1000))
        await client.close()
        assert "6" in str(result)


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
    assert [json.loads(e["attributes"]["nora.model.request.body"]) for e in saved] == requests
    assert any(r["kind"] == "case_result" and
               r["result"]["environment_state"][0]["state"] == 1 for r in data)
    assert any(r["kind"] == "corpus_report" and r["report"]["cases"] for r in data)
    assert any(r["kind"] == "log" and r["message"] == "generated warning"
               and r["severity_number"] == 13 for r in data)
    surfaces = [json.loads(e["attributes"]["nora.tools.surface"])
                for r in data if r["kind"] == "span_finished" for e in r["events"]
                if e["name"] == "nora.tools.surface"]
    assert surfaces == [{"admitted": ["later_tool", "next_number"],
                         "resident": ["next_number"], "deferred": ["later_tool"],
                         "platform": "signal", "grant": None}]
    case = next(r for r in data if r["kind"] == "case_started")
    assert all(r["case_id"] == case["case_id"] for r in data
               if r["kind"] in ("case_result", "case_finished"))
    assert data[-1]["kind"] == "run_finished" and data[-1]["exit_code"] == 0
    assert stat.S_IMODE(evidence.path.stat().st_mode) == 0o700
    assert stat.S_IMODE((evidence.path / "events.jsonl").stat().st_mode) == 0o600
    assert "generated-private-auth-value" not in (evidence.path / "events.jsonl").read_text()


def test_started_case_and_failure_survive_interrupt_and_lock_releases(evidence_root):
    with pytest.raises(SystemExit), eval_host_lock() as fd:
        evidence = capture(fd)
        with evidence.capture(trace.get_tracer_provider()), evidence.case(
                "interrupted", input="generated", metadata={}):
            raise SystemExit(143)
    data = rows(evidence)
    assert any(r["kind"] == "case_started" for r in data)
    assert data[-1]["kind"] == "run_failed" and data[-1]["exit_code"] == 143
    assert not any(r["kind"] == "run_finished" for r in data)
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


def test_cli_capture_failure_refuses_before_execution(evidence_root, monkeypatch):
    reached = []
    def disk_full(*_):
        raise OSError("generated full disk")
    monkeypatch.setattr(os, "write", disk_full)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: reached.append(True) or 0)
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection"])
    assert cli.main() == 2
    assert reached == []


@pytest.mark.parametrize("failure", ["root_is_file", "unreadable_config"])
def test_cli_preparation_oserror_is_a_clean_refusal(evidence_root, monkeypatch, capsys, failure):
    reached = []
    if failure == "root_is_file":
        evidence_root.write_text("generated regular file")
    else:
        def denied(*_):
            raise PermissionError("generated unreadable config")
        monkeypatch.setattr(cli, "fingerprint", denied)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: reached.append(True) or 0)
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection"])
    assert cli.main() == 2
    assert reached == []
    assert "refusing to run: cannot prepare private eval evidence" in capsys.readouterr().err


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
    assert rows(evidence)[-1]["kind"] == "run_finished"


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
