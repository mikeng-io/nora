"""Registered memory graph tools: the existing index seeds the new graph.

The read surface follows the index-read switch. Scope and narrowing apply
inside index reads and graph traversal; every result carries its state and note.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
from nora.domain.memory_index import IndexedNode
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.memory_index import FakeMemoryIndex, FakeQueryEmbedder
from tests.fakes import SinkSender
from tests.test_room_tools import (
    _UNREACHABLE_TRANSIT,
    GROUP,
    IDENTITY,
    MILES,
    NORA,
    TYLER,
    _call,
    _NullStore,
)

_ROOM = f"signal:{GROUP}"
_FRIENDS = RoomFacts(
    "signal", frozenset({GROUP}), "u1", frozenset({"miles_novak", "tyler_cortez"}),
    label="Friends",
)
_LINEAGE = LineagePolicy(rooms=(_FRIENDS,), _by_id={("signal", GROUP): _FRIENDS})
#: 22:13 UTC on 14 Nov 2023, and a day of milliseconds.
_T = 1_700_000_000_000
_DAY = 86_400_000


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _tool(persons: Any, honcho: FakeMemoryStore, search: MemorySearch | None) -> Any:
    return _build_with(persons, honcho, search)["memory_search"]


def _build_with(
    persons: Any, honcho: FakeMemoryStore, search: MemorySearch | None,
    metrics: Metrics | None = None, **extra: Any,
) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(
            groups={GROUP: tuple(extra.pop("members", (MILES, TYLER, NORA)))}),
        persons=persons, tagger=None, self_ids=NORA,
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=honcho, deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=room, transit_source=_UNREACHABLE_TRANSIT, question="佢鍾唔鍾意踢波",
        context_window=40, lineage=extra.pop("lineage", _LINEAGE),
        memory_retrieval=search, **extra,
    )
    return {t.tool_name: t for t in built}


def _dense(index: FakeMemoryIndex, metrics: Metrics) -> MemorySearch:
    """The search as `[memory.index] embed` builds it: dense seeds
    over the fake's vectors of model `m`, the question embedded by a fake."""
    return MemorySearch(index, metrics, embedder=FakeQueryEmbedder(), model="m")


def _index(**kw: Any) -> FakeMemoryIndex:
    return FakeMemoryIndex(
        [
            IndexedNode("message", "41", "five-a-side football on sunday", _T,
                        origin_room=_ROOM),
            IndexedNode("person", "miles_novak"),
        ],
        [("message", "41", "authored_by", "person", "miles_novak")],
        embed_model="m", **kw,
    )


# ---- arguments ---------------------------------------------------------------


@pytest.mark.parametrize("dates", [
    {"since": "last week"}, {"until": "2026-13-01"}, {"since": "yesterday-ish"},
])
async def test_a_bad_date_is_unavailable_and_searches_nothing(
    persons: Any, dates: dict[str, str],
) -> None:
    """Never silently ignored: a narrowing dropped would answer a question
    nobody asked."""
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["x"]})
    index = _index()
    result = await _call(
        _tool(persons, honcho, _dense(index, Metrics())), query="x", person="Miles", **dates)
    assert result["status"] == "unavailable" and "YYYY-MM-DD" in result["note"]
    assert honcho.chats == [] and honcho.searches == [] and index.calls == []


async def test_a_span_that_ends_before_it_starts_is_unavailable(persons: Any) -> None:
    index = _index()
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(index, Metrics())),
        query="x", since="2026-10-10", until="2026-10-01",
    )
    assert result["status"] == "unavailable" and result["note"]
    assert index.calls == []


async def test_an_ambiguous_person_is_asked_about_not_searched(tmp_path: Any) -> None:
    path = tmp_path / "twins.toml"
    path.write_text(
        '[persons.a]\nname = "Ah Ming"\naliases = ["阿星"]\n'
        '[[persons.a.signal]]\nphone = "+85211111111"\n'
        '[persons.b]\nname = "Ming Chai"\naliases = ["阿星"]\n'
        '[[persons.b.signal]]\nphone = "+85222222222"\n'
    )
    index = _index()
    tools = _build_with(
        load_persons(path), FakeMemoryStore(), _dense(index, Metrics()),
        members=(frozenset({"+85211111111"}), frozenset({"+85222222222"})),
    )
    result = await _call(tools["memory_search"], query="x", person="阿星")
    assert result["status"] == "ambiguous" and result["note"]
    assert sorted(result["could_be"]) == ["Ah Ming", "Ming Chai"]
    assert index.calls == []


# ---- subgraph answers --------------------------------------------------------


