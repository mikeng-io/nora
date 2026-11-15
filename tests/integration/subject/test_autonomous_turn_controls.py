"""Canonical evidence gates for the autonomous journey's later-turn
observation.

A produced turn binds its declared input, its settled admission rows and
a typed outcome — delivered, intentional non-delivery, or the unconfirmed
middle — before any judge sees it. A missing, foreign or unfinished
observation freezes ERROR with ZERO judge traffic; only complete turn
evidence reaches both judges. State is never the oracle: the same
observed reply must judge identically whichever store the diff shows.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from evals.rebuilt.autonomous_journey import (
    _freeze_turn,
    _judge_turn,
    _plan_error,
    _turn_causes,
    _turn_error,
    _turn_outcome,
)
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.judges import observation_gap, turn_gap
from scripts.cognition_loop.verdicts import ERROR
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


def _ctx(**overrides: Any) -> dict[str, Any]:
    ctx = {
        "index": 1, "input": "generated follow-up",
        "event_id": "generated-journey-2", "at_ms": 2000,
        "cursor": (0, 0), "boundary": object(), "engine": object(),
        "event": {"id": 9, "state": "done"},
        "work": [{"kind": "admission", "state": "done"}],
    }
    ctx.update(overrides)
    return ctx


def _trajectory() -> dict[str, Any]:
    return {
        "turn": 1, "at_ms": 2000, "unit": UNIT,
        "session_id": None,
        "declared_input": {"text": "generated follow-up",
                           "event_id": "generated-journey-2",
                           "at_ms": 2000},
        "model_calls": [{"model": "generated-subject", "messages": [
            {"role": "system", "content": "generated subject instructions"},
            {"role": "user", "content": "generated follow-up"}]}],
        "tool_calls": [{"tool": "send_reply",
                        "arguments": {"text": "the venue is Cedar Hall"},
                        "status": "ok", "result": {"status": "ok"}}],
        "effects": {"status": "observed",
                    "items": [{"kind": "delivery", "ref": "delivery:b:1"}],
                    "tools": []},
        "settlement": {"status": "observed", "event": "done", "work": []},
        "turn_outcome": {"status": "delivered",
                         "said": ["the venue is Cedar Hall"]},
        "final_response": {"status": "observed",
                           "text": "the venue is Cedar Hall"},
    }


def test_turn_outcome_is_typed_never_inferred():
    trajectory = _trajectory()
    confirmed = [{"action_id": "a1", "op": "send", "state": "confirmed"}]
    outcome = _turn_outcome(confirmed, trajectory, _ctx()["work"])
    assert outcome["status"] == "delivered"
    assert outcome["said"] == ["the venue is Cedar Hall"]
    assert outcome["deliveries"] == ["delivery:b:1"]
    none = _turn_outcome([], trajectory, _ctx()["work"])
    assert none["status"] == "intentional_non_delivery"
    for state in ("prepared", "started", "rejected", "uncertain"):
        stuck = [{"action_id": "a1", "op": "send", "state": state}]
        assert _turn_outcome(stuck, trajectory,
                             _ctx()["work"])["status"] == "unconfirmed"


@pytest.mark.parametrize("damage", [
    lambda ctx, trajectory: ctx.update(event=None),
    lambda ctx, trajectory: ctx.update(event={"state": "failed"}),
    lambda ctx, trajectory: ctx.update(work=[]),
    lambda ctx, trajectory: ctx.update(
        work=[{"kind": "admission", "state": "started"}]),
    lambda ctx, trajectory: trajectory.update(effects={"status": "gone"}),
    lambda ctx, trajectory: trajectory.update(declared_input=None),
    lambda ctx, trajectory: trajectory.update(settlement=None),
    lambda ctx, trajectory: trajectory.update(
        settlement={"status": "unavailable"}),
    lambda ctx, trajectory: trajectory.update(session_id="s-9"),
    lambda ctx, trajectory: trajectory.pop("session_id"),
    lambda ctx, trajectory: trajectory.update(turn_outcome=None),
    lambda ctx, trajectory: trajectory.update(
        turn_outcome={"status": "unconfirmed"}),
])
def test_an_incomplete_turn_observation_names_its_cause(damage):
    ctx, trajectory = _ctx(), _trajectory()
    damage(ctx, trajectory)
    causes = _turn_causes(ctx, trajectory, ())
    assert causes, "a torn observation must refuse before any judge"


def test_a_complete_turn_observation_has_no_gap():
    assert _turn_causes(_ctx(), _trajectory(), ()) == []
    assert turn_gap(_trajectory()) == []
    assert observation_gap(_trajectory(), ()) == []


def test_an_invalid_earlier_occasion_is_named_never_empty_history():
    earlier = ((None, {"invalid": "turn 0 observation failed: ValueError"}),)
    causes = observation_gap(_trajectory(), earlier)
    assert any("turn 0 observation failed" in c for c in causes)


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
    from scripts.cognition_loop.trajectory import turn_events
    window = _ordered(_session_events(7))
    assert turn_events(window) == window
