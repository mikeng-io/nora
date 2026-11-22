"""One outcome contract over both implementations of the wake-pass port."""

import pytest

from nora.memory import WakeClosed, WakeFailed, WakePass
from nora.testing.scripted_agent import ScriptedTurn
from nora.testing.scripted_wake_pass import ScriptedWakePass
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_wake_runner import WakeModel, claim, observation, runner


@pytest.mark.parametrize("implementation", ("runner", "scripted"))
async def test_wake_pass_returns_the_outcome_of_each_claimed_set(scenario, implementation):
    first, second = "fixture-claimed-first", "fixture-claimed-second"
    await scenario.item(first)
    await scenario.item(second)
    port: WakePass
    if implementation == "runner":
        script = [ScriptedTurn(tool_calls=[("memory_read", {"id": first})])]
        port = runner(scenario, WakeModel(script), turns=1)
    else:
        port = ScriptedWakePass({(first,): WakeFailed("bound"), (second,): WakeClosed()})
    assert await port.run(claim(observation(first))) == WakeFailed("bound")
    assert await port.run(claim(observation(second))) == WakeClosed()