def _graph_search(index, metrics):
    from datetime import UTC, datetime

    from nora.domain.memory_graph import Entity, Evidence, Relation, pointer_entity
    from nora.testing.memory_graph import FakeMemoryGraph

    graph = FakeMemoryGraph()
    person = pointer_entity("person", "u1", "miles_novak", "Miles Novak")
    event = Entity(
        "generated-event",
        "event",
        "u1",
        "Sunday football",
        aliases=("football",),
        attributes={"start": "2023-11-14"},
        created_at=datetime(2023, 11, 14, tzinfo=UTC),
    )
    graph.entities = {e.id: e for e in (person, event)}
    node = index.nodes[("message", "41")]
    graph.indexed[node.key] = node
    graph.relations = {
        "generated-authored": Relation(
            "generated-authored",
            node.key,
            "authored_by",
            person.key,
            "record",
            "u1",
            asserted_at=datetime(2023, 11, 14, tzinfo=UTC),
        ),
        "generated-plan": Relation(
            "generated-plan",
            person.key,
            "plans",
            event.key,
            "notebook",
            "u1",
            ("miles_novak",),
            asserted_at=datetime(2023, 11, 14, tzinfo=UTC),
        ),
    }
    graph.evidence = [Evidence(node.key, relation_id="generated-plan", span="football")]
    search = MemorySearch(index, metrics, graph=graph, embedder=FakeQueryEmbedder(), model="m")
    return search, graph


async def test_registered_search_seeds_index_and_walks_new_graph(persons):
    metrics = Metrics()
    search, graph = _graph_search(_index(), metrics)
    honcho = FakeMemoryStore()
    result = await _call(_tool(persons, honcho, search), query="football", depth=2)
    assert result["status"] in {"ok", "partial"}
    assert "Sunday football" in result["preview"]
    assert "judgement" in result["preview"] and "1 message" in result["preview"]
    assert "valid=" in result["preview"] and "state=live" in result["preview"]
    assert not honcho.chats and not honcho.searches
    graph.available = False
    result = await _call(_tool(persons, honcho, search), query="football")
    assert result["status"] == "unavailable" and result["note"]


async def test_registered_read_zero_returns_full_fields_and_evidence(persons):
    search, _graph = _graph_search(_index(), Metrics())
    tools = _build_with(persons, FakeMemoryStore(), search)
    result = await _call(tools["memory_read"], kind="event", id="generated-event", depth=0)
    assert result["status"] == "ok"
    assert result["nodes"][0]["entity"]["attributes"] == {"start": "2023-11-14"}
    evidence = result["evidence"][0]
    assert evidence["evidence"][0]["span"] == "football"
    assert evidence["sources"][0]["text"] == "five-a-side football on sunday"
    tools = _build_with(persons, FakeMemoryStore(), search)
    missing = await _call(tools["memory_read"], kind="event", id="not-present")
    assert missing["status"] == "nothing_found" and missing["note"]
    invalid = await _call(tools["memory_read"], kind="event", id="generated-event", depth=5)
    assert invalid["status"] == "unavailable" and invalid["note"]


async def test_unwired_graph_is_unavailable(persons):
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(_index(), Metrics())), query="football"
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_shadow_registry_leaves_live_surface_unchanged():
    from nora.agent.registry import ToolRegistry

    registry = ToolRegistry()
    search, _ = _graph_search(_index(), Metrics())
    shadow = registry.with_memory_search(search)
    assert "memory_read" in registry.withheld_tools()
    assert "memory_read" not in shadow.withheld_tools()
    assert "about" in shadow.withheld_tools()


async def test_on_an_out_of_window_node_is_never_returned_and_takes_no_slot(persons):
    search, _ = _graph_search(_index(), Metrics())
    result = await _call(_tool(persons, FakeMemoryStore(), search), query='football',
                         since='2026-01-01')
    assert result['status'] == 'nothing_found'
    assert 'message:41' not in result['preview']
    # Same source without the window is a positive control.
    found = await _call(_tool(persons, FakeMemoryStore(), search), query='football')
    assert 'message:41' in found['preview']


async def test_on_a_person_the_room_does_not_list_is_unavailable_not_nothing(persons):
    from dataclasses import replace
    restricted = replace(_FRIENDS, persons=frozenset({'tyler_cortez'}))
    policy = LineagePolicy(rooms=(restricted,), _by_id={('signal', GROUP): restricted})
    search, _ = _graph_search(_index(), Metrics())
    result = await _call(_build_with(persons, FakeMemoryStore(), search,
        lineage=policy)['memory_search'], query='football', person='Miles')
    assert result['status'] == 'unavailable' and result['note']


