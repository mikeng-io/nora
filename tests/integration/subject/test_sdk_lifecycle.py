"""Native SDK adapter capture over real SQL; production runtime controls are separate."""

from __future__ import annotations

import html
import json
import re

import pytest
from aiohttp import web
from strands_evals import Case, Experiment

from evals import backbone
from evals.backbone import MeetsGrader, Spec, nora_task
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from evals.rebuilt.grading import Judge
from nora.domain.models import NormalizedMessage
from nora.domain.notes import NoteSupersedeReason
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.subject import effect_capture as effects
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import read_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from tests.integration.subject.test_sdk_recorded_caller import (
    IDENTITY,
    NOW,
    recording,
    state_of_from_row,
)
from tests.integration.subject.test_sdk_recorded_caller import (
    sdk_evidence as sdk_evidence,
)


async def retire_note(subject, ids):
    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlNoteStore(engine)
        note = await store.create(unit=subject.unit, title="Generated venue question",
            description="A generated venue is undecided.", uncertainty="Which venue?",
            closing_condition="A generated venue is selected.", established_in="g1",
            body="## Decided\n\n## Open\n- Which generated venue? [1]",
            established_by="generated-sdk-control", now_ms=NOW, anchor_entity_ids=("Miles",))
        ids.append(note.id)
        assert await store.supersede(key=subject.unit, target=note.id, replaced_by=None,
            because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=NOW + 1) == 1
        assert await store.standing(unit=subject.unit) == ()
    finally:
        await engine.dispose()


def judge_response(model):
    return {"method": "POST", "path": "/v1/chat/completions", "response": {
        "status": 200, "json": {"id": "generated-judge", "model": model, "created": 1,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps([{
                    "criterion": "position_property", "score": 1.0, "confidence": 1.0,
                    "rationale": "Generated adapter protocol response"}])}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}}}


@pytest.mark.parametrize("broken", [False, True])
async def test_native_sdk_keeps_each_drive_and_gives_the_judge_the_first_effects(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, sdk_evidence, broken,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    engine = create_sql_engine(clean_pg_url)
    try:
        await SqlRecordStore(engine).append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", NOW - 1000,
            "Generated earlier adapter input", {}), person_id="miles_novak")
    finally:
        await engine.dispose()
    ids, requests = [], []

    async def lifecycle(subject, case, *, now_ms):
        await retire_note(subject, ids)
        return Outcome(admitted=None, position="Generated note was retired.",
                       evidence="Generated adapter input", written=1)

    monkeypatch.setitem(ENTRY_POINTS, "generated-lifecycle", EntryPoint(
        drive=lifecycle, judged_on="position"))
    reader = effects.read_effects

    async def read(*args, **kwargs):
        if broken:
            raise RuntimeError("generated lifecycle reader unavailable")
        return await reader(*args, **kwargs)

    monkeypatch.setattr(effects, "read_effects", read)
    coverage = IsolatedServices._coverage

    def observe(self, service):
        original = coverage(self, service)

        @web.middleware
        async def capture(request, handler):
            if request.path.endswith("chat/completions"):
                requests.append(await request.json())
            return await original(request, handler)

        return capture

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    policy = default_policy(("signal", "g1"), unit="generated-unit")
    overrides = {"platforms.signal.enabled": True, "platforms.signal.mode": "bridge"}
    cfg = build_config(SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "declared",
                                  policy=policy, identity=IDENTITY, overrides=overrides))
    model = cfg.endpoint("eval_judge", "generated-judge")[2]
    case = Case(name="generated-lifecycle", input="Generated adapter control", expected_output=
        "The generated note was created and retired.", metadata={"expect": "fold",
        "kind": "faithful", "snapshot": {
            "platform": "signal", "conversation_id": "g1", "before_ms": NOW}})
    spec = Spec(source_url=clean_pg_url, entry="generated-lifecycle", repeat=2,
                identity=IDENTITY, policy=policy, overrides=overrides,
                judge=Judge("https://unbound.invalid/v1",
                    model="generated-judge", samples=1, config=cfg), services={case.name: recording(
                        cfg, model_responses=[] if broken else [judge_response(model)])})
    report = await Experiment(cases=[case], evaluators=[MeetsGrader()]).run_evaluations_async(
        task=nora_task(spec), max_workers=1)
    row = report.cases[0]
    artifact = state_of_from_row(row, backbone.NORA_EFFECTS)
    assert isinstance(artifact, dict), "native SDK lost canonical lifecycle state"
    assert report.detailed_results[0][0].label == ("ERROR" if broken else "PASS"), (
        state_of_from_row(row, backbone.NORA_ERROR), report.detailed_results[0][0].reason)
    if broken:
        assert requests == [] and len(ids) == 1
        assert "canonical_lifecycle_unavailable" in state_of_from_row(row, backbone.NORA_ERROR)
        assert artifact["status"] == "unavailable" and artifact["drives"] is None
        events = read_events(sdk_evidence.path)
        [failure] = [event for event in events if event["kind"] == "subject_effects"]
        assert failure["cause"] == "canonical_lifecycle_unavailable"
        assert failure["error_type"] == "RuntimeError"
        assert failure["frames"][-1]["function"] == "read"
        results = [event["result"] for event in events if event["kind"] == "case_result"]
        assert len(results) == 1, "the failed SDK case lost its private task result"
        result = results[0]
        captured = {state["name"]: state["state"] for state in result["environment_state"]}
        assert captured[backbone.NORA_ERROR] == state_of_from_row(row, backbone.NORA_ERROR)
        sdk_evidence.record("expected_invalid_capture")
    else:
        assert state_of_from_row(row, backbone.NORA_ERROR) is None
        assert artifact["status"] == "observed" and len(artifact["drives"]) == len(ids) == 2
        for index, drive in enumerate(artifact["drives"]):
            assert ids[index] in json.dumps(drive)
            assert ids[1 - index] not in json.dumps(drive)
            assert drive["tools"] == []
        assert len(requests) == 1
        prompt = requests[0]["messages"][0]["content"]
        block = re.search(r"<cited_sources\b[^>]*>\n(.*?)\n</cited_sources>", prompt, re.DOTALL)
        assert block is not None
        observed = json.loads(html.unescape(block.group(1)))
        assert observed["evidence_role"] == "post_execution_effects"
        assert observed["effects"] == artifact["drives"][0]
        events = [event for event in read_events(sdk_evidence.path)
                  if event["kind"] == "subject_effects"]
        assert [event["effects"] for event in events] == artifact["drives"]
        assert all(event["case_id"] == artifact["case_id"] for event in events)
        assert artifact["run_id"] == sdk_evidence.run_id
    backbone.retain_report(sdk_evidence, "generated-sdk-lifecycle", report)
    retained = json.loads((sdk_evidence.path / "corpus-generated-sdk-lifecycle.json").read_text())
    assert state_of_from_row(retained["report"]["cases"][0], backbone.NORA_EFFECTS) == artifact
