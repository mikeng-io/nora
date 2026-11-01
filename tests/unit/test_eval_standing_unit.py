"""The unit eval measures a saved Record's fold, never another agent turn."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.rebuilt.domain import Case, Moment
from evals.rebuilt.entry import standing_unit
from nora.agent.shared_state import read_shared
from nora.domain.standing_ledger import LedgerEntry
from nora.domain.tag_trace import TagTrace
from nora.obs.metrics import Metrics
from nora.testing.standing_ledger import InMemoryStandingLedger
from nora.testing.tag_traces import FakeTagTraceStore
from tests.objectives import shipped_objectives


def test_unit_judge_reads_the_unit_contract_instead_of_a_room_recap():
    from evals.rebuilt.judge import _build_position_prompt

    prompt = _build_position_prompt(
        kind="faithful", prop="retain the scheduled check",
        evidence="generated completed session", position="generated unit state",
    )
    assert "UNIT STANDING" in prompt
    assert "Scheduling is not delivery" in prompt
    assert "wrote about one room" not in prompt
    assert "generated completed session" in prompt
    assert "generated unit state" in prompt


async def _setup(monkeypatch, *, trace_changes=None, outcome_code="written", keep=True):
    trace = TagTrace(
        tag_id="generated-session", conversation_id="", unit="generated-unit",
        occasion="cognition", created_at=2000, outcome="silent",
        bound_rooms=("signal:generated-a", "discord:generated-b"),
        transcript=(), messages=({"role": "assistant", "content": [
            {"text": "The generated check remains scheduled; no message was delivered."},
        ]},),
    )
    trace = replace(trace, **(trace_changes or {}))
    source = FakeTagTraceStore()
    await source.save(trace)
    target = FakeTagTraceStore()
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr("nora.infra.sql.create_readonly_sql_engine", lambda _url: engine)
    monkeypatch.setattr("nora.infra.sql.tag_traces.SqlTagTraceStore", lambda _engine: source)
    ledger = InMemoryStandingLedger()
    metrics = Metrics()
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        assert await target.by_tag(tag_id=kwargs["trace"].tag_id) is not None
        if keep:
            ledger.entries.append(LedgerEntry(
                id="generated-c1", kind="commitment", scope="unit", unit="generated-unit",
                platform="signal", conversation_id="", revision=1, status="open",
                subject="generated pending check", body="## Owed\nThe check is scheduled.",
                opened_at=2000, last_revised_at=2000,
            ))
        metrics.inc("nora_debrief_total", outcome=outcome_code, source="cognition")

    rooms = (("signal", "generated-a"), ("discord", "generated-b"))

    async def shared(**kwargs):
        return await read_shared(rooms=rooms, ledger=ledger, ai_peer="generated-nora", **kwargs)

    destinations = []

    async def destination(occasion, ref):
        destinations.append((occasion.platform, occasion.conversation_id))
        return await shared(unit="generated-unit", now_ms=2000,
                            room=(occasion.platform, occasion.conversation_id))

    runner = SimpleNamespace(
        _standing_fold=complete,
        _rooms_of_unit=lambda unit: rooms if unit == "generated-unit" else (),
        _ledger=ledger, _tag_trace_store=target, _shared=shared,
    )
    subject = SimpleNamespace(
        unit="generated-unit",
        cfg=SimpleNamespace(doctrine=SimpleNamespace(display_name="Nora")),
        deps=SimpleNamespace(
            cognition_runner=runner, metrics=metrics,
            fire_path=SimpleNamespace(_read_shared=destination, destinations=destinations),
            engagement=SimpleNamespace(_debrief_wiring=SimpleNamespace(objective=shipped_objectives())),
        ),
    )
    case = Case(name="generated-unit-fold", entry="standing_unit", expect="fold",
                moment=Moment(source_url="generated-source", rows={}),
                metadata={"tag_id": trace.tag_id})
    return subject, case, calls, target


@pytest.mark.asyncio
async def test_unit_eval_folds_once_and_reads_actual_canonical_state(monkeypatch):
    subject, case, calls, target = await _setup(monkeypatch)
    result = await standing_unit(subject, case, now_ms=2000)
    assert result.error is None
    assert len(calls) == 1
    assert calls[0]["rooms"] == (("signal", "generated-a"), ("discord", "generated-b"))
    assert calls[0]["unit"] == "generated-unit"
    assert (await target.by_tag(tag_id=case.metadata["tag_id"])).handle is not None
    assert "generated pending check" in result.position
    assert "scheduled" in result.evidence
    assert subject.deps.fire_path.destinations == [
        ("signal", "generated-a"), ("discord", "generated-b"),
    ]


@pytest.mark.asyncio
async def test_a_missing_destination_projection_is_not_shared_state_success(monkeypatch):
    subject, case, calls, _ = await _setup(monkeypatch)
    subject.deps.fire_path._read_shared = AsyncMock(return_value=None)
    result = await standing_unit(subject, case, now_ms=2000)
    assert len(calls) == 1
    assert result.error and result.position is None


@pytest.mark.asyncio
async def test_unit_input_preview_makes_no_call_and_writes_no_record(monkeypatch):
    subject, case, calls, target = await _setup(monkeypatch)
    case = replace(case, metadata={**case.metadata, "print_input": True})
    result = await standing_unit(subject, case, now_ms=2000)
    assert "no model" in result.error
    assert "standing_ledger_prior" in result.shown
    assert "scheduled" in result.shown
    assert not calls
    assert await target.by_tag(tag_id=case.metadata["tag_id"]) is None


@pytest.mark.asyncio
async def test_an_uncitable_historical_record_is_refused_before_a_paid_fold(monkeypatch):
    subject, case, calls, _ = await _setup(monkeypatch, trace_changes={
        "messages": ({"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "generated-legacy", "name": "close", "input": {},
        }}]},),
    })
    result = await standing_unit(subject, case, now_ms=2000)
    assert result.error and not calls


@pytest.mark.asyncio
async def test_generated_record_is_explicit_and_does_not_backfill_old_requirements(monkeypatch):
    subject, case, calls, target = await _setup(monkeypatch, trace_changes={
        "messages": ({"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "generated-legacy", "name": "close", "input": {},
        }}]},),
    })
    case = replace(case, metadata={**case.metadata,
                   "counterfactual": {"varies": "unit_record"},
                   "record_text": "GENERATED: choose Amber conditionally; the check remains open."})
    result = await standing_unit(subject, case, now_ms=2000)
    assert result.error is None and len(calls) == 1
    saved = await target.by_tag(tag_id="generated-unit:" + case.metadata["tag_id"])
    assert saved is not None and saved.requirements == {}
    assert "choose Amber conditionally" in result.evidence
    assert "generated-legacy" not in result.evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"unit": "generated-foreign-unit"}, {"created_at": 1999},
    {"occasion": "tag"}, {"bound_rooms": ("signal:generated-foreign-room",)},
])
async def test_unit_eval_refuses_a_mismatched_record_before_calling(monkeypatch, changes):
    subject, case, calls, _ = await _setup(monkeypatch, trace_changes=changes)
    result = await standing_unit(subject, case, now_ms=2000)
    assert result.error and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_code,keep", [("timeout", True), ("no_change", False)])
async def test_a_dead_or_empty_unit_fold_is_not_a_semantic_pass(monkeypatch, outcome_code, keep):
    subject, case, calls, _ = await _setup(monkeypatch, outcome_code=outcome_code, keep=keep)
    result = await standing_unit(subject, case, now_ms=2000)
    assert len(calls) == 1
    assert result.error and result.position is None


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["close", "note_create"])
@pytest.mark.parametrize("unknown_read", [False, True])
async def test_native_activity_is_citable_without_erasing_unknown_sources(verb, unknown_read):
    from nora.agent.turn_recording import TurnRecording, record_requirement
    from nora.domain.note_body import NoteBodyError
    from nora.domain.turn_record import DisclosureRequirement, cited_turn_lineage
    from tests.unit.cognition_rig import GOOD_BODY, NOTE, NOW, A, session

    recording = TurnRecording()
    result = {}
    arguments = ({"item_index": 0, "reason": "no_change"} if verb == "close"
                 else {"body": GOOD_BODY, **NOTE})

    async def behave(by, tools):
        recording.before(SimpleNamespace(tool_use={"toolUseId": "generated-call"}))
        try:
            if unknown_read:
                record_requirement(DisclosureRequirement(None, (), resolved=False))
            result.update(await by[verb](**arguments))
        finally:
            recording.after(SimpleNamespace())

    await session(behave)
    assert result["status"] == "ok"
    trace = TagTrace(
        tag_id="generated-action", unit="u", occasion="cognition",
        conversation_id="", created_at=NOW, outcome="silent", transcript=(),
        bound_rooms=(f"{A.platform}:{A.conversation_id}",),
        messages=(
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "generated-call", "name": verb, "input": arguments,
            }}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "generated-call", "content": [{"json": result}],
            }}]},
        ), requirements=recording.to_json(),
    )
    if unknown_read:
        with pytest.raises(NoteBodyError):
            cited_turn_lineage(trace, unit="u")
    else:
        assert cited_turn_lineage(trace, unit="u").section("row")
