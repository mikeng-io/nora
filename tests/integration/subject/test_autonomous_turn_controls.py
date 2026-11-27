"""Canonical evidence gates for the autonomous journey's later-turn
observation.

A produced turn binds its declared input, its settled admission rows and
a typed outcome — a bound confirmed delivery, an explicit production
abstention, the admission's own decline, or the unconfirmed middle —
before any judge sees it. A foreign action, a missing decision, an
ingest-only settlement, or a conflicting session is named invalidity
with ZERO judge traffic; only complete turn evidence reaches both
judges. State is never the oracle: the same observed reply must judge
identically whichever store the diff shows.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import sqlalchemy as sa

from evals.cognition_loop.cases import Expectation
from evals.cognition_loop.judges import observation_gap, turn_gap
from evals.cognition_loop.trajectory import bound_turn_events, turn_events
from evals.cognition_loop.verdicts import ERROR
from evals.rebuilt.journey_manifest import _plan_error
from evals.rebuilt.journey_turns import (
    _freeze_turn,
    _judge_turn,
    _turn_causes,
    _turn_error,
    _turn_outcome,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_WAKE_OUTCOME, STIMULUS_CLAIM
from nora.subject.effects import stimulus_session
from tests.integration.subject.test_autonomous_finalize_controls import (
    MODELS,
    UNIT,
    CountingJudges,
    _ordered,
    _session_events,
)

V2_PLAN = {
    "version": 2,
    "steps": [{"send": "generated hello"}, {"send": "generated follow-up"}],
    "expected_wakes": 0,
    "expectations": [
        {"turn": 1, "polarity": "must", "kind": "behavior",
         "text": "the turn recalls the generated venue",
         "check": "Cedar Hall"},
    ],
    "judges": list(MODELS),
}

BEHAVIOR = [Expectation(wake=None, turn=1, polarity="must",
                        kind="behavior",
                        text="the turn recalls the generated venue",
                        check="Cedar Hall")]


class _Empty:
    """An empty state cut — the diff and after-state stay provenance only."""

    def __init__(self) -> None:
        self.kinds = {}

    def entries(self, kind: str) -> dict:
        return {}


def _turn_work(**overrides: Any) -> dict[str, Any]:
    """The `turn` work row production settles with its disposition."""
    return {"id": 43, "kind": "turn", "state": "done",
            "result": {"tag_id": "generated-tag",
                       "disposition": "send_reply"},
            **overrides}


def _work(turn: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The settled work ledger one send leaves."""
    rows: list[dict[str, Any]] = [
        {"id": 41, "kind": "ingest", "state": "done"},
        {"id": 42, "kind": "stimulus", "state": "done",
         "result": {"stimulus_id": 5}},
    ]
    if turn is not None:
        rows.append(turn)
    return rows


def _event(engage: Any = True) -> dict[str, Any]:
    return {"id": "generated-journey-2", "state": "done",
            "admission": {"engage": engage, "reason": "generated",
                          "relevance_gate": "pass"}}


def _ctx(**overrides: Any) -> dict[str, Any]:
    ctx = {
        "index": 1, "input": "generated follow-up",
        "event_id": "generated-journey-2", "at_ms": 2000,
        "cursor": (0, 0), "boundary": object(), "engine": object(),
        "event": _event(),
        "work": _work(_turn_work()),
    }
    ctx.update(overrides)
    return ctx


def _action(*, state: str = "confirmed", owner_id: str = "w-43",
            owner_kind: str = "work") -> dict[str, Any]:
    """A `bridge_action` row: durable only under its own work owner."""
    return {"action_id": f"{owner_id}:send:0", "op": "send",
            "owner_kind": owner_kind, "owner_id": owner_id,
            "owner_generation": 0, "state": state,
            "request": {"op": "send", "text": "the venue is Cedar Hall",
                        "target": {"id": "g1"}}}


def _effects(action_id: str = "w-43:send:0") -> dict[str, Any]:
    return {"status": "observed",
            "items": [{"kind": "delivery", "ref": "delivery:b:1",
                       "source": "bridge_action",
                       "row": {"action_id": action_id}}],
            "tools": []}


