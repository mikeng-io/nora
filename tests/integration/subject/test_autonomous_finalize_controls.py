"""Canonical evidence gates for the autonomous journey's finalization.

Each control builds one wake's REAL canonical evidence — private events,
the settled SQL rows, an effect boundary — and calls the journey's own
`_finalize`. A torn or foreign window must freeze the wake ERROR with
ZERO judge traffic; only complete evidence reaches both judges. The
denominator bindings and the plan manifest are exercised on frozen
produced rows and malformed plans — no runtime needed.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from evals.rebuilt.autonomous_journey import (
    _bind_declared_wakes,
    _finalize,
    _plan_error,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    BRIDGE_ACTION,
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
)
from nora.subject.effects import EffectScope, effect_boundary
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.judges import JUDGE_INPUT_MAX_BYTES
from scripts.cognition_loop.verdicts import ERROR, PASS, Rollup

UNIT = "generated-unit"
SCOPE = EffectScope(unit=UNIT, rooms=(("signal", "g1"),),
                    profile_keys=("generated-profile",))
MODELS = ("generated-a", "generated-b")
EXPECTATIONS = [Expectation(wake=0, polarity="must", kind="note",
                            text="a generated claim")]
VALID_PLAN = {
    "steps": [{"send": "generated hello"}, {"until_wakes": 1}],
    "expected_wakes": 1,
    "expectations": [{"wake": 0, "polarity": "must", "kind": "note",
                      "text": "a generated claim"}],
    "judges": list(MODELS),
}


class CountingJudges:
    """A judge seam that counts dispatches; served identities differ."""

    input_budget = JUDGE_INPUT_MAX_BYTES

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.served: dict[str, list[dict[str, Any]]] = {}

    async def call(self, model: str, instruction: Any, payload: Any) -> str:
        self.calls.append(model)
        self.served.setdefault(model, []).append(
            {"x-omniroute-model": f"served-{model}"})
        return json.dumps(
            {"verdict": "met", "reason": "generated", "entry_ids": []})


def _named(name: str, **body: Any) -> dict[str, Any]:
    return {"name": name, "attributes": {"body": json.dumps(body)}}


def _ordered(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**event, "order": index, "event_id": f"e{index}",
             "time_ns": index, "run_id": "generated"}
            for index, event in enumerate(events, start=1)]


def _session_events(session_id: int, *, terminal: bool = True,
                    settled: bool = True) -> list[dict[str, Any]]:
    """One complete session window in the production evidence shape."""
    inv, attempt = "inv-1", "attempt-1"
    request = {"model": "generated-subject", "messages": [
        {"role": "system", "content": "generated system"},
        {"role": "user", "content": "generated agenda"}]}
    events: list[dict[str, Any]] = [
        {"kind": "event", **_named("nora.session.started",
            session_id=session_id, wake_id=1, system="generated system",
            agenda="generated agenda", items=[], stimuli=[])},
        {"kind": "event", **_named("nora.session.instructions",
            session_id=session_id, doctrine_sha256="generated")},
        {"kind": "invocation_started", "invocation_id": inv,
         "parent_invocation_id": None},
        {"kind": "call_started", "invocation_id": inv,
         "attempt_id": attempt},
        {"kind": "transport_started", "invocation_id": inv,
         "attempt_id": attempt},
        {"kind": "event", "name": "nora.model.request",
         "attributes": {"nora.model.request.body": json.dumps(request)}},
        {"kind": "transport_response", "invocation_id": inv,
         "attempt_id": attempt, "response": {"choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "generated reply"}}]}},
        {"kind": "transport_finished", "invocation_id": inv,
         "attempt_id": attempt},
        {"kind": "call_finished", "invocation_id": inv,
         "attempt_id": attempt},
        {"kind": "invocation_finished", "invocation_id": inv},
    ]
    if terminal:
        events.append({"kind": "event", **_named(
            "nora.session.terminal", session_id=session_id,
            outcome="completed")})
    if settled:
        events.append({"kind": "event", **_named(
            "nora.session.settled", session_id=session_id)})
    return _ordered(events)


def _evidence_dir(tmp_path: Path, events: list[dict[str, Any]]) -> Path:
    path = tmp_path / "capture" / "run-1"
    path.mkdir(mode=0o700, parents=True)
    fd = os.open(path / "events.jsonl",
                 os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")
    return path


async def _settled_rows(engine: Any, *, session_id: Any = 7,
                        outcome: str = "closed") -> None:
    """The SQL lifecycle a real closed wake leaves, written at finalize time."""
    async with engine.begin() as conn:
        await conn.execute(sa.insert(COGNITION_WAKE).values(
            id=1, unit=UNIT, mode="live", reason="strong_cue",
            from_stimulus_id=0, through_stimulus_id=0, stimulus_count=0,
            eligible_count=0, at_ms=1234, tracked=1))
        await conn.execute(sa.insert(COGNITION_SESSION).values(
            id=7, unit=UNIT, mode="live", wake_id=1, started_at_ms=1234,
            from_stimulus_id=0, through_stimulus_id=0,
            situation={}, agenda={}))
        await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
            wake_id=1, unit=UNIT, session_id=session_id, outcome=outcome,
            at_ms=1235))


def _ctx(engine: Any, boundary: Any, *, session_id: Any = 7,
         outcome: str = "closed", wake_unit: str = UNIT) -> dict[str, Any]:
    return {
        "wake": {"id": 1, "unit": wake_unit, "at_ms": 1234,
                 "reason": "strong_cue"},
        "outcome": {"wake_id": 1, "session_id": session_id,
                    "outcome": outcome, "detail": None},
        "engine": engine, "boundary": boundary, "tokens": None,
        "batch": None,
    }


def _subject() -> Any:
    return SimpleNamespace(
        deps=SimpleNamespace(metrics=SimpleNamespace(
            get=lambda *args, **kwargs: None)),
        cfg=SimpleNamespace())


async def _finalize_once(clean_pg_url: str, tmp_path: Path, *,
                         events: list[dict[str, Any]],
                         session_id: Any = 7,
                         row_session_id: Any = None,
                         outcome: str = "closed",
                         wake_unit: str = UNIT,
                         post_settle=None) -> tuple[dict[str, Any], CountingJudges]:
    """The journey's own finalize on one wake, with its settled rows pre-written."""
    engine = create_sql_engine(clean_pg_url)
    try:
        boundary = await effect_boundary(engine)
        before = await state_mod.snapshot(
            engine, unit=SCOPE.unit, rooms=SCOPE.rooms,
            profile_keys=SCOPE.profile_keys, now_ms=0)
        await _settled_rows(engine, session_id=session_id
                            if row_session_id is None else row_session_id,
                            outcome=outcome)
        if post_settle is not None:
            await post_settle(engine)
        judges = CountingJudges()
        row = await _finalize(
            0, _ctx(engine, boundary, session_id=session_id,
                    outcome=outcome, wake_unit=wake_unit),
            subject=_subject(), scope=SCOPE, output=tmp_path,
            judges=judges, models=MODELS, expectations=EXPECTATIONS,
            earlier_wakes=(), prior_links=set(),
            egress={"status": "isolated"},
            evidence_path=_evidence_dir(tmp_path, events), before=before)
        return row, judges
    finally:
        await engine.dispose()


