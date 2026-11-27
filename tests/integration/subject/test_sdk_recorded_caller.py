"""Generated SDK gate replay through production composition and owned HTTP."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from aiohttp import web
from opentelemetry import trace
from strands_evals import Case, Experiment

from evals import backbone
from evals.backbone import MeetsGrader, Spec, nora_task, state_of
from evals.memory_graph_spike import measured_run_budget
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject import effect_capture as effects_mod
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices
from nora.subject.sandbox import eval_host_lock

NOW = 2_000_000_000_000
IDENTITY = '[persons.miles_novak]\nname="Miles"\n[[persons.miles_novak.signal]]\nphone="+852111"\n'


@contextmanager
def sdk_capture(tmp_path):
    backbone._prefill_capture()
    budget = {}
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="generated-sdk", lock_fd=lock_fd,
            private_root=tmp_path / "capture", metadata={"behavior": "UNMEASURED"})
        with evidence.capture(trace.get_tracer_provider()), measured_run_budget(
            SimpleNamespace(stop_after_tokens=20000), evidence, tmp_path, budget,
            max_requests=20, max_output_tokens=400000,
        ):
            yield evidence


@pytest.fixture(autouse=True)
def sdk_evidence(tmp_path):
    evidence = None
    try:
        with sdk_capture(tmp_path) as evidence:
            yield evidence
    except EvidenceError as exc:
        assert evidence is not None
        assert str(exc) == "incomplete_record"
        events = read_events(evidence.path)
        assert any(e["kind"] == "expected_invalid_capture" for e in events)
        assert validate_events(events)["causes"] == ["capture_interrupted"]
    else:
        assert validate_events(read_events(evidence.path))["status"] == "PASS"


def recording(cfg, path="/v1/chat/completions", *, model_responses=None):
    bindings = {"llm.base_url": "model", "platforms.signal.base_url": "bridge"}
    bindings.update({f"providers.{name}.base_url": "model" for name in cfg.providers})
    model = cfg.endpoint("relevance_gate", cfg.relevance_gate.model)[2]
    data = {"version": 1, "bindings": bindings, "services": {
        "bridge": [{"method": "GET", "path": "/v1/self", "response": {"status": 200,
                     "json": {"primary": "generated-self", "aliases": []}}}],
        "model": [{
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


async def test_a_conflicting_measurement_refuses_before_sdk_composition(sdk_evidence):
    from evals.measurement import BEHAVIORAL, PROTOCOL

    PROTOCOL.bind(sdk_evidence)
    PROTOCOL.bind(sdk_evidence)
    before = read_events(sdk_evidence.path)
    bindings = [e for e in before if e["kind"] == "measurement_bound"]
    assert len(bindings) == 1
    assert bindings[0]["measurement"] == "protocol-rehearsal"
    assert sdk_evidence.metadata["measurement"] == "protocol-rehearsal"
    with pytest.raises(EvidenceError) as refused:
        await backbone._drive(Case(name="foreign-claim", input="generated"),
                              Spec(source_url="unused", measurement=BEHAVIORAL))
    added = read_events(sdk_evidence.path)[len(before):]
    if any(e["kind"] == "case_failed" for e in added):
        sdk_evidence.record("expected_invalid_capture")
    assert added == []
    assert str(refused.value) == "measurement_claim_conflict"


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
