"""Generated SDK gate replay through production composition and owned HTTP."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest
from aiohttp import web
from opentelemetry import trace
from strands_evals import Case, Experiment

from evals import backbone
from evals.backbone import MeetsGrader, Spec, nora_task, state_of
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject import effect_capture as effects_mod
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices
from nora.subject.sandbox import eval_host_lock
from scripts.memory_graph_spike import measured_run_budget

NOW = 2_000_000_000_000
IDENTITY = '[persons.miles_novak]\nname="Miles"\n[[persons.miles_novak.signal]]\nphone="+852111"\n'


@pytest.fixture(autouse=True)
def sdk_evidence(tmp_path):
    backbone._prefill_capture()
    budget = {}
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="generated-sdk", lock_fd=lock_fd,
            private_root=tmp_path / "capture", metadata={"behavior": "UNMEASURED"})
        try:
            with evidence.capture(trace.get_tracer_provider()), measured_run_budget(
                SimpleNamespace(stop_after_tokens=20000), evidence, tmp_path, budget,
                max_requests=20, max_output_tokens=400000,
            ):
                yield evidence
        except EvidenceError as exc:
            assert str(exc) == "incomplete_record"
            events = read_events(evidence.path)
            assert any(e["kind"] == "expected_invalid_capture" for e in events)
            assert validate_events(events)["causes"] == ["capture_interrupted"]
        else:
            assert validate_events(read_events(evidence.path))["status"] == "PASS"


def recording(cfg, path="/v1/chat/completions", *, model_responses=None):
    bindings = {"llm.base_url": "model", "honcho.base_url": "memory",
                "platforms.signal.base_url": "bridge"}
    bindings.update({f"providers.{name}.base_url": "model" for name in cfg.providers})
    model = cfg.endpoint("relevance_gate", cfg.relevance_gate.model)[2]
    data = {"version": 1, "bindings": bindings, "services": {
        "bridge": [{"method": "GET", "path": "/v1/self", "response": {"status": 200,
                     "json": {"primary": "generated-self", "aliases": []}}}],
        "memory": [
            {"method": "POST", "path": "/v3/workspaces", "request_json": {
                "id": cfg.honcho.workspace_id}, "response": {"status": 200, "json": {
                    "id": cfg.honcho.workspace_id}}},
            {"method": "POST", "path": f"/v3/workspaces/{cfg.honcho.workspace_id}/peers",
             "request_json": {"id": cfg.honcho.ai_peer}, "response": {"status": 200,
             "json": {"id": cfg.honcho.ai_peer}}},
        ], "model": [{
            "method": "POST", "path": path, "response": {"status": 200, "json": {
                "id": "generated-completion", "model": model, "created": 1,
                "object": "chat.completion", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": '{"verdict":"go"}'},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }},
        }],
    }}
    if model_responses is not None:
        data["services"]["model"] = model_responses
    return RecordedServices(data)


@pytest.mark.parametrize("broken", [False, True, "effects"])
async def test_sdk_cases_use_real_subjects_in_distinct_owned_services(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, broken, sdk_evidence,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    engine = create_sql_engine(clean_pg_url)
    try:
        await SqlRecordStore(engine).append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", NOW - 1000,
            "Generated earlier conversation", {}), person_id="miles_novak")
    finally:
        await engine.dispose()
    overrides = {"platforms.signal.enabled": True, "platforms.signal.mode": "bridge"}
    cfg = build_config(SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "declared",
                                  overrides=overrides))
    received, subjects, requests, windows = [], [], [], []
    coverage = IsolatedServices._coverage
    obtain = backbone.obtain
    since = RunEvidence.since
    reader, reads = effects_mod.read_effects, []

    async def read(*args, **kwargs):
        effects = await reader(*args, **kwargs)
        reads.append(1)
        if broken == "effects" and len(reads) == 2:
            effects.pop("tools")
        return effects

    monkeypatch.setattr(effects_mod, "read_effects", read)

    def window(self, cursor):
        events = since(self, cursor)
        windows.append([event for event in events if event.get("name") == "nora.model.request"])
        return events

    monkeypatch.setattr(RunEvidence, "since", window)

    def observe(self, service):
        original = coverage(self, service)

        @web.middleware
        async def capture(request, handler):
            received.append((request.host, request.method, request.raw_path))
            if request.path.endswith("chat/completions"):
                requests.append(await request.json())
            return await original(request, handler)

        return capture

    async def compose(spec):
        subject = await obtain(spec)
        subjects.append(subject)
        return subject

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    monkeypatch.setattr(backbone, "obtain", compose)
    cases = [Case(name=f"generated-{index}", input="Generated question", metadata={
        "expect": "go", "snapshot": {"platform": "signal", "conversation_id": "g1",
                                      "before_ms": NOW}}) for index in range(2)]
    spec = Spec(source_url=clean_pg_url, entry="gate", identity=IDENTITY,
                policy=default_policy(("signal", "g1")), overrides=overrides,
                services={case.name: recording(
                    cfg, path="/wrong" if broken is True and i == 1 else "/v1/chat/completions")
                    for i, case in enumerate(cases)})
    report = await Experiment(cases=cases, evaluators=[MeetsGrader()]).run_evaluations_async(
        task=nora_task(spec), max_workers=1)
    errors = [state_of_from_row(row, backbone.NORA_ERROR) for row in report.cases]
    assert errors[0] is None, (errors, received)
    cause = ("canonical_lifecycle_unavailable" if broken == "effects"
             else "external_response_not_recorded")
    assert cause in (errors[1] or "") if broken else errors[1] is None
    assert [rows[0].label for rows in report.detailed_results] == [
        "PASS", "ERROR" if broken else "PASS"]
    assert state_of_from_row(report.cases[0], backbone.NORA_CALLS) == 1
    assert [len(window) for window in windows] == [1, 1]
    import json

    assert [json.loads(event["attributes"]["nora.model.request.body"])
            for window in windows for event in window] == requests
    from nora.domain.run_budget import active_run_budget

    assert active_run_budget.get().requests == len(requests) == 2
    assert "Generated earlier conversation" in str(requests[0]["messages"])
    assert "Generated question" in str(requests[0]["messages"])
    expected_model = cfg.endpoint("relevance_gate", cfg.relevance_gate.model)[2]
    assert all(request["model"] == expected_model for request in requests)
    assert len(subjects) == 2
    assert subjects[0].environment is not subjects[1].environment
    assert len({host for host, _, path in received if path.endswith("chat/completions")}) == 2
    assert all(not subject.environment.active for subject in subjects)
    assert all(not subject.deps.background._tasks for subject in subjects)
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
    assert [state_of_from_row(row, backbone.NORA_BEHAVIOR) for row in report.cases] == [
        "UNMEASURED", "UNMEASURED"]
    if broken:
        sdk_evidence.record("expected_invalid_capture")


@pytest.mark.parametrize("method", ["position", "coverage"])
async def test_frozen_sdk_judge_uses_case_recording(
    evaluation_config, tmp_path, monkeypatch, sdk_evidence, method,
):
    import json

    from evals.rebuilt.domain import Outcome
    from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
    from evals.rebuilt.grading import Judge

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    cfg = build_config(SubjectSpec(database_url="sqlite://", workdir=tmp_path / "declared"))
    bindings = {"llm.base_url": "judge"}
    bindings.update({f"providers.{name}.base_url": "judge" for name in cfg.providers})
    recorded = RecordedServices({"version": 1, "bindings": bindings, "services": {"judge": [{
        "method": "POST", "path": "/v1/chat/completions", "response": {"status": 200, "json": {
            "id": "generated-judge-response", "model": "generated-judge", "created": 1,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps([{
                    "criterion": "position_property", "score": 1.0, "confidence": 1.0,
                    "rationale": "Generated protocol response"}])}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }}}]}})

    async def frozen(judge, case, *, now_ms):
        if method == "coverage":
            got = await judge.coverage_batch("Generated coverage protocol input")
            return Outcome(admitted=None, position="Generated position",
                           judged="PASS" if "error" not in got else "REVIEW",
                           error=got.get("error"))
        got = await judge.grade_position(position="Generated position", evidence="Generated source",
                                         kind="faithful", prop="Generated property")
        return Outcome(admitted=None, position="Generated position",
                       judged=got.verdict, error=got.error)

    monkeypatch.setitem(ENTRY_POINTS, "generated-frozen",
                        EntryPoint(drive=frozen, frozen_input=True))
    case = Case(name="generated-frozen", input="", metadata={"expect": "fold"})
    original = Judge("https://unbound.invalid/v1", model="generated-judge", samples=1,
                     config_path=tmp_path / "missing.toml", config=cfg)
    report = await Experiment(cases=[case], evaluators=[MeetsGrader()]).run_evaluations_async(
        task=nora_task(Spec(source_url="unused", entry="generated-frozen", judge=original,
                            services={case.name: recorded})), max_workers=1)
    assert report.detailed_results[0][0].label == "PASS", (
        report.detailed_results[0][0].reason,
        state_of_from_row(report.cases[0], backbone.NORA_ERROR))
    assert original.config is cfg
    events = read_events(sdk_evidence.path)
    calls = [e for e in events if e.get("name") == "nora.model.request"]
    responses = [e for e in events if e.get("kind") == "transport_response"]
    assert len(calls) == len(responses) == 1
    assert responses[0]["model_role"] == "judge"
    assert calls[0]["span_id"] == responses[0]["span_id"]
    assert calls[0]["case_id"] == responses[0]["case_id"]
    request = json.loads(calls[0]["attributes"]["nora.model.request.body"])
    assert request["model"] == "generated-judge"
    assert request["max_tokens"] == 4096
    if method == "coverage":
        assert request["reasoning_effort"] == "low"
        assert request["messages"] == [{
            "role": "user", "content": "Generated coverage protocol input"}]
    assert state_of_from_row(report.cases[0], backbone.NORA_BEHAVIOR) == "UNMEASURED"


def state_of_from_row(row, key):
    from strands_evals.types.evaluation import EvaluationData

    return state_of(EvaluationData(**row), key)


async def test_missing_recording_is_an_sdk_error_without_obtaining_a_subject(
    monkeypatch, sdk_evidence,
):
    calls = []

    async def forbidden(spec):
        calls.append(spec)
        raise AssertionError("missing recording reached composition")

    monkeypatch.setattr(backbone, "obtain", forbidden)
    case = Case(name="missing", input="Generated", metadata={"expect": "go"})
    report = await Experiment(cases=[case], evaluators=[MeetsGrader()]).run_evaluations_async(
        task=nora_task(Spec(source_url="unused", entry="gate")), max_workers=1)
    assert calls == []
    assert report.detailed_results[0][0].label == "ERROR"
    assert "external_responses_missing" in report.detailed_results[0][0].reason
    sdk_evidence.record("expected_invalid_capture")


def test_canonical_cursor_reads_only_new_private_bytes(sdk_evidence, tmp_path, monkeypatch):
    import io
    import shutil

    evidence = sdk_evidence
    evidence.record("generated_prefix", body="古" * 30000)
    cursor = evidence.cursor()
    evidence.record("generated_first")
    evidence.record("generated_last")
    target = evidence.path / "events.jsonl"
    original_open = type(target).open
    seen = {"bytes": 0, "seeks": []}

    class Reader(io.BufferedReader):
        def __next__(self):
            line = super().__next__()
            seen["bytes"] += len(line)
            return line

        def seek(self, offset, whence=0):
            seen["seeks"].append(offset)
            return super().seek(offset, whence)

    def observed(path, mode="r", *args, **kwargs):
        if path == target and mode == "rb":
            return Reader(io.FileIO(path, "rb"))
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(target), "open", observed)
        events = evidence.since(cursor)
    assert [event["kind"] for event in events] == ["generated_first", "generated_last"]
    assert seen["seeks"] == [cursor[1]]
    assert 0 < seen["bytes"] < 2048 < target.stat().st_size
    broken = tmp_path / "missing-tail"
    shutil.copytree(evidence.path, broken)
    copy = broken / "events.jsonl"
    copy.write_bytes(b"".join(copy.read_bytes().splitlines(keepends=True)[:-1]))
    with monkeypatch.context() as patch:
        patch.setattr(evidence, "path", broken)
        with pytest.raises(EvidenceError, match="dropped_event"):
            evidence.since(cursor)


def test_uncaptured_native_sdk_refuses_before_any_subject_source(evaluation_config, tmp_path,
                                                               monkeypatch):
    import asyncio
    import contextvars

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("uncaptured draw reached subject source")

    monkeypatch.setattr(backbone, "_drive_draw", forbidden)
    cfg = build_config(SubjectSpec(database_url="sqlite://", workdir=tmp_path / "declared"))
    case = Case(name="generated-uncaptured", input="Generated", metadata={"expect": "go"})
    spec = Spec(source_url="unused", entry="gate", services={case.name: recording(cfg)})
    report = contextvars.Context().run(lambda: asyncio.run(Experiment(cases=[case],
        evaluators=[MeetsGrader()]).run_evaluations_async(task=nora_task(spec), max_workers=1)))
    assert calls == []
    assert report.detailed_results[0][0].label == "ERROR"
    assert "canonical_capture_unavailable" in state_of_from_row(
        report.cases[0], backbone.NORA_ERROR)