def _trajectory() -> dict[str, Any]:
    return {
        "turn": 1, "at_ms": 2000, "unit": UNIT,
        "session_id": None,
        "declared_input": {"text": "generated follow-up",
                           "event_id": "generated-journey-2",
                           "at_ms": 2000},
        "bound": {"event_id": "generated-journey-2", "stimulus_id": 5,
                  "wake_id": None, "session_id": None,
                  "wake_outcome": None, "session_conflict": None,
                  "attempt_traces": ["t-attempt", "t-generation"],
                  "window": {"events": 4, "orders": [1, 4]}},
        "model_calls": [{"model": "generated-subject", "messages": [
            {"role": "system", "content": "generated subject instructions"},
            {"role": "user", "content": "generated follow-up"}]}],
        "tool_calls": [{"tool": "send_reply",
                        "arguments": {"text": "the venue is Cedar Hall"},
                        "status": "ok", "result": {"status": "ok"}}],
        "effects": _effects(),
        "settlement": {"status": "observed", "event": "done", "work": []},
        "turn_outcome": {"status": "delivered",
                         "said": ["the venue is Cedar Hall"]},
        "final_response": {"status": "observed",
                           "text": "the venue is Cedar Hall"},
    }


def test_a_bound_confirmed_action_is_the_turns_own_delivery():
    outcome = _turn_outcome(_ctx(), [_action()], _effects())
    assert outcome["status"] == "delivered"
    assert outcome["said"] == ["the venue is Cedar Hall"]
    assert outcome["deliveries"] == ["delivery:b:1"]


@pytest.mark.parametrize("turn", [
    _turn_work(state="intentional_skip", result={"admission": "rejected"}),
    _turn_work(state="intentional_skip", result={"admission": "queue_full"}),
    _turn_work(result={"tag_id": "generated-tag", "disposition": "silent"}),
    _turn_work(result={"tag_id": "generated-tag", "disposition": "vetoed"}),
])
def test_explicit_abstention_is_a_production_decision_not_silence(turn):
    ctx = _ctx(work=_work(turn))
    outcome = _turn_outcome(ctx, [], _effects())
    assert outcome["status"] == "intentional_non_delivery"
    assert outcome["decisions"]


def test_the_admissions_own_decline_is_recorded_never_delivered():
    ctx = _ctx(event=_event(engage=False), work=_work())
    outcome = _turn_outcome(ctx, [], _effects())
    assert outcome["status"] == "recorded"
    assert outcome["decision"]["engage"] is False


def test_an_unrelated_delivery_cannot_stand_in_for_the_turn():
    """A confirmed action owned by another occasion's work, or by any
    non-`work` owner, is never this turn's output."""
    ctx = _ctx()
    foreign = _action(owner_id="w-99")
    outcome = _turn_outcome(ctx, [foreign],
                          _effects(action_id="w-99:send:0"))
    assert outcome["status"] == "unconfirmed"
    for kind in ("event_receipt", "otp_challenge", "live_invocation"):
        outcome = _turn_outcome(
            ctx, [_action(owner_kind=kind)], _effects())
        assert outcome["status"] == "unconfirmed", kind


def test_missing_output_never_reads_as_restraint():
    """A settled `send_reply` turn with no bound action and no recorded
    abstention is unconfirmed — quiet is not a decision."""
    outcome = _turn_outcome(_ctx(), [], _effects())
    assert outcome["status"] == "unconfirmed"
    # The admission accepted the obligation but no `turn` work ever ran:
    # ingest and stimulus alone are the same unconfirmed middle.
    ctx = _ctx(work=_work())
    assert _turn_outcome(ctx, [], _effects())["status"] == "unconfirmed"


def test_a_non_confirmed_or_conflicting_action_is_unconfirmed():
    for state in ("prepared", "started", "rejected", "uncertain"):
        outcome = _turn_outcome(
            _ctx(), [_action(state=state)], _effects())
        assert outcome["status"] == "unconfirmed", state
    # A bound send beside a bound refusal — or beside a recorded
    # abstention — contradicts delivery rather than proving it.
    outcome = _turn_outcome(
        _ctx(), [_action(), _action(state="rejected")], _effects())
    assert outcome["status"] == "unconfirmed"
    silent = _ctx(work=_work(_turn_work(
        result={"tag_id": "generated-tag", "disposition": "silent"})))
    assert _turn_outcome(
        silent, [_action()], _effects())["status"] == "unconfirmed"


