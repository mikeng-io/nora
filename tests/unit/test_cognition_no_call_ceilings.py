"""A Cognition session has no call ceiling of any kind (DEC-247).

It is bounded by rounds (`turns`), total tokens, output tokens and the wall
clock, and by nothing per tool. Driven through the real
`LiveCognitionAgentRunner`, the real `DreamGuard` and a real Strands loop with
a scripted model, so the assertion is on what the loop did, not on what the
guard was constructed with. The other adaptors keep every ceiling: the same
guard, built the way the Dreamer builds it, still vetoes.

What each test guards is named in `scripts/mutations/cognition-rounds.json`.
"""

from __future__ import annotations

from dataclasses import replace

from nora.agent import factory
from nora.agent.dream_guard import DreamGuard
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.app.cognition_session import AdaptorBudget
from nora.obs.metrics import Metrics
from tests.unit.test_dream_memory_budget import (
    BUDGET,
    _Adaptor,
    _After,
    _Before,
    _runtime,
    _ScriptedModel,
    _Tools,
)

RULES = ("tool-budget", "memory-budget", "pass-budget", "R-7", "R-99")


class _Models:
    def __init__(self, model: _ScriptedModel) -> None:
        self._model = model

    def create(self) -> _ScriptedModel:
        return self._model


def _runner(model: _ScriptedModel, metrics: Metrics) -> LiveCognitionAgentRunner:
    return LiveCognitionAgentRunner(
        models=_Models(model), metrics=metrics,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        param_names={"about": frozenset({"person"}), "note_read": frozenset({"person"})},
    )


def _budget(turns: int) -> AdaptorBudget:
    return AdaptorBudget(
        turns=turns, total_tokens=10_000_000, output_tokens=1_000_000,
        timeout_seconds=30.0,
    )


def _vetoes_for(metrics: Metrics, adaptor: str, rule: str) -> float:
    return metrics.get("nora_dream_guard_vetoes_total", adaptor=adaptor, rule=rule)


def _vetoes(metrics: Metrics, rule: str) -> float:
    return metrics.get("nora_dream_guard_vetoes_total", adaptor="cognition", rule=rule)


async def test_fifty_calls_of_one_tool_are_not_vetoed():
    """`about` is a memory verb with a per-tool budget of 4 in the registry and a
    per-pass ration; 50 calls of it in one session all run and nothing is
    vetoed or refused."""
    tools = _Tools(["about", "note_read"])
    model = _ScriptedModel(["about"] * 50 + [None])
    metrics = Metrics()
    live = _runner(model, metrics)

    result = await live.run(
        system_prompt="s", prompt="p",
        tools=tools.build(key="k", names=frozenset(tools.read_only())),
        budget=_budget(turns=200), trace_attributes={"cognition.items": "1"},
    )

    assert tools.ran == ["about"] * 50
    assert result.stop_reason == "end_turn"
    for rule in RULES:
        assert _vetoes(metrics, rule) == 0, rule


async def test_a_session_stops_at_its_turn_cap_and_says_so():
    """With no call ceiling the round cap is what bounds a model that never
    stops asking, and it is named, not inferred."""
    tools = _Tools(["about"])
    model = _ScriptedModel(["about"] * 60)
    metrics = Metrics()

    result = await _runner(model, metrics).run(
        system_prompt="s", prompt="p", tools=tools.build(key="k", names=frozenset({"about"})),
        budget=_budget(turns=10), trace_attributes={},
    )

    assert result.stop_reason == "limit_turns"
    assert 1 <= len(tools.ran) <= 10
    for rule in RULES:
        assert _vetoes(metrics, rule) == 0, rule


async def test_a_session_still_refuses_an_argument_the_tool_does_not_declare():
    """R-99 is a safety check, not a budget: the session keeps it."""
    tools = _Tools(["about"])
    model = _ScriptedModel(["about", None], inputs=['{"file": "x"}'])
    metrics = Metrics()

    await _runner(model, metrics).run(
        system_prompt="s", prompt="p", tools=tools.build(key="k", names=frozenset({"about"})),
        budget=_budget(turns=10), trace_attributes={},
    )

    assert tools.ran == []
    assert _vetoes(metrics, "R-99") == 1


async def test_a_session_still_refuses_a_tool_it_does_not_hold():
    tools = _Tools(["about"])
    model = _ScriptedModel(["note_read", None])
    metrics = Metrics()

    await _runner(model, metrics).run(
        system_prompt="s", prompt="p", tools=tools.build(key="k", names=frozenset({"about"})),
        budget=_budget(turns=10), trace_attributes={},
    )

    assert _vetoes(metrics, "R-7") == 1


def _veto_after(guard: DreamGuard, calls: int) -> str | None:
    """Run `calls` completed calls of `about`, then ask for one more."""
    for i in range(calls):
        before = _Before("about", f"t{i}")
        guard._before_tool(before)
        assert before.cancel_tool is None
        guard._after_tool(_After("about", f"t{i}"))
    last = _Before("about", "tlast")
    guard._before_tool(last)
    return last.cancel_tool


def test_every_other_adaptor_keeps_its_per_tool_ceiling():
    """The default guard, which is how the Dreamer builds it for every adaptor
    but the session, still vetoes the call over a tool's ceiling.

    "Every other adaptor" is every adaptor but TWO, deliberately: `cognition` (DEC-247) and
    `subagent`, which DEC-247's amendment (DEC-282) exempts by name. The sub-agent's own half
    is `tests/unit/test_subagent_worker.py::
    test_the_workers_guard_has_no_call_ceiling_and_every_other_adaptors_keeps_its_own`."""
    guard = DreamGuard(adaptor="proactive", budgets={"about": 4}, metrics=Metrics())
    message = _veto_after(guard, 4)
    assert message is not None
    assert "You already called about 4 time(s)" in message


async def test_a_dreamer_adaptors_guard_built_by_the_dreamer_still_vetoes_per_tool():
    """Through the REAL `DreamerRuntime._sample` (not a `DreamGuard` built by
    hand): a non-cognition adaptor (`proactive`) asking `about` 30 times in one
    pass is stopped at the tool's own ceiling, the call's body never runs past
    it, and the veto is counted under the `tool-budget` rule. The pass-wide and
    memory ceilings are lifted here so ONLY the per-tool one can be what binds."""
    tools = _Tools(["about"])
    model = _ScriptedModel(["about"] * 30 + [None])
    budget = replace(BUDGET, turns=60, max_memory_calls=None, max_tool_calls=None)
    runtime, metrics = _runtime(model, tools, budget=budget)

    await runtime.run(adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=())

    assert 0 < len(tools.ran) < 30
    assert _vetoes_for(metrics, "proactive", "tool-budget") >= 1


def test_the_session_guard_is_the_same_class_with_the_ceiling_switched_off():
    """The same four calls, the same budget map, one flag: the only difference
    between the session and every other adaptor is `per_tool_ceilings`."""
    guard = DreamGuard(
        adaptor="cognition", budgets={"about": 4}, metrics=Metrics(),
        per_tool_ceilings=False,
    )
    assert _veto_after(guard, 4) is None
