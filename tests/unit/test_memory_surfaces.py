"""One memory surface per deployment (DEC-316).

`[memory.index] read` (the registry holding a `MemorySearch`) selects the
graph's surface, `memory_search`; off (the shipped default) it is `who_said`
and no `memory_search`. Both offer `load_transcript` to read the room's rows.

Pinned at every place tools are assembled, each through its own production
builder: a tag, a fire turn, the Cognition session, each Dreamer adaptor that
holds a memory verb, and the sub-agent worker. The sets are literals, so a
surface that gains or loses a memory verb is a decision somebody writes here.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.catalog.groups import GRAPH_MEMORY_TOOLS, OFF_GRAPH_ONLY_TOOLS
from nora.agent.dreamer import (
    HomeRoom,
    RegistryToolSource,
    cognition_grant,
)
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.subagent_tools import SubagentToolSource
from nora.app.cognition_wiring import read_surface, read_tools_for
from nora.domain.cognition_session import Room as AgendaRoom
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource
from nora.testing.memory_index import FakeMemoryIndex
from tests.fakes import SinkSender
from tests.test_room_tools import GROUP, IDENTITY, MILES, NORA, TYLER, _NullStore

#: Every verb either surface could carry, so a set below is what one place
#: offers of them. A literal, not built from `OFF_GRAPH_ONLY_TOOLS`: a tool
#: dropped from that constant must still be seen here, or the graph test
#: would pass by no longer looking for it.
MEMORY_VERBS = frozenset({
    "who_said", "remember", "memory_search", "memory_read", "load_transcript",
    "profile_dispute",
})


def test_memory_verbs_cover_both_surfaces() -> None:
    assert OFF_GRAPH_ONLY_TOOLS | GRAPH_MEMORY_TOOLS <= MEMORY_VERBS

#: What each assembly point offers of `MEMORY_VERBS`, per surface. The graph
#: column swaps `who_said` for `memory_search` where permitted. The
#: worker holds no `memory_search`: it reads the unit's other rooms, and the
#: worker's reach is its one room (`NOT_LOOKUPS`).
EXPECTED: dict[str, dict[bool, frozenset[str]]] = {
    "tag": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"memory_search", "memory_read", "profile_dispute",
                         "load_transcript"}),
    },
    "fire": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"memory_search", "memory_read", "profile_dispute",
                         "load_transcript"}),
    },
    "cognition": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"memory_search", "memory_read", "load_transcript"}),
    },
    "subagent": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"load_transcript"}),
    },
}


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _registry(graph: bool) -> ToolRegistry:
    """The registry offers the Cognitive Graph reads when their service is wired."""
    return ToolRegistry(
        memory_search=MemorySearch(FakeMemoryIndex([]), Metrics()) if graph else None,
        graph_retrieval=object() if graph else None,
        memory_intake=object(),
    )


def _room(persons: Any) -> Room:
    return Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=None, self_ids=NORA,
    )


def _names(tools: Any) -> set[str]:
    return {getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in tools}


def _dream_source(registry: ToolRegistry, persons: Any) -> RegistryToolSource:
    here = HomeRoom(unit="u1", platform="signal", conversation_id=GROUP)
    return RegistryToolSource(
        registry=registry, store=_NullStore(), persons=persons,
        metrics=Metrics(),
        scope_rooms={GROUP: here},
        rosters={"signal": FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)})},
        self_ids=NORA,
    )


async def _built(where: str, graph: bool, persons: Any) -> set[str]:
    """What `where` is handed, through that place's own builder."""
    registry = _registry(graph)
    if where in ("tag", "fire"):
        surface = registry.surface(
            store=_NullStore(), conversation_id=GROUP, conversation_type="group",
            persons=persons, deliverer=SinkSender(),
            metrics=Metrics(), room=_room(persons), fire_turn=where == "fire",
        )
        return set(surface.admitted)
    if where == "cognition":
        source = _dream_source(registry, persons)
        # As composition derives it: the registry that builds the tools says
        # which memory surface it offers (one source, DEC-316).
        wired = read_surface(
            read_only=source.read_only(), enabled_flags=(),
            registry_withheld=registry.withheld_tools(),
        ).wired
        build = read_tools_for(source, names=wired, grant=cognition_grant())
        return _names(build("u1", [AgendaRoom("signal", GROUP)]))
    assert where == "subagent"
    return _names(await SubagentToolSource(
        registry=registry, store=_NullStore(), persons=persons,
        metrics=Metrics(),
        rosters={"signal": FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)})},
        self_ids=NORA,
    ).build(
        job_id="j1", platform="signal", conversation_id=GROUP,
        conversation_type="group", requester_person_id="miles_novak",
    ))


@pytest.mark.parametrize("where", sorted(EXPECTED))
async def test_the_off_graph_surface_has_no_memory_search(
    persons: Any, where: str,
) -> None:
    """`[memory.index]` off: the memory verbs each place offers, never the graph's."""
    offered = await _built(where, False, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][False]
    assert not offered & GRAPH_MEMORY_TOOLS


@pytest.mark.parametrize("where", sorted(EXPECTED))
async def test_the_graph_surface_offers_memory_search_and_no_off_graph_tool(
    persons: Any, where: str,
) -> None:
    """`[memory.index]` on: `memory_search`, and no `who_said`."""
    offered = await _built(where, True, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][True]
    assert not offered & OFF_GRAPH_ONLY_TOOLS


@pytest.mark.parametrize("graph", [False, True])
def test_the_retired_write_is_absent_from_general_specs(persons: Any, graph: bool) -> None:
    registry = _registry(graph)
    specs = registry.specs(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, deliverer=SinkSender(), metrics=Metrics(), room=_room(persons),
    )
    names = {spec.name for spec in specs}
    assert "now" in names
    assert "remember" not in names


def test_cognition_takes_the_memory_surface_from_the_registry() -> None:
    """One source (DEC-316): Cognition's `wired` set is what the
    registry it builds through withholds, never a flag re-derived from
    config beside it. `read_surface` cannot be called without it."""
    read_only = frozenset(OFF_GRAPH_ONLY_TOOLS | GRAPH_MEMORY_TOOLS)
    off = read_surface(read_only=read_only, enabled_flags=(),
                       registry_withheld=_registry(False).withheld_tools())
    on = read_surface(read_only=read_only, enabled_flags=(),
                      registry_withheld=_registry(True).withheld_tools())
    assert "memory_search" in off.withheld and "memory_search" not in off.wired
    assert "memory_search" in on.wired
    assert not on.wired & OFF_GRAPH_ONLY_TOOLS
    assert on.withheld >= OFF_GRAPH_ONLY_TOOLS
    assert off.wired >= OFF_GRAPH_ONLY_TOOLS
    with pytest.raises(TypeError):
        read_surface(read_only=read_only, enabled_flags=())  # type: ignore[call-arg]


@pytest.mark.parametrize("graph", [False, True])
def test_the_digest_lists_one_memory_surface(graph: bool) -> None:
    """The capability digest (the relevance gate's view of what she can
    answer) names the surface the registry offers, never the other one."""
    digest = _registry(graph).capability_digest()
    named = {line.split(" — ")[0][2:] for line in digest.splitlines()}
    if graph:
        assert "memory_search" in named and not named & OFF_GRAPH_ONLY_TOOLS
    else:
        assert "memory_search" not in named and "who_said" in named
