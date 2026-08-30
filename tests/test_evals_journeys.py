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
    # F2 held-out paraphrases — same intent, same trap, different words.
    "weather_alt": "web_search",
    "carpark_alt": "place_info",
    "mall_alt": "web_search",
    "where_work_alt": "where_is",
    # The remember write lane (2026-08-29): a 記低 ask leaking into the NOTE
    # lane is the fresh 「記低咗-wrote-nowhere」 family; a 「記唔記得」 recall
    # ask answered by WRITING (remember) is the reverse leak the remember-about
    # edge exists to catch. Both edges are in SEED_INTENT_GRAPH.
    "remember": "note_create",
    "remember_alt": "note_create",
    "about_recall": "remember",
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


def test_no_journey_tool_is_live_unmeasurable():
    """Every tool a journey requires OR names as a trap must be reconstructable in
    a live run — else the live gate reads an instrument blindness as a verdict.
    Fails closed if a journey (or a TRAP) ever names an `UNMEASURABLE_LIVE` tool."""
    from evals.evaluators import UNMEASURABLE_LIVE

    used = {t for j in JOURNEYS for t in j.intent.required} | set(TRAPS.values())
    assert used.isdisjoint(UNMEASURABLE_LIVE), used & UNMEASURABLE_LIVE


def test_all_journeys_are_fully_backed_so_every_check_is_hard():
    """F4's payoff: with the canned eval backends, every journey's tools (required
    + siblings) are backed, so NoSiblingLeakage and ConvergesInBudget are HARD for
    all of them — not just PathMatchesIntent. If a new journey touches an unbacked
    tool (e.g. a future MCP tool), this reddens and its leakage silently drops to
    advisory instead of the confound being hidden."""
    from evals.journeys import hard_checks_for

    for journey in JOURNEYS:
        hard = hard_checks_for(journey)
        assert "NoSiblingLeakage" in hard, journey.intent.name
        assert "ConvergesInBudget" in hard, journey.intent.name


def test_every_journey_required_tool_is_in_the_graph():
    """A required tool with NO graph edges makes `NoSiblingLeakage` vacuous — it
    passes no matter what she leaks into (external review, 2026-08-10). Forcing
    every journey's required tool to be a graph node means its confusability was
    declared, not forgotten; a new journey whose tool has no edges fails here
    rather than silently weakening the leakage check."""
    from evals.evaluators import SEED_INTENT_GRAPH

    nodes = SEED_INTENT_GRAPH.nodes()
    for journey in JOURNEYS:
        missing = journey.intent.required - nodes
        assert not missing, f"{journey.intent.name}: required tool(s) not in graph: {missing}"


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


# --- F8a: majority-of-k journeys gate ---------------------------------------


def _weather_journey():
    from evals.evaluators import Intent
    from evals.journeys import Journey

    return Journey(
        intent=Intent(name="w", required=frozenset({"weather"}), budget=1),
        message="x", note="",
    )


async def _run_with_scripted(monkeypatch, trajectories, *, samples):
    """Drive `_run_journeys_live` offline by scripting what `run_case` returns."""
    from evals import run as run_mod

    seq = iter(trajectories)

    async def fake_run_case(case, *, live=False):
        return next(seq)

    monkeypatch.setattr(run_mod, "run_case", fake_run_case)
    return await run_mod._run_journeys_live([_weather_journey()], samples=samples)


async def test_journey_passes_on_majority(monkeypatch):
    # 2 of 3 runs reach weather → strict majority → journey PASSES.
    ok, results = await _run_with_scripted(monkeypatch, [
        _traj(["weather", "send_reply"]),
        _traj(["weather", "send_reply"]),
        _traj(["web_search", "send_reply"]),  # missed weather this run
    ], samples=3)
    assert ok
    assert results[0]["gate_pass"] == 2


async def test_journey_ties_fail_closed(monkeypatch):
    # 1 of 2 runs pass — a tie — must FAIL (strict majority, n_pass*2 > samples).
    ok, results = await _run_with_scripted(monkeypatch, [
        _traj(["weather", "send_reply"]),
        _traj(["web_search", "send_reply"]),
    ], samples=2)
    assert not ok
    assert results[0]["gate_pass"] == 1


# --- live regression gate (network-marked) ---------------------------------


@pytest.mark.network
@pytest.mark.parametrize("journey", JOURNEYS, ids=lambda j: j.intent.name)
async def test_live_journey_routes_correctly(journey):
    assert os.environ.get("OMNIROUTE_API_KEY"), "OMNIROUTE_API_KEY must be set"
    trajectory = await run_case(Case(name=journey.intent.name, input=journey.message), live=True)
    results = grade(journey, trajectory)
    failed = {k: r.reason for k, r in results.items() if not r.passed}
    # Assert exactly what the gate asserts — the HARD routing checks
    # (`journeys_gate` → PathMatchesIntent today), no more. NoSiblingLeakage and
    # ConvergesInBudget are ADVISORY in the gate because a down offline backend
    # drives fallback calls that inflate them (the D3 confound recorded in
    # DEC-76); asserting them here would flake this test on exactly the runs the
    # gate is designed to tolerate.
    assert journeys_gate(results), (journey.intent.name, trajectory.tool_calls, failed)


# ── the stop_reason instrument (2026-08-10) ─────────────────────────────────

def test_stop_reason_is_read_back_off_the_metric():
    """THE PROBE. `Trajectory` carried no stop reason, and its absence made a
    root-cause analysis unfalsifiable: grepping an eval log for
    `limit_output_tokens` returned zero, which looked like evidence and was a
    null result from an instrument that could not measure the thing.

    `end_turn` with no reply and `limit_output_tokens` are different failures —
    one is the model declining to speak, the other is the loop being cut off —
    and the gate could not tell them apart. Delete `_stop_reason_from_metrics`
    and this reds."""
    from evals.harness import _stop_reason_from_metrics
    from nora.obs.metrics import Metrics

    m = Metrics()
    m.inc("nora_llm_stop_reason_total", reason="end_turn", audience="tag")
    assert _stop_reason_from_metrics(m) == "end_turn"


def test_no_stop_reason_recorded_is_none_not_a_guess():
    """A turn that faulted before the model was invoked has no stop reason.
    Inventing `"unknown"` would make that indistinguishable from a real one —
    the same fail-closed rule the rest of the codebase uses for «I could not
    check» vs «there is nothing»."""
    from evals.harness import _stop_reason_from_metrics
    from nora.obs.metrics import Metrics

    assert _stop_reason_from_metrics(Metrics()) is None


def test_the_winning_stop_reason_is_deterministic_on_a_tie():
    """Two series with equal counts must not resolve by dict order, or the same
    run reports different reasons on different days."""
    from evals.harness import _stop_reason_from_metrics
    from nora.obs.metrics import Metrics

    m = Metrics()
    m.inc("nora_llm_stop_reason_total", reason="end_turn", audience="tag")
    m.inc("nora_llm_stop_reason_total", reason="limit_turns", audience="tag")
    assert _stop_reason_from_metrics(m) == "end_turn"      # alphabetical on a tie
    m.inc("nora_llm_stop_reason_total", reason="limit_turns", audience="tag")
    assert _stop_reason_from_metrics(m) == "limit_turns"   # count beats alphabet