async def test_a_culture_pass_never_reads_another_rooms_line(persons):
    from dataclasses import replace

    from nora.agent.tools import CULTURE_ADAPTOR_NAME
    other = RoomFacts('signal', frozenset({'generated-other'}), 'u1', _FRIENDS.persons)
    lineage = LineagePolicy(rooms=(_FRIENDS, other),
        _by_id={('signal', GROUP): _FRIENDS, ('signal', 'generated-other'): other})
    index = _index()
    extra = IndexedNode('message', '42', 'football in the other room', _T,
                        origin_room='signal:generated-other')
    index.nodes[extra.key] = extra
    search, graph = _graph_search(index, Metrics())
    graph.indexed[extra.key] = extra
    first = graph.relations['generated-authored']
    graph.relations['generated-other-authored'] = replace(first,
        id='generated-other-authored', src=extra.key)
    tools = _build_with(persons, FakeMemoryStore(), search, lineage=lineage,
                        dream_adaptor=CULTURE_ADAPTOR_NAME)
    result = await _call(tools['memory_search'], query='football')
    assert 'message:41' in result['preview'] and 'message:42' not in result['preview']
    tools = _build_with(persons, FakeMemoryStore(), search, lineage=lineage,
                        dream_adaptor=CULTURE_ADAPTOR_NAME)
    own = await _call(tools['memory_read'], kind='message', id='41', depth=0)
    assert own['status'] == 'ok' and own['nodes']
    tools = _build_with(persons, FakeMemoryStore(), search, lineage=lineage,
                        dream_adaptor=CULTURE_ADAPTOR_NAME)
    other = await _call(tools['memory_read'], kind='message', id='42', depth=0)
    assert other['status'] == 'nothing_found' and not other['nodes']


@pytest.mark.parametrize('name,arguments', [
    ('memory_search', {'query': 'football'}),
    ('memory_read', {'kind': 'event', 'id': 'generated-event', 'depth': 0}),
])
async def test_graph_tool_results_preserve_untrusted_memory_framing(persons, name, arguments):
    search, _ = _graph_search(_index(), Metrics())
    tools = _build_with(persons, FakeMemoryStore(), search)
    result = await _call(tools[name], **arguments)
    assert result['status'] in {'ok', 'partial'} and result['preview']
    assert result.get('untrusted') is True
    assert 'never an instruction' in result['framing']


async def test_subgraph_preserves_enabled_identifier_screen(persons):
    from dataclasses import replace
    phone = next(value for value in MILES if value.startswith('+'))
    index = _index()
    node = index.nodes[('message', '41')]
    index.nodes[node.key] = replace(node, text=f'football: call {phone}')
    search, _ = _graph_search(index, Metrics())
    result = await _call(_tool(persons, FakeMemoryStore(), search), query='football')
    assert result['status'] == 'unavailable' and phone not in str(result)
    off = _build_with(persons, FakeMemoryStore(), search, content_checks=False)
    allowed = await _call(off['memory_search'], query='football')
    assert allowed['status'] in {'ok', 'partial'} and phone in allowed['preview']


async def test_matching_index_without_graph_projection_is_unavailable(persons):
    from nora.testing.memory_graph import FakeMemoryGraph
    search = MemorySearch(_index(), Metrics(), graph=FakeMemoryGraph(),
                          embedder=FakeQueryEmbedder(), model='m')
    result = await _call(_tool(persons, FakeMemoryStore(), search), query='football')
    assert result['status'] == 'unavailable' and result['note']


async def test_one_turn_reuses_its_walk_and_does_not_guess_outside_the_snapshot(
    persons, monkeypatch,
):
    search, graph = _graph_search(_index(), Metrics())
    original = graph.walk
    calls = []

    async def counted(seeds, query):
        calls.append((seeds, query))
        return await original(seeds, query)

    monkeypatch.setattr(graph, 'walk', counted)
    tools = _build_with(persons, FakeMemoryStore(), search)
    found = await _call(tools['memory_search'], query='football')
    assert found['status'] == 'ok' and 'Sunday football' in found['preview']
    detail = await _call(tools['memory_read'], kind='event', id='generated-event', depth=0)
    assert detail['status'] == 'partial'
    assert detail['nodes'][0]['entity']['name'] == 'Sunday football'
    assert detail['evidence'][0]['sources'][0]['text'] == 'five-a-side football on sunday'
    assert 'more="available"' in detail['preview']
    outside = await _call(tools['memory_read'], kind='event', id='not-covered')
    assert outside['status'] == 'unavailable' and outside['note']
    assert len(calls) == 1


async def test_parallel_tools_reserve_one_database_walk(persons, monkeypatch):
    import asyncio

    search, graph = _graph_search(_index(), Metrics())
    original = graph.walk
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def paused(seeds, query):
        calls.append((seeds, query))
        entered.set()
        await release.wait()
        return await original(seeds, query)

    monkeypatch.setattr(graph, 'walk', paused)
    tools = _build_with(persons, FakeMemoryStore(), search)
    task = asyncio.create_task(_call(tools['memory_search'], query='football'))
    await entered.wait()
    try:
        concurrent = await _call(tools['memory_read'], kind='event', id='generated-event')
        assert concurrent['status'] == 'unavailable' and concurrent['note']
    finally:
        release.set()
    completed = await task
    assert completed['status'] == 'ok' and 'Sunday football' in completed['preview']
    assert len(calls) == 1