@pytest.mark.parametrize("damage,cause", [
    (lambda c, t: c.update(event=None), "admission_not_done"),
    (lambda c, t: c.update(event={"state": "failed"}),
     "admission_not_done"),
    (lambda c, t: c.update(event={"id": "generated-journey-3",
                                  "state": "done"}),
     "admission_identity_foreign"),
    (lambda c, t: c.update(work=[]), "admission_work_unsettled"),
    (lambda c, t: c.update(
        work=[{"kind": "ingest", "state": "started"}]),
     "admission_work_unsettled"),
    (lambda c, t: t.update(effects={"status": "gone"}),
     "effects_unobserved"),
    (lambda c, t: t.update(declared_input=None),
     "declared_input_unbound"),
    (lambda c, t: t["declared_input"].pop("event_id"),
     "declared_input_unbound"),
    (lambda c, t: t.pop("session_id"), "turn_session_unbound"),
    (lambda c, t: t.update(
        bound={**t["bound"], "event_id": "generated-journey-9"}),
     "turn_binding_foreign"),
    (lambda c, t: t.pop("bound"), "turn_binding_foreign"),
    (lambda c, t: t.update(
        bound={**t["bound"], "session_conflict": "two closed sessions"}),
     "turn_session_conflict"),
    (lambda c, t: t.update(bound={**t["bound"], "session_id": 9}),
     "turn_session_conflict"),
    (lambda c, t: t.update(
        session_id=9, bound={**t["bound"], "session_id": 9},
        session_settlement={"status": "unavailable"}),
     "session_settlement_unobserved"),
    (lambda c, t: t.update(settlement=None), "settlement_unobserved"),
    (lambda c, t: t.update(settlement={"status": "unavailable"}),
     "settlement_unobserved"),
    (lambda c, t: t.update(turn_outcome=None), "turn_outcome_unobserved"),
    (lambda c, t: t.update(turn_outcome={"status": "unconfirmed"}),
     "turn_outcome_unobserved"),
    (lambda c, t: t.update(final_response={"status": "unavailable"}),
     "final_response_unobserved"),
    (lambda c, t: t.update(model_calls=[{"messages": "torn"}]),
     "subject_instructions_unobserved"),
])
def test_an_incomplete_turn_observation_names_its_cause(damage, cause):
    ctx, trajectory = _ctx(), _trajectory()
    damage(ctx, trajectory)
    assert cause in _turn_causes(ctx, trajectory, ())


def test_a_complete_turn_observation_has_no_gap():
    assert _turn_causes(_ctx(), _trajectory(), ()) == []
    assert turn_gap(_trajectory()) == []
    assert observation_gap(_trajectory(), ()) == []


def test_an_invalid_earlier_occasion_is_named_never_empty_history():
    earlier = ((None, {"invalid": "turn 0 observation failed: ValueError"}),)
    causes = observation_gap(_trajectory(), earlier)
    assert any("turn 0 observation failed" in c for c in causes)


def _span(name: str, trace_id: str, **attrs: Any) -> dict[str, Any]:
    return {"kind": "span_started", "name": name, "trace_id": trace_id,
            "attributes": attrs}


def test_bound_turn_events_correlate_the_occasions_own_traces():
    """The queue journal spans and the turn's own generation root stamp
    `nora.bridge.event_id`; everything on those traces — the model call
    the separate `engagement` trace carried — belongs to this occasion."""
    window = _ordered([
        _span("queue.claim", "t-claim",
              **{"nora.bridge.event_id": "generated-journey-2"}),
        _span("queue.attempt", "t-attempt",
              **{"nora.bridge.event_id": "generated-journey-2"}),
        _span("engagement", "t-generation",
              **{"nora.bridge.event_id": "generated-journey-2",
                 "nora.bridge.attempt_id": "w-43:0"}),
        {"kind": "call_started", "trace_id": "t-generation",
         "invocation_id": "inv-1"},
        _span("queue.attempt", "t-foreign",
              **{"nora.bridge.event_id": "generated-journey-1"}),
        {"kind": "call_started", "trace_id": "t-foreign",
         "invocation_id": "inv-2"},
        {"kind": "log", "trace_id": "t-generation",
         "message": "generated"},
        {"kind": "log", "message": "no trace"},
    ])
    bound, traces = bound_turn_events(
        window, event_id="generated-journey-2", session_id=None)
    assert set(traces) == {"t-claim", "t-attempt", "t-generation"}
    assert [e for e in bound if e.get("invocation_id") == "inv-1"]
    assert not [e for e in bound if e.get("trace_id") == "t-foreign"]
    assert not [e for e in bound if e.get("invocation_id") == "inv-2"]


def test_bound_turn_events_never_binds_the_unstamped_window():
    """A turn with no canonical identity binds nothing — the unmarked
    rows around it are provenance, never its acts."""
    window = _ordered([
        _span("queue.attempt", "t-attempt"),
        {"kind": "call_started", "trace_id": "t-attempt"},
        _span("queue.attempt", "t-foreign",
              **{"nora.bridge.event_id": "generated-journey-1"}),
        {"kind": "call_started", "trace_id": "t-foreign"},
    ])
    bound, traces = bound_turn_events(
        window, event_id=None, session_id=None)
    assert bound == [] and traces == []


