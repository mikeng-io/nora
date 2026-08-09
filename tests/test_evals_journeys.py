"""D3: the four real-incident journeys, graded by the D2 routing evaluators.

Offline (always-on): for each journey, the INTENDED tool path passes all three
routing checks, and the REGRESSION path (the trap tool a near-identical question
would take) fails. This pins the ground truth without a model.

Live (network-marked, excluded by default): run each real message through Nora's
real model and grade what she actually routed to — the real regression gate.
"""

import os

import pytest

from evals.evaluators import Result
from evals.harness import Case, Trajectory, run_case
from evals.journeys import HARD_ROUTING_CHECKS, JOURNEYS, grade, journeys_gate

# The trap tool each journey must NOT take — the near-identical-question sibling.
TRAPS = {
    "weather": "web_search",
    "carpark": "place_info",
    "mall": "web_search",
    "where_work": "where_is",
}


def _traj(tool_calls) -> Trajectory:
    return Trajectory(
        tool_calls=list(tool_calls), sent=["reply"], reply="reply", honcho_feeds=1
    )


@pytest.mark.parametrize("journey", JOURNEYS, ids=lambda j: j.intent.name)
def test_intended_path_passes_all_routing_checks(journey):
    required = sorted(journey.intent.required)
    intended = _traj([*required, "send_reply"])
    results = grade(journey, intended)
    assert all(r.passed for r in results.values()), {
        k: r.reason for k, r in results.items() if not r.passed
    }


@pytest.mark.parametrize("journey", JOURNEYS, ids=lambda j: j.intent.name)
def test_regression_path_is_caught(journey):
    trap = TRAPS[journey.intent.name]
    # The model took the plausible-but-wrong tool instead of the right one.
    regressed = _traj([trap, "send_reply"])
    results = grade(journey, regressed)
    # Missing the required tool is caught by PathMatchesIntent; the trap being a
    # graph sibling is caught by NoSiblingLeakage. At least one must fire, and for
    # these four it is both.
    assert not results["PathMatchesIntent"].passed
    assert not results["NoSiblingLeakage"].passed


def test_every_journey_message_is_nonempty_and_distinct():
    messages = [j.message for j in JOURNEYS]
    assert all(m.strip() for m in messages)
    assert len(set(messages)) == len(messages)


# --- journeys_gate (D4 tier 2 gate logic) ----------------------------------


def _graded(**overrides) -> dict[str, Result]:
    base = {
        "PathMatchesIntent": Result(True),
        "NoSiblingLeakage": Result(True),
        "ConvergesInBudget": Result(True),
    }
    base.update(overrides)
    return base


def test_gate_passes_when_hard_checks_pass():
    assert journeys_gate(_graded())


def test_gate_fails_when_a_hard_check_fails():
    assert not journeys_gate(_graded(PathMatchesIntent=Result(False, "missing weather")))


def test_gate_ignores_advisory_failures():
    # NoSiblingLeakage is advisory today (backend-confounded) — its failure must
    # NOT fail the gate.
    assert "NoSiblingLeakage" not in HARD_ROUTING_CHECKS
    assert journeys_gate(_graded(NoSiblingLeakage=Result(False, "leaked into place_info")))


def test_gate_is_fail_closed_on_missing_check():
    # A hard check that was never run counts as a failure, not a silent pass.
    assert not journeys_gate({"NoSiblingLeakage": Result(True)})


def test_journeys_is_a_registered_suite():
    from evals.run import SUITES

    assert "journeys" in SUITES


# --- live regression gate (network-marked) ---------------------------------


@pytest.mark.network
@pytest.mark.parametrize("journey", JOURNEYS, ids=lambda j: j.intent.name)
async def test_live_journey_routes_correctly(journey):
    assert os.environ.get("OMNIROUTE_API_KEY"), "OMNIROUTE_API_KEY must be set"
    trajectory = await run_case(Case(name=journey.intent.name, input=journey.message), live=True)
    results = grade(journey, trajectory)
    failed = {k: r.reason for k, r in results.items() if not r.passed}
    # PathMatchesIntent and NoSiblingLeakage are correctness; a failure here is a
    # real routing regression in the live model. ConvergesInBudget is advisory
    # (a slow-but-correct turn still answered), so it is reported, not asserted.
    assert results["PathMatchesIntent"].passed, (journey.intent.name, trajectory.tool_calls, failed)
    assert results["NoSiblingLeakage"].passed, (journey.intent.name, trajectory.tool_calls, failed)
