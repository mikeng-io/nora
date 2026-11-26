"""Memory reads under the graph switch (DEC-316).

Production wires the graph reads `memory_search` and `memory_read`
unconditionally; evaluation may omit them only as an explicit experimental
arm. The transcript-window attribution read `who_said` remains available
either way where granted. Both surfaces offer `load_transcript`
to read the room's rows.

Pinned at every place tools are assembled, each through its own production
builder: a tag, a fire turn, the Cognition session, each Dreamer adaptor that
holds a memory verb, and the sub-agent worker. The sets are literals, so a
surface that gains or loses a memory verb is a decision somebody writes here.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.catalog.groups import GRAPH_MEMORY_TOOLS
from nora.agent.dreamer import (
    HomeRoom,
    RegistryToolSource,
    cognition_grant,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.subagent_tools import SubagentToolSource
from nora.app.cognition_wiring import read_surface, read_tools_for
from nora.domain.cognition_session import Room as AgendaRoom
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource
from tests.fakes import SinkSender
from tests.test_room_tools import GROUP, IDENTITY, MILES, NORA, TYLER, _NullStore

#: Every memory verb either graph state could carry, so a set below is what
#: one place offers of them. A literal: a tool dropped from the catalogue
#: constants must still be seen here, or a test could pass by not looking for it.
MEMORY_VERBS = frozenset({
    "who_said", "remember", "memory_search", "memory_read", "load_transcript",
    "profile_dispute",
})


def test_memory_verbs_cover_the_graph_reads_and_who_said() -> None:
    assert {"who_said"} | GRAPH_MEMORY_TOOLS <= MEMORY_VERBS

#: What each assembly point offers of `MEMORY_VERBS`, per graph state. The
#: graph reads join `who_said` where permitted; they never replace it. The
#: worker holds no `memory_search`: it reads the unit's other rooms, and the
#: worker's reach is its one room (`NOT_LOOKUPS`).
EXPECTED: dict[str, dict[bool, frozenset[str]]] = {
    "tag": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"who_said", "memory_search", "memory_read", "profile_dispute",
                         "load_transcript"}),
    },
    "fire": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"who_said", "memory_search", "memory_read", "profile_dispute",
                         "load_transcript"}),
    },
    "cognition": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"who_said", "memory_search", "memory_read", "load_transcript"}),
    },
    "subagent": {
        False: frozenset({"who_said", "load_transcript"}),
        True: frozenset({"who_said", "load_transcript"}),
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
    """Without the retrieval service: the memory verbs each place offers."""
    offered = await _built(where, False, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][False]
    assert not offered & GRAPH_MEMORY_TOOLS


@pytest.mark.parametrize("where", sorted(EXPECTED))
async def test_the_graph_surface_offers_memory_search_and_keeps_who_said(
    persons: Any, where: str,
) -> None:
    """With the retrieval service: the graph reads join, never replace, `who_said`."""
    offered = await _built(where, True, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][True]
    assert "who_said" in offered


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
    read_only = frozenset({"who_said"} | GRAPH_MEMORY_TOOLS)
    off = read_surface(read_only=read_only, enabled_flags=(),
                       registry_withheld=_registry(False).withheld_tools())
    on = read_surface(read_only=read_only, enabled_flags=(),
                      registry_withheld=_registry(True).withheld_tools())
    assert "memory_search" in off.withheld and "memory_search" not in off.wired
    assert "memory_search" in on.wired
    assert "who_said" in on.wired
    assert "who_said" not in on.withheld
    assert "who_said" in off.wired
    with pytest.raises(TypeError):
        read_surface(read_only=read_only, enabled_flags=())  # type: ignore[call-arg]


@pytest.mark.parametrize("graph", [False, True])
def test_the_digest_lists_the_wired_memory_reads(graph: bool) -> None:
    """The capability digest (the relevance gate's view of what she can
    answer) names `who_said` either way, plus the graph reads only when wired."""
    digest = _registry(graph).capability_digest()
    named = {line.split(" — ")[0][2:] for line in digest.splitlines()}
    assert "who_said" in named
    if graph:
        assert "memory_search" in named
    else:
        assert "memory_search" not in named
