"""The model loop enforces the registry accepted tool argument names."""

from nora.agent import factory
from nora.agent.dreamer import AdaptorBudget, DreamerRuntime
from nora.obs.metrics import Metrics
from tests.fakes import FixedModelSource
from tests.fixtures.dreamer_calls import _ScriptedModel, _Tools


async def test_a_dreamer_pass_refuses_an_unknown_argument_and_never_runs_the_tool():
    model = _ScriptedModel(["place_info", None], inputs=['{"bogus": "x"}'])
    tools = _Tools(["place_info"])
    metrics = Metrics()
    runtime = DreamerRuntime(
        models=FixedModelSource(model), tools=tools, metrics=metrics,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
    )

    await runtime.run_agent(
        system_prompt="Use the scoped read tool.", prompt="Read this room.",
        tools=tools.build(key="friends", names=frozenset({"place_info"})),
        budget=AdaptorBudget(turns=4, total_tokens=10_000, output_tokens=1_000, timeout_seconds=5),
        agent_name="cognition",
    )

    assert model.turn == 2
    assert tools.ran == []
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="cognition", rule="R-99",
    ) == 1
