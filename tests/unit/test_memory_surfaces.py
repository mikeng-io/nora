"""One memory surface per deployment (operator ruling 2026-10-16, DEC-316).

Honcho and the memory graph never sit on one tool surface. `[memory_index]
enabled` (the registry holding a `MemorySearch`) selects the graph's,
`memory_search`; off (the shipped default, and the rollback) it is the
Honcho surface exactly as before: `ask_memory`, `search_memory`,
`search_memory_workspace`, `who_said`. `about`, `remember` and
`load_transcript` are on both.

Pinned at every place tools are assembled, each through its own production
builder: a tag, a fire turn, the Cognition session, each Dreamer adaptor that
holds a memory verb, and the sub-agent worker. The sets are literals, so a
surface that gains or loses a memory verb is a decision somebody writes here.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.dreamer import (
    AdaptorGrant,
    HomeRoom,
    RegistryToolSource,
    cognition_grant,
    culture_grant,
)
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.subagent_tools import SubagentToolSource
from nora.agent.tools import GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS
from nora.app.cognition_wiring import read_surface, read_tools_for
from nora.domain.cognition_session import Room as AgendaRoom
from nora.language.character import CHARACTER_PERSON_ADAPTOR_NAME, CHARACTER_PERSON_TOOLS
from nora.language.culture import CULTURE_ADAPTOR_NAME, CULTURE_TOOLS
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.memory_index import FakeMemoryIndex
from tests.fakes import SinkSender
from tests.test_room_tools import GROUP, IDENTITY, MILES, NORA, TYLER, _NullStore

#: Every verb either surface could carry, so a set below is what one place
#: offers of them.
MEMORY_VERBS = HONCHO_MEMORY_TOOLS | GRAPH_MEMORY_TOOLS | {
    "about", "remember", "load_transcript",
}

#: What each assembly point offers of `MEMORY_VERBS`, per surface. The graph
#: column is the Honcho column with every Honcho memory verb taken out and
#: `memory_search` put in where that place had a Honcho search verb. The
#: worker holds no `memory_search`: it reads the unit's other rooms, and the
#: worker's reach is its one room (design/subagent §4c, `NOT_LOOKUPS`), as
#: `search_memory_workspace` was kept from it.
EXPECTED: dict[str, dict[bool, frozenset[str]]] = {
    "tag": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "remember", "load_transcript"}),
        True: frozenset({"memory_search", "about", "remember", "load_transcript"}),
    },
    "fire": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "remember", "load_transcript"}),
        True: frozenset({"memory_search", "about", "remember", "load_transcript"}),
    },
    "cognition": {
        False: frozenset({"ask_memory", "search_memory", "search_memory_workspace",
                          "about"}),
        True: frozenset({"memory_search", "about"}),
    },
    CULTURE_ADAPTOR_NAME: {
        False: frozenset({"ask_memory", "search_memory", "about"}),
        True: frozenset({"memory_search", "about"}),
    },
    CHARACTER_PERSON_ADAPTOR_NAME: {
        False: frozenset({"ask_memory", "search_memory", "about"}),
        True: frozenset({"memory_search", "about"}),
    },
    "subagent": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "load_transcript"}),
        True: frozenset({"about", "load_transcript"}),
    },
}


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _registry(graph: bool) -> ToolRegistry:
    """The registry as `build_dependencies` builds it: a `MemorySearch`
    exactly when `[memory_index] enabled`."""
    return ToolRegistry(
        memory_search=MemorySearch(FakeMemoryIndex([]), Metrics()) if graph else None,
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
        honcho=FakeMemoryStore(), metrics=Metrics(),
        scope_rooms={GROUP: here}, home_rooms={"u1": here},
        rosters={"signal": FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)})},
        self_ids=NORA,
    )


def _built(where: str, graph: bool, persons: Any) -> set[str]:
    """What `where` is handed, through that place's own builder."""
    registry = _registry(graph)
    if where in ("tag", "fire"):
        surface = registry.surface(
            store=_NullStore(), conversation_id=GROUP, conversation_type="group",
            persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=_room(persons), fire_turn=where == "fire",
        )
        return set(surface.admitted)
    if where == "cognition":
        source = _dream_source(registry, persons)
        # The flags as composition derives them from config.
        flags = ("memory_index.enabled",) if graph else ()
        wired = read_surface(read_only=source.read_only(), enabled_flags=flags).wired
        build = read_tools_for(source, names=wired, grant=cognition_grant())
        return _names(build("u1", [AgendaRoom("signal", GROUP)]))
    if where == CULTURE_ADAPTOR_NAME:
        return _names(_dream_source(registry, persons).build(
            key=GROUP, names=CULTURE_TOOLS, unit=None, adaptor=where,
            grant=culture_grant(),
        ))
    if where == CHARACTER_PERSON_ADAPTOR_NAME:
        return _names(_dream_source(registry, persons).build(
            key="miles_novak", names=CHARACTER_PERSON_TOOLS, unit="u1", adaptor=where,
            grant=AdaptorGrant(),
        ))
    assert where == "subagent"
    return _names(SubagentToolSource(
        registry=registry, store=_NullStore(), persons=persons,
        honcho=FakeMemoryStore(), metrics=Metrics(),
        rosters={"signal": FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)})},
        self_ids=NORA,
    ).build(
        job_id="j1", platform="signal", conversation_id=GROUP,
        conversation_type="group", requester_person_id="miles_novak",
    ))


@pytest.mark.parametrize("where", sorted(EXPECTED))
def test_the_honcho_surface_is_origin_masters_with_no_memory_search(
    persons: Any, where: str,
) -> None:
    """`[memory_index]` off: the memory verbs each place held on origin/master,
    and never the graph's."""
    offered = _built(where, False, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][False]
    assert not offered & GRAPH_MEMORY_TOOLS


@pytest.mark.parametrize("where", sorted(EXPECTED))
def test_the_graph_surface_offers_memory_search_and_no_honcho_memory_verb(
    persons: Any, where: str,
) -> None:
    """`[memory_index]` on: `memory_search`, and none of `ask_memory`,
    `search_memory`, `search_memory_workspace` or `who_said`."""
    offered = _built(where, True, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][True]
    assert not offered & HONCHO_MEMORY_TOOLS


def test_cognition_names_what_it_holds_on_each_surface() -> None:
    """The session's `wired` set is its guard's read-only set and its boot
    log, so it must name the surface the registry builds, not the catalogue."""
    read_only = frozenset(HONCHO_MEMORY_TOOLS | GRAPH_MEMORY_TOOLS | {"about"})
    off = read_surface(read_only=read_only, enabled_flags=())
    on = read_surface(read_only=read_only, enabled_flags=("memory_index.enabled",))
    assert "memory_search" in off.withheld and "memory_search" not in off.wired
    assert "memory_search" in on.wired
    assert not on.wired & HONCHO_MEMORY_TOOLS
    assert {"ask_memory", "search_memory", "search_memory_workspace"} <= on.withheld
