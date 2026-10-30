"""The scripted runner and production Strands runner share tool-shape behavior."""

import json
from types import SimpleNamespace

import pytest

from nora.agent.factory import RetryPolicy
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.domain.stimuli import UnitView
from nora.obs.metrics import Metrics
from nora.testing.comprehend import ScriptedCognitionRunner
from tests.unit.test_cognition_session_runner import NOW, _pending, _runner, proposal, wake_memory
from tests.unit.test_dream_memory_budget import _ScriptedModel


@pytest.mark.parametrize("implementation", ["scripted", "strands"])
async def test_wrong_call_shape_can_be_corrected_in_the_one_session(implementation):
    trajectory = [("memory_propose", {"bad_field": True}), ("memory_propose", proposal())]
    if implementation == "scripted":
        model = ScriptedCognitionRunner([trajectory])
    else:
        scripted = _ScriptedModel(["memory_propose", "memory_propose", None],
            inputs=[json.dumps(args) for _, args in trajectory])
        model = LiveCognitionAgentRunner(models=SimpleNamespace(create=lambda: scripted),
            retry=RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0), metrics=Metrics())
    memory = wake_memory()
    runner, store, _ = _runner(agent_runner=model, memory=memory)
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert len(store.sessions) == 1
    assert list(memory.records.claims.values()) == [[]]
