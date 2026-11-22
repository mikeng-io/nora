"""The composition root hands each Cognitive Graph stage the collaborator it calls."""

from nora.app.composition import build_dependencies
from tests.test_composition import cfg as cfg
from tests.test_composition import owned_dependencies as owned_dependencies


async def test_the_composed_verifier_resolves_an_origin_room_through_the_room_policy(cfg):
    deps = await build_dependencies(cfg)
    [verifier] = [run for stage, run in deps.graph_observe._executors
                  if stage == "intention_verify"]

    assert verifier._room_slots.resolve("signal:g1", "nora") == "signal:g1"
