"""Generated SDK gate replay through production composition and owned HTTP."""

from __future__ import annotations

import socket

import pytest
from aiohttp import web
from strands_evals import Case, Experiment

from evals import backbone
from evals.backbone import MeetsGrader, Spec, nora_task, state_of
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject.environment import IsolatedServices
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices

NOW = 2_000_000_000_000
IDENTITY = '[persons.miles_novak]\nname="Miles"\n[[persons.miles_novak.signal]]\nphone="+852111"\n'


def recording(cfg, path="/v1/chat/completions"):
    bindings = {"llm.base_url": "model", "honcho.base_url": "memory",
                "platforms.signal.base_url": "bridge"}
    bindings.update({f"providers.{name}.base_url": "model" for name in cfg.providers})
    model = cfg.endpoint("relevance_gate", cfg.relevance_gate.model)[2]
    return RecordedServices({"version": 1, "bindings": bindings, "services": {
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
    }})


@pytest.mark.parametrize("broken", [False, True])
async def test_sdk_cases_use_real_subjects_in_distinct_owned_services(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, broken,
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
    received, subjects, requests = [], [], []
    coverage = IsolatedServices._coverage
    obtain = backbone.obtain

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
                    cfg, path="/wrong" if broken and i == 1 else "/v1/chat/completions")
                    for i, case in enumerate(cases)})
    report = await Experiment(cases=cases, evaluators=[MeetsGrader()]).run_evaluations_async(
        task=nora_task(spec), max_workers=1)
    errors = [state_of_from_row(row, backbone.NORA_ERROR) for row in report.cases]
    assert errors[0] is None, (errors, received)
    assert ("external_response_not_recorded" in (errors[1] or "")) if broken else errors[1] is None
    assert [rows[0].label for rows in report.detailed_results] == [
        "PASS", "ERROR" if broken else "PASS"]
    assert state_of_from_row(report.cases[0], backbone.NORA_CALLS) == 1
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


async def test_frozen_sdk_judge_uses_case_recording(evaluation_config, tmp_path, monkeypatch):
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
    assert state_of_from_row(report.cases[0], backbone.NORA_BEHAVIOR) == "UNMEASURED"


def state_of_from_row(row, key):
    from strands_evals.types.evaluation import EvaluationData

    return state_of(EvaluationData(**row), key)


async def test_missing_recording_is_an_sdk_error_without_obtaining_a_subject(monkeypatch):
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