async def test_finalize_with_complete_evidence_dispatches_both_judges(
        clean_pg_url, tmp_path):
    """Positive control: a closed wake's own complete evidence reaches
    both judges — one call per model for the wake's one criterion."""
    row, judges = await _finalize_once(
        clean_pg_url, tmp_path, events=_session_events(7))
    assert row["step"] is None and row["error"] is None
    assert sorted(judges.calls) == sorted(MODELS)
    (expectation,) = row["expectations"]
    assert len(expectation["answers"]) == 2
    assert {a["served"] for a in expectation["answers"]} == {
        "served-generated-a", "served-generated-b"}


async def test_finalize_missing_terminal_freezes_error_without_judges(
        clean_pg_url, tmp_path):
    """A session window with no terminal is capture-invalid: the wake
    freezes ERROR at the evidence gate and no judge is asked."""
    row, judges = await _finalize_once(
        clean_pg_url, tmp_path,
        events=_session_events(7, terminal=False))
    assert row["status"] == ERROR and row["step"] == "evidence"
    assert "capture_terminal_missing" in row["error"]
    assert judges.calls == [] and row["expectations"] == []
    frozen = json.loads((tmp_path / "wake-0-1.json").read_text())
    assert frozen["error"]["step"] == "evidence"
    assert frozen["expectations"] == []


async def test_finalize_missing_settlement_freezes_error_without_judges(
        clean_pg_url, tmp_path):
    """No closed `cognition_wake_outcome` for this wake is
    `settlement_absent` — frozen ERROR, zero judge traffic."""
    engine = create_sql_engine(clean_pg_url)
    try:
        boundary = await effect_boundary(engine)
        before = await state_mod.snapshot(
            engine, unit=SCOPE.unit, rooms=SCOPE.rooms,
            profile_keys=SCOPE.profile_keys, now_ms=0)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(COGNITION_WAKE).values(
                id=1, unit=UNIT, mode="live", reason="strong_cue",
                from_stimulus_id=0, through_stimulus_id=0,
                stimulus_count=0, eligible_count=0, at_ms=1234,
                tracked=1))
            await conn.execute(sa.insert(COGNITION_SESSION).values(
                id=7, unit=UNIT, mode="live", wake_id=1,
                started_at_ms=1234, from_stimulus_id=0,
                through_stimulus_id=0, situation={}, agenda={}))
        judges = CountingJudges()
        row = await _finalize(
            0, _ctx(engine, boundary), subject=_subject(), scope=SCOPE,
            output=tmp_path, judges=judges, models=MODELS,
            expectations=EXPECTATIONS, earlier_wakes=(), prior_links=set(),
            egress={"status": "isolated"},
            evidence_path=_evidence_dir(tmp_path, _session_events(7)),
            before=before)
        assert row["status"] == ERROR and row["step"] == "evidence"
        assert "settlement_absent" in row["error"]
        assert judges.calls == [] and row["expectations"] == []
    finally:
        await engine.dispose()


