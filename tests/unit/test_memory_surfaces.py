"""One memory surface per deployment (DEC-316).

Honcho and the memory graph never sit on one tool surface. `[memory_index]
enabled` (the registry holding a `MemorySearch`) selects the graph's,
`memory_search`; off (the shipped default, and the rollback) it is the
Honcho surface: `ask_memory`, `search_memory`,
`search_memory_workspace`, `who_said`, `about` and `remember`. The graph
surface holds no Honcho tool at all: no `about`. `remember` is on both, a
Honcho conclusion off the graph and a Persona `told` line on it (DEC-317);
`load_transcript` reads the room's own rows and is on both.

Pinned at every place tools are assembled, each through its own production
builder: a tag, a fire turn, the Cognition session, each Dreamer adaptor that
holds a memory verb, and the sub-agent worker. The sets are literals, so a
surface that gains or loses a memory verb is a decision somebody writes here.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.dreamer import (
    HomeRoom,
    RegistryToolSource,
    cognition_grant,
    culture_grant,
)
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.subagent_tools import SubagentToolSource
from nora.agent.tools import GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS, build_tool_specs
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
#: offers of them. A literal, not built from `HONCHO_MEMORY_TOOLS`: a tool
#: dropped from that constant must still be seen here, or the graph test
#: would pass by no longer looking for it.
MEMORY_VERBS = frozenset({
    "ask_memory", "search_memory", "search_memory_workspace", "who_said",
    "about", "remember", "memory_search", "load_transcript",
})


def test_memory_verbs_cover_both_surfaces() -> None:
    assert HONCHO_MEMORY_TOOLS | GRAPH_MEMORY_TOOLS <= MEMORY_VERBS

#: What each assembly point offers of `MEMORY_VERBS`, per surface. The graph
#: column is the Honcho column with every Honcho tool taken out (`about`
#: included; `remember` stays, as the Persona write) and `memory_search` put
#: in where that place had a Honcho search verb. The
#: worker holds no `memory_search`: it reads the unit's other rooms, and the
#: worker's reach is its one room (`NOT_LOOKUPS`).
EXPECTED: dict[str, dict[bool, frozenset[str]]] = {
    "tag": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "remember", "load_transcript"}),
        True: frozenset({"memory_search", "remember", "load_transcript"}),
    },
    "fire": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "remember", "load_transcript"}),
        True: frozenset({"memory_search", "remember", "load_transcript"}),
    },
    "cognition": {
        False: frozenset({"ask_memory", "search_memory", "search_memory_workspace",
                          "about"}),
        True: frozenset({"memory_search"}),
    },
    CULTURE_ADAPTOR_NAME: {
        False: frozenset({"ask_memory", "search_memory", "about"}),
        True: frozenset({"memory_search"}),
    },
    CHARACTER_PERSON_ADAPTOR_NAME: {
        False: frozenset({"ask_memory", "search_memory", "about"}),
        True: frozenset({"memory_search"}),
    },
    "subagent": {
        False: frozenset({"ask_memory", "search_memory", "who_said", "about",
                          "load_transcript"}),
        True: frozenset({"load_transcript"}),
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
        # As composition derives it: the registry that builds the tools says
        # which memory surface it offers (one source, DEC-316).
        wired = read_surface(
            read_only=source.read_only(), enabled_flags=(),
            registry_withheld=registry.withheld_tools(),
        ).wired
        build = read_tools_for(source, names=wired, grant=cognition_grant())
        return _names(build("u1", [AgendaRoom("signal", GROUP)]))
    if where == CULTURE_ADAPTOR_NAME:
        return _names(_dream_source(registry, persons).build(
            key=GROUP, names=CULTURE_TOOLS, unit=None, adaptor=where,
            grant=culture_grant(),
        ))
    if where == CHARACTER_PERSON_ADAPTOR_NAME:
        # As production builds it (`DreamerRuntime`, composition's grants):
        # the key is the person's home room (`tool_room`), no unit supplied
        # (`key_kind` is `entity`), and the culture grant (a real room).
        return _names(_dream_source(registry, persons).build(
            key=GROUP, names=CHARACTER_PERSON_TOOLS, unit=None, adaptor=where,
            grant=culture_grant(),
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
    `search_memory`, `search_memory_workspace`, `who_said` or `about`: no
    Honcho tool at all (`remember` there is the Persona write, below)."""
    offered = _built(where, True, persons) & MEMORY_VERBS
    assert offered == EXPECTED[where][True]
    assert not offered & HONCHO_MEMORY_TOOLS
    assert "about" not in offered


@pytest.mark.parametrize(("graph", "closure"), [(False, "remember"), (True, "remember_told")])
def test_remember_is_the_persona_write_on_the_graph_and_honchos_off_it(
    persons: Any, graph: bool, closure: str,
) -> None:
    """One name, two writes: off the graph `remember` is Honcho's conclusion,
    on it the Persona `told` line, never the Honcho closure under the graph."""
    specs = build_tool_specs(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=_room(persons), question="", context_window=40,
        transit_source=None,
        memory_retrieval=MemorySearch(FakeMemoryIndex([]), Metrics()) if graph else None,
    )
    assert [s.call.__name__ for s in specs if s.name == "remember"] == [closure]


def test_cognition_takes_the_memory_surface_from_the_registry() -> None:
    """One source (DEC-316): Cognition's `wired` set is what the
    registry it builds through withholds, never a flag re-derived from
    config beside it. `read_surface` cannot be called without it."""
    read_only = frozenset(HONCHO_MEMORY_TOOLS | GRAPH_MEMORY_TOOLS | {"about"})
    off = read_surface(read_only=read_only, enabled_flags=(),
                       registry_withheld=_registry(False).withheld_tools())
    on = read_surface(read_only=read_only, enabled_flags=(),
                      registry_withheld=_registry(True).withheld_tools())
    assert "memory_search" in off.withheld and "memory_search" not in off.wired
    assert "memory_search" in on.wired
    assert not on.wired & HONCHO_MEMORY_TOOLS
    assert {"ask_memory", "search_memory", "search_memory_workspace", "about"} <= on.withheld
    assert "about" in off.wired
    with pytest.raises(TypeError):
        read_surface(read_only=read_only, enabled_flags=())  # type: ignore[call-arg]


@pytest.mark.parametrize("graph", [False, True])
def test_the_digest_lists_one_memory_surface(graph: bool) -> None:
    """The capability digest (the relevance gate's view of what she can
    answer) names the surface the registry offers, never the other one."""
    digest = _registry(graph).capability_digest()
    named = {line.split(" — ")[0][2:] for line in digest.splitlines()}
    if graph:
        assert "memory_search" in named and not named & HONCHO_MEMORY_TOOLS
    else:
        assert "memory_search" not in named and "search_memory" in named
