"""One outcome contract over both implementations of the wake-pass port."""

import pytest

from nora.memory import WakeClosed, WakeFailed, WakePass
from nora.testing.scripted_agent import ScriptedTurn
from nora.testing.scripted_wake_pass import ScriptedWakePass
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_wake_runner import WakeModel, claim, observation, runner


@pytest.mark.parametrize("implementation", ("runner", "scripted"))
@pytest.mark.parametrize("bounded", (False, True), ids=("closed", "bound"))
async def test_wake_pass_returns_the_outcome_of_the_claimed_set(
    scenario, implementation, bounded,
):
    graph_id = "fixture-claimed-set"
    await scenario.item(graph_id)
    claimed = claim(observation(graph_id))
    expected = WakeFailed("bound") if bounded else WakeClosed()
    port: WakePass
    if implementation == "runner":
        script = [ScriptedTurn(tool_calls=[("memory_read", {"id": graph_id})])] if bounded else []
        port = runner(scenario, WakeModel(script), turns=1)
    else:
        port = ScriptedWakePass({(graph_id,): expected})
    assert await port.run(claimed) == expected