def test_bound_turn_events_joins_the_bound_session_by_identity():
    window = _ordered([*_session_events(7), *_session_events(9)])
    bound, _traces = bound_turn_events(
        window, event_id="generated-journey-2", session_id=7)
    rows = [e for e in bound
            if str(e.get("name", "")).startswith("nora.session.")]
    ids = {json.loads(e["attributes"]["body"])["session_id"]
           for e in rows}
    assert ids == {7}
    names = {e["name"] for e in rows}
    assert {"nora.session.started", "nora.session.settled"} <= names


async def test_the_bound_session_is_the_stimuluss_own_closed_claim(
        clean_pg_url):
    """Positive control: one claim, closed by the session that consumed
    it — the turn's session binding is production's, not inferred."""
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(STIMULUS_CLAIM).values(
                unit=UNIT, stimulus_id=5, wake_id=1))
            await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
                wake_id=1, unit=UNIT, session_id=7, outcome="closed",
                at_ms=1235))
        bound = await stimulus_session(engine, 5)
        assert bound["session_id"] == 7 and bound["wake_id"] == 1
        assert bound["session_conflict"] is None
    finally:
        await engine.dispose()


async def test_a_failed_claim_followed_by_a_closed_one_binds(
        clean_pg_url):
    """The ordinary retry order: the latest claim's closed outcome owns
    the session; the earlier failed claim is provenance."""
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            for wake_id in (1, 2):
                await conn.execute(sa.insert(STIMULUS_CLAIM).values(
                    unit=UNIT, stimulus_id=5, wake_id=wake_id))
            await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
                wake_id=1, unit=UNIT, session_id=None, outcome="failed",
                at_ms=1235))
            await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
                wake_id=2, unit=UNIT, session_id=8, outcome="closed",
                at_ms=1236))
        bound = await stimulus_session(engine, 5)
        assert bound["session_id"] == 8 and bound["wake_id"] == 2
        assert bound["session_conflict"] is None
    finally:
        await engine.dispose()


