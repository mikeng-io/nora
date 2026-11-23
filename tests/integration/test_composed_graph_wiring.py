"""The composition root hands each Cognitive Graph stage the collaborator it calls."""

from nora.app.composition import build_dependencies
from nora.domain.lineage import LiveRoom
from nora.memory.domain.retrieval import ReadingRoom, RetrievalAnchors
from tests.builders import override_config
from tests.infra.test_retrieval_scope import BOUNDS
from tests.integration.test_memory_vectors import MODEL_ID
from tests.integration.test_memory_vectors import llama as llama
from tests.test_composition import cfg as cfg
from tests.test_composition import owned_dependencies as owned_dependencies


async def test_the_composed_verifier_resolves_an_origin_room_through_the_room_policy(cfg):
    deps = await build_dependencies(cfg)
    [verifier] = [run for stage, run in deps.graph_observe._executors
                  if stage == "intention_verify"]

    assert verifier._room_slots.resolve("signal:g1", "nora") == "signal:g1"


async def test_the_composed_prefetch_reads_an_empty_graph_as_nothing_found(cfg, llama):
    _, url = llama
    deps = await build_dependencies(override_config(cfg, {
        "memory.index.embed": True, "memory.index.embedder_url": url,
        "memory.index.embedder_model": MODEL_ID,
    }))
    called = deps.engagement
    room = ReadingRoom("signal", "g1", LiveRoom(frozenset({"miles"})))

    for assembler in (called._graph._assembler, called._debrief_wiring.assembler):
        result = await assembler._retrieval.retrieve(
            room, RetrievalAnchors(query="Generated question"), BOUNDS,
        )
        assert (result.status, result.cause) == ("nothing_found", None)
