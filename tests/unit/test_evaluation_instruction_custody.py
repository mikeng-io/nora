"""Instruction absence requires a positive canonical no-session receipt."""

import json
from copy import deepcopy

import pytest

from nora.subject.request_evidence import subject_instructions
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.state import StateDiff, UnitState
from tests.integration.subject.evidence_fixtures import judge_trajectory


def empty_session():
    return judge_trajectory(unit="room:generated", wake_id=11, session_id=None,
                           model_calls=[], effects={
        "status": "observed", "unit": "room:generated", "tools": [], "items": [{
            "source": "cognition_wake_outcome", "ref": "wake:generated-closed-receipt",
            "row": {"unit": "room:generated", "wake_id": 11,
                    "session_id": None, "outcome": "closed"}}]})


@pytest.mark.parametrize("damage", ["request", "session", "outcome", "foreign", "ref", "bool",
                                   "unit", "effect_unit", "row_unit"])
def test_only_matching_closed_no_session_receipt_establishes_instruction_absence(damage):
    trajectory = empty_session()
    assert subject_instructions(trajectory) == {"status": "observed", "mode": "no_session",
        "messages": [], "ref": "wake:generated-closed-receipt"}
    broken = deepcopy(trajectory)
    row = broken["effects"]["items"][0]["row"]
    if damage == "request":
        broken["model_calls"] = None
    elif damage == "session":
        broken.pop("session_id")
    elif damage == "outcome":
        row["outcome"] = "failed"
    elif damage == "foreign":
        row["wake_id"] = 22
    elif damage == "ref":
        broken["effects"]["items"][0].pop("ref")
    elif damage == "unit":
        broken.pop("unit")
    elif damage == "effect_unit":
        broken["effects"]["unit"] = "room:foreign"
    elif damage == "row_unit":
        row["unit"] = "room:foreign"
    else:
        broken["wake_id"], row["wake_id"] = 1, True
    assert subject_instructions(broken)["status"] == "unavailable"


class Judges:
    def __init__(self):
        self.calls, self.served = [], {}

    async def call(self, model, instruction, payload):
        self.calls.append(payload)
        self.served[model] = [{"x-omniroute-model": model + "-served"}]
        return json.dumps({"verdict": "met", "entry_ids": [],
                           "reason": "Generated custody control"})


@pytest.mark.parametrize("where", ["current", "earlier"])
async def test_missing_instruction_custody_stops_before_both_judges(where):
    current, prior = judge_trajectory(), judge_trajectory()
    judges = Judges()
    expectation = Expectation(0, "must_not", "note", "No generated note is created.")
    diff, state = StateDiff(kinds={}), UnitState(kinds={})
    good = await judge_expectation(judges, ("a", "b"), expectation, diff, state,
                                   current, ((diff, prior),))
    assert good.verdict == "PASS" and len(judges.calls) == 2
    judges.calls.clear()
    (current if where == "current" else prior)["model_calls"] = []
    bad = await judge_expectation(judges, ("a", "b"), expectation, diff, state,
                                  current, ((diff, prior),))
    assert bad.verdict == "ERROR" and judges.calls == []
    assert {answer.reason for answer in bad.answers} == {"subject instruction evidence unavailable"}
    none = empty_session()
    positive = await judge_expectation(judges, ("a", "b"), expectation, diff, state,
                                       none, ((diff, none),))
    assert positive.verdict == "PASS" and len(judges.calls) == 2
    assert judges.calls[0]["subject_instructions"]["mode"] == "no_session"