async def test_finalize_foreign_settlement_freezes_error_without_judges(
        clean_pg_url, tmp_path):
    """The claimed session 7 has its own complete captured window, but
    the settlement row inside the observed effects carries session 9 —
    the receipt contradicts the claim: `settlement_foreign`, frozen
    ERROR, zero judge traffic."""
    row, judges = await _finalize_once(
        clean_pg_url, tmp_path,
        events=_session_events(7), session_id=7, row_session_id=9)
    assert row["status"] == ERROR and row["step"] == "evidence"
    assert "settlement_foreign" in row["error"]
    assert judges.calls == []


async def test_finalize_torn_capture_freezes_error_without_judges(
        clean_pg_url, tmp_path):
    """A foreign wake identity and an unsettled invocation are both
    named causes — the row still freezes durable, with zero judges."""
    events = _session_events(7)
    events = [e for e in events
              if e["kind"] not in ("call_finished", "invocation_finished")]
    events = _ordered(events)
    row, judges = await _finalize_once(
        clean_pg_url, tmp_path, events=events, wake_unit="other-unit")
    assert row["status"] == ERROR and row["step"] == "evidence"
    assert "foreign_wake_unit" in row["error"]
    assert "capture_unmatched_event" in row["error"]
    assert judges.calls == []


async def test_finalize_one_cut_covers_post_settle_writes(
        clean_pg_url, tmp_path):
    """Repair-4: delivery and a reminder DELIVERED after the wake settled
    are inside the one cut the effects read, the state snapshot and the
    diff all share — a pending-only projection would hide the done row
    and let the two windows disagree."""

    async def post_settle(engine):
        async with engine.begin() as conn:
            await conn.execute(sa.insert(BRIDGE_ACTION).values(
                action_id="generated-action", platform="signal",
                account_key="generated", owner_kind="live_invocation",
                owner_id="generated-owner", op="send",
                request={"target": {"id": "g1"}},
                request_hash="generated-hash", state="confirmed",
                completed_at=datetime.now(UTC)))
            await conn.execute(sa.text(
                "INSERT INTO reminders (id, requester_person_id, platform,"
                " conversation_id, conversation_type, text, created_at,"
                " due_at, due_tick, state, fired_at) VALUES (:id, :pid,"
                " 'signal', 'g1', 'group', 'generated delivered reminder',"
                " now(), :due,"
                " ceil(extract(epoch from (:due -"
                " '1970-01-01 00:00:00+00'::timestamptz)) * 1000"
                " / 60000)::bigint, 'done', :fired)"),
                {"id": uuid.uuid4(), "pid": "miles_novak",
                 "due": datetime.now(UTC),
                 "fired": datetime.now(UTC)})

    row, judges = await _finalize_once(
        clean_pg_url, tmp_path, events=_session_events(7),
        post_settle=post_settle)
    assert row["step"] is None
    items = row["trajectory"]["effects"]["items"]
    kinds = {item["kind"] for item in items}
    assert "delivery" in kinds and "reminder" in kinds
    assert row["diff_counts"]["reminder"]["created"] == 1
    (reminder_id,) = set(row["_state_before"].entries("reminder"))
    after_entries = row["_state_before"].entries("reminder")
    assert after_entries[reminder_id]["state"] == "done"
    assert sorted(judges.calls) == sorted(MODELS)