async def test_an_unclaimed_or_pending_stimulus_binds_no_session(
        clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        bound = await stimulus_session(engine, 5)
        assert bound["session_id"] is None
        assert bound["session_conflict"] is None
        async with engine.begin() as conn:
            await conn.execute(sa.insert(STIMULUS_CLAIM).values(
                unit=UNIT, stimulus_id=5, wake_id=1))
        bound = await stimulus_session(engine, 5)
        assert bound["wake_id"] == 1 and bound["session_id"] is None
        assert bound["session_conflict"] is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("claims,outcomes", [
    ([(1, ), (2, )],
     [(1, 7, "closed"), (2, 8, "closed")]),
    ([(1, ), (2, )],
     [(1, 7, "closed"), (2, None, "failed")]),
])
async def test_conflicting_session_evidence_is_named_never_resolved(
        clean_pg_url, claims, outcomes):
    """Two closed consumptions, or a closed claim a newer claim
    followed, contradict which session the stimulus settled to."""
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            for (wake_id,) in claims:
                await conn.execute(sa.insert(STIMULUS_CLAIM).values(
                    unit=UNIT, stimulus_id=5, wake_id=wake_id))
            for wake_id, session_id, outcome in outcomes:
                await conn.execute(sa.insert(
                    COGNITION_WAKE_OUTCOME).values(
                        wake_id=wake_id, unit=UNIT, session_id=session_id,
                        outcome=outcome, at_ms=1235))
        bound = await stimulus_session(engine, 5)
        assert bound["session_conflict"]
        assert bound["session_id"] is None
    finally:
        await engine.dispose()


async def test_a_behavior_expectation_reaches_both_judges():
    judges = CountingJudges()
    rows = await _judge_turn(
        judges, MODELS, BEHAVIOR, 1, _Empty(), _Empty(),
        _trajectory(), ())
    (row,) = rows
    assert row["expectation"]["turn"] == 1
    assert row["expectation"]["check"] == "Cedar Hall"
    assert sorted(judges.calls) == sorted(MODELS)
    assert len(row["answers"]) == 2


@pytest.mark.parametrize("mutate", [
    # The declared send's `send_reply` turn produced no output at all:
    # a missing decision, never intentional restraint.
    lambda ctx: _turn_outcome(ctx, [], _effects()),
    # Another occasion's confirmed send is in the interval — owner-bound
    # evidence refuses it at the door before any judge request.
    lambda ctx: _turn_outcome(
        ctx, [_action(owner_id="w-99")],
        _effects(action_id="w-99:send:0")),
    # Ingest and its stimulus settled but the admission's engagement
    # produced no turn work — ingestion alone proves nothing.
    lambda ctx: _turn_outcome(
        {**ctx, "work": _work()}, [], _effects()),
    # The platform refused the only bound send.
    lambda ctx: _turn_outcome(ctx, [_action(state="rejected")],
                              _effects()),
])
async def test_unproven_turn_output_spends_no_judge(mutate):
    """The actual measurement consumer: an outcome that cannot type
    itself `unconfirmed` is refused by name, zero judge traffic."""
    ctx = _ctx()
    trajectory = _trajectory()
    trajectory["turn_outcome"] = mutate(ctx)
    assert trajectory["turn_outcome"]["status"] == "unconfirmed"
    judges = CountingJudges()
    rows = await _judge_turn(
        judges, MODELS, BEHAVIOR, 1, _Empty(), _Empty(), trajectory, ())
    (row,) = rows
    assert judges.calls == []
    assert row["verdict"] == ERROR
    assert any("turn_outcome_unobserved" in a["reason"]
               for a in row["answers"])


async def test_a_conflicting_session_spends_no_judge():
    judges = CountingJudges()
    trajectory = _trajectory()
    trajectory["bound"]["session_id"] = 9
    rows = await _judge_turn(
        judges, MODELS, BEHAVIOR, 1, _Empty(), _Empty(), trajectory, ())
    (row,) = rows
    assert judges.calls == []
    assert row["verdict"] == ERROR
    assert any("turn_session_conflict" in a["reason"]
               for a in row["answers"])


async def test_a_turn_anchored_claim_skips_wake_bound_expectations():
    judges = CountingJudges()
    rows = await _judge_turn(
        judges, MODELS,
        [Expectation(wake=0, polarity="must", kind="note", text="v1"),
         *BEHAVIOR], 1, _Empty(), _Empty(), _trajectory(), ())
    assert len(rows) == 1 and sorted(judges.calls) == sorted(MODELS)


def test_freeze_turn_records_identity_and_evidence(tmp_path):
    trajectory = _trajectory()
    row = _freeze_turn(
        tmp_path, _ctx(), trajectory, _Empty(), [], _Empty(),
        unit=UNIT, step_error=None)
    frozen = json.loads((tmp_path / "turn-1.json").read_text())
    assert frozen["turn"] == 1 and frozen["unit"] == UNIT
    assert frozen["declared_input"]["text"] == "generated follow-up"
    assert frozen["declared_input"]["event_id"] == "generated-journey-2"
    assert frozen["trajectory"]["turn_outcome"]["status"] == "delivered"
    assert frozen["trajectory"]["bound"]["event_id"] == (
        "generated-journey-2")
    assert row["outcome"] == "delivered" and row["status"] != ERROR


def test_an_unobserved_turn_freezes_an_error_row(tmp_path):
    ctx = _ctx(event=None, work=None)
    row = _turn_error(tmp_path, 0, ctx, step="observe",
                      message="TimeoutError", unit=UNIT)
    assert row["status"] == ERROR and row["step"] == "observe"
    frozen = json.loads((tmp_path / "turn-0.json").read_text())
    assert frozen["error"]["step"] == "observe"
    assert frozen["declared_input"]["text"] == "generated follow-up"


@pytest.mark.parametrize("broken", [
    {"version": 3, **{k: v for k, v in V2_PLAN.items() if k != "version"}},
    {**V2_PLAN, "expectations": [
        {"turn": 0, "polarity": "must", "kind": "note", "text": "x"}]},
    {**V2_PLAN, "expectations": [
        {"turn": 7, "polarity": "must", "kind": "behavior", "text": "x"}]},
    {**V2_PLAN, "expectations": [
        {"turn": 0, "polarity": "must", "kind": "behavior", "text": "x",
         "check": 4}]},
    {**V2_PLAN, "expectations": [
        {"turn": 0, "polarity": "must", "kind": "behavior", "text": "x",
         "wake": 0}]},
])
def test_a_version2_plan_refuses_wake_bound_or_kind_bound_claims(broken):
    assert isinstance(_plan_error(broken), str)


def test_a_version2_plan_binds_turns_and_zero_wakes():
    assert _plan_error(dict(V2_PLAN)) is None


def test_a_session_window_inside_the_turn_stays_visible():
    """The turn's evidence is everything its send produced — a session's
    own span is part of the observation, not filtered out."""
    window = _ordered(_session_events(7))
    assert turn_events(window) == window
