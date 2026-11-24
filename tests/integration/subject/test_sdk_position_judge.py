"""Recorded SDK position grading retains the judge request and response."""

from __future__ import annotations

import json

import pytest
from opentelemetry import baggage, context, trace
from strands_evals import Case, Experiment

from evals import backbone
from evals.backbone import MeetsGrader, Spec, nora_task
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from evals.rebuilt.grading import Judge
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.obs.tracing import attached
from nora.subject.eval_evidence import read_events, validate_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from tests.builders import override_config
from tests.integration.subject.test_sdk_recorded_caller import (
    IDENTITY,
    NOW,
    recording,
    sdk_capture,
    state_of_from_row,
)


@pytest.mark.parametrize(("finish_reason", "detach"), [
    pytest.param("stop", False, id="position"),
    pytest.param("stop", True, id="detached-position"),
    pytest.param("length", False, id="length"),
    pytest.param("content_filter", False, id="content_filter"),
])
async def test_sdk_position_judge_retains_recorded_responses(
    clean_pg_url, tmp_path, monkeypatch, finish_reason, detach,
):
    class DetachedJudge(Judge):
        async def grade_position(self, **kwargs):
            with trace.get_tracer(__name__).start_as_current_span("generated-judge-parent") as span:
                token = context.attach(baggage.set_baggage("unrelated-parent", "generated"))
                parent = span.get_span_context()
                assert parent.is_valid
                try:
                    with attached(None):
                        assert baggage.get_baggage("unrelated-parent") is None
                        assert not trace.get_current_span().get_span_context().is_valid
                        return await super().grade_position(**kwargs)
                finally:
                    assert trace.get_current_span().get_span_context() == parent
                    context.detach(token)

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    engine = create_sql_engine(clean_pg_url)
    try:
        await SqlRecordStore(engine).append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", NOW - 1000,
            "Generated source", {}), person_id="miles_novak")
    finally:
        await engine.dispose()
    subject_overrides = {"platforms.signal.enabled": True, "platforms.signal.mode": "bridge"}
    policy = default_policy(("signal", "g1"))
    cfg = build_config(SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "declared",
                                  overrides=subject_overrides, identity=IDENTITY, policy=policy))
    unbound = "http://127.0.0.1:1/v1"
    original_config = override_config(cfg, {
        "llm.base_url": unbound,
        **{f"providers.{name}.base_url": unbound for name in cfg.providers},
    })
    model = cfg.endpoint("eval_judge", "generated-judge")[2]
    raw = json.dumps([{"criterion": "position_property", "score": 1.0, "confidence": 1.0,
                       "rationale": "Generated protocol response"}])
    recorded = recording(cfg, model_responses=[{
        "method": "POST", "path": "/v1/chat/completions", "response": {"status": 200, "json": {
            "id": "generated-judge-response", "model": "generated-served-judge", "created": 1,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": raw}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }},
    }])

    async def position(subject, case, *, now_ms):
        return Outcome(admitted=None, position="Generated position", evidence="Generated source")

    monkeypatch.setitem(ENTRY_POINTS, "generated-position",
                        EntryPoint(drive=position, judged_on="position"))
    case = Case(
        name="generated-position", input="Generated fold", expected_output="Generated property",
        metadata={"expect": "fold", "kind": "faithful", "snapshot": {
            "platform": "signal", "conversation_id": "g1", "before_ms": NOW}},
    )
    judge_type = DetachedJudge if detach else Judge
    original = judge_type(unbound, model="generated-judge", samples=1,
                          config_path=tmp_path / "missing.toml", config=original_config)
    spec = Spec(source_url=clean_pg_url, entry="generated-position", judge=original,
                identity=IDENTITY, policy=policy, overrides=subject_overrides,
                services={case.name: recorded})
    with sdk_capture(tmp_path) as evidence:
        report = await Experiment(cases=[case], evaluators=[MeetsGrader()]).run_evaluations_async(
            task=nora_task(spec), max_workers=1)
        row = report.cases[0]
        error = state_of_from_row(row, backbone.NORA_ERROR)
        assert original.config is original_config
        events = read_events(evidence.path)
        calls = [e for e in events if e.get("name") == "nora.model.request"]
        responses = [e for e in events if e.get("kind") == "transport_response"]
        assert len(calls) == len(responses) == 1, error
        [response] = responses
        assert response["model_role"] == "judge"
        assert calls[0]["span_id"] == response["span_id"]
        assert calls[0]["case_id"] == response["case_id"]
        request = json.loads(calls[0]["attributes"]["nora.model.request.body"])
        assert request["model"] == model
        assert request["max_tokens"] == 4096
        [message] = request["messages"]
        assert message["role"] == "user"
        assert all(text in message["content"] for text in (
            "Generated property", "Generated position", "Generated source",
        ))
        completion = response["response"]
        assert completion["id"] == "generated-judge-response"
        assert completion["model"] == "generated-served-judge"
        [choice] = completion["choices"]
        assert choice["finish_reason"] == finish_reason
        assert choice["message"]["content"] == raw
        rejected = finish_reason != "stop"
        assert report.detailed_results[0][0].label == ("ERROR" if rejected else "PASS"), error
        assert state_of_from_row(row, backbone.NORA_JUDGED) == ("REVIEW" if rejected else "PASS")
        if rejected:
            assert "judge response truncated or refused" in error
        else:
            assert error is None
        assert state_of_from_row(row, backbone.NORA_BEHAVIOR) == "UNMEASURED"
    assert validate_events(read_events(evidence.path))["status"] == "PASS"