async def test_finalize_cut_hides_a_concurrent_commit_consistently(
        clean_pg_url, tmp_path, monkeypatch):
    """F1: a writer that commits between the effects read and the state
    read lands in NEITHER — one REPEATABLE READ image reaches the judges.

    The latch is real: `state_mod.snapshot` is wrapped so an independent
    engine connection commits a done reminder after `read_effects`
    finished and before the snapshot's first query runs — no new wake,
    no fake snapshot. The one shared cut reads the pre-commit image on
    both sides; without it the snapshot would see the row and the
    effects would not, and the diff would disagree with the evidence
    the judges were shown.
    """
    real_snapshot = state_mod.snapshot
    committed: list[bool] = []
    calls = 0

    async def latched_snapshot(engine, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # The opening `before` snapshot — the latch belongs inside
            # finalize's cut, after the effects read, before the state read.
            return await real_snapshot(engine, **kwargs)
        writer = create_sql_engine(clean_pg_url)
        try:
            async with writer.begin() as conn:
                await conn.execute(sa.text(
                    "INSERT INTO reminders (id, requester_person_id,"
                    " platform, conversation_id, conversation_type, text,"
                    " created_at, due_at, due_tick, state, fired_at)"
                    " VALUES (:id, :pid, 'signal', 'g1', 'group',"
                    " 'generated concurrent reminder', now(), :due,"
                    " ceil(extract(epoch from (:due -"
                    " '1970-01-01 00:00:00+00'::timestamptz)) * 1000"
                    " / 60000)::bigint, 'done', :fired)"),
                    {"id": uuid.uuid4(), "pid": "miles_novak",
                     "due": datetime.now(UTC), "fired": datetime.now(UTC)})
            committed.append(True)
        finally:
            await writer.dispose()
        return await real_snapshot(engine, **kwargs)

    monkeypatch.setattr(state_mod, "snapshot", latched_snapshot)
    row, judges = await _finalize_once(
        clean_pg_url, tmp_path, events=_session_events(7))
    assert committed == [True]
    items = row["trajectory"]["effects"]["items"]
    assert not any("concurrent" in json.dumps(item) for item in items)
    entries = row["_state_before"].entries("reminder")
    assert not any("concurrent" in json.dumps(entry)
                   for entry in entries.values())
    # The commit is real — a fresh read outside the cut sees the row.
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.connect() as conn:
            seen = (await conn.execute(sa.text(
                "SELECT count(*) FROM reminders"
                " WHERE text = 'generated concurrent reminder'"))).scalar_one()
        assert seen == 1
    finally:
        await engine.dispose()
    assert sorted(judges.calls) == sorted(MODELS)


@pytest.mark.parametrize("broken", [
    {},                                     # no expected_wakes
    {"expected_wakes": 0},
    {"expected_wakes": "2"},
    {"expected_wakes": True},
    {"expected_wakes": 1},                  # no steps
    {"expected_wakes": 1, "steps": []},
    {"expected_wakes": 1, "steps": [{"until_wakes": 1}]},   # sends nothing
    {"expected_wakes": 1, "steps": [{"send": "x"}]},        # never reached
    {"expected_wakes": 1, "steps": [
        {"send": "x"}, {"until_wakes": 2}]},                # out of range
    {"expected_wakes": 2, "steps": [
        {"send": "x"}, {"until_wakes": 1}]},                # short of declared
    {"expected_wakes": 1, "steps": [
        {"send": "x"}, {"until_wakes": 1}, {"bogus": True}]},
    {**VALID_PLAN, "expectations": [
        {"wake": 1, "polarity": "must", "kind": "note", "text": "x"}]},
    {**VALID_PLAN, "expectations": [
        {"wake": 0, "polarity": "should", "kind": "note", "text": "x"}]},
    {**VALID_PLAN, "expectations": [
        {"wake": 0, "polarity": "must", "kind": "vibes", "text": "x"}]},
    {**VALID_PLAN, "expectations": [
        {"wake": 0, "polarity": "must", "kind": "note", "text": "  "}]},
    {**VALID_PLAN, "judges": ["generated-a"]},
    {**VALID_PLAN, "judges": ["generated-a", "generated-a"]},
    {**VALID_PLAN, "judges": "generated-a"},
])
def test_malformed_plan_refuses_before_runtime(broken):
    """Every malformed manifest names its refusal before the App, a model
    call or a judge could run — the declared slots stay the denominator."""
    assert isinstance(_plan_error(broken), str)


def test_valid_plan_binds():
    assert _plan_error(dict(VALID_PLAN)) is None


def test_bind_declared_wakes_keeps_missing_slots_in_the_denominator():
    roll = Rollup()
    bound = _bind_declared_wakes(3, [11], roll)
    assert bound == [11, None, None]
    assert roll.verdict == ERROR
    assert any("1 of the 3 declared" in e for e in roll.errors)


def test_bind_declared_wakes_rejects_extra_produced():
    roll = Rollup()
    bound = _bind_declared_wakes(2, [11, 12, 13], roll)
    assert bound == [11, 12]
    assert roll.verdict == ERROR
    assert any("beyond the 2 declared" in e for e in roll.errors)


def test_bind_declared_wakes_rejects_duplicate_produced():
    roll = Rollup()
    bound = _bind_declared_wakes(3, [11, 11], roll)
    assert bound == [11, 11, None]
    assert roll.verdict == ERROR
    assert any("two declared slots" in e for e in roll.errors)


def test_bind_declared_wakes_passes_a_full_census():
    roll = Rollup()
    assert _bind_declared_wakes(2, [11, 12], roll) == [11, 12]
    assert roll.verdict == PASS and roll.errors == []
