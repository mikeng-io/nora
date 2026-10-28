"""The scripted runner and production Strands runner share tool-shape behavior."""

import json
from types import SimpleNamespace

import pytest

from nora.agent.factory import RetryPolicy
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.obs.metrics import Metrics
from nora.testing.comprehend import InMemoryComprehendRecords, ScriptedCognitionRunner
from nora.testing.memory_graph import FakeMemoryGraph
from tests.unit.test_comprehend import ITEM, PERSON, PLACE, WORK, WRITE, runner
from tests.unit.test_dream_memory_budget import _ScriptedModel


@pytest.mark.parametrize("implementation", ["scripted", "strands"])
async def test_wrong_call_shape_can_be_corrected_then_both_stages_settle(implementation):
    # Each entry is one SESSION, and each session's model emits actual tool-use
    # deltas and usage when run through the real Strands agent loop.
    trajectories = [
        [("memory_stage", {"bad_field": True}), ("memory_stage", WRITE)],
        *[[("profile_stage", {"proposals_in": []})] for _ in range(3)],
    ]
    if implementation == "scripted":
        model = ScriptedCognitionRunner(trajectories)
    else:
        models = [
            _ScriptedModel(
                [*[name for name, _ in script], None],
                inputs=[json.dumps(args) for _, args in script],
            )
            for script in trajectories
        ]
        model = LiveCognitionAgentRunner(
            models=SimpleNamespace(create=lambda: models.pop(0)),
            retry=RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
            metrics=Metrics(),
        )
    graph = FakeMemoryGraph()
    for entity in (PERSON, PLACE):
        await graph.upsert_entity(entity)
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    r = runner(graph, records, model)
    assert await r.run_once()
    assert graph.work[WORK.id].state == "settled"
    assert len(graph.relations) == 1
    assert [s["entity"][0] for s in records.shadows] == ["person", "room", "unit"]
    costs = [row["payload"]["total_tokens"] for row in records.receipts if row["part"] == "model"]
    assert len(costs) == 4 and all(c > 0 for c in costs)
