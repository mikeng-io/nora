"""`ask_memory` behind `[memory_index] enabled` (design/unified-memory/60 S5).

Off (no `memory_search`, the shipped default): today's Honcho path, the same
call and the same result. On: the index answers and Honcho is not called;
every status carries a note, and nothing in the result is an id.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
from nora.domain.memory_index import IndexedNode, IndexState
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
    _build,
    _call,
    _NullStore,
)

_ROOM = f"signal:{GROUP}"
_FRIENDS = RoomFacts(
    "signal", frozenset({GROUP}), "u1", frozenset({"miles_novak", "tyler_cortez"}),
    label="Friends",
)
_LINEAGE = LineagePolicy(rooms=(_FRIENDS,), _by_id={("signal", GROUP): _FRIENDS})


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _tools(persons: Any, honcho: FakeMemoryStore, search: MemorySearch | None) -> Any:
    tools = _build_with(persons, honcho, search)
    return tools["ask_memory"]


def _build_with(
    persons: Any, honcho: FakeMemoryStore, search: MemorySearch | None,
    metrics: Metrics | None = None,
) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=None, self_ids=NORA,
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=honcho, deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=room, transit_source=_UNREACHABLE_TRANSIT, question="佢鍾唔鍾意踢波",
        context_window=40, lineage=_LINEAGE, memory_search=search,
    )
    return {t.tool_name: t for t in built}


def _dense(index: FakeMemoryIndex, metrics: Metrics) -> MemorySearch:
    """The search as `[memory_index] embed` builds it (65 V2): dense seeds
    over the fake's vectors of model `m`, the question embedded by a fake."""
    return MemorySearch(index, metrics, embedder=FakeQueryEmbedder(), model="m")


def _index(**kw: Any) -> FakeMemoryIndex:
    return FakeMemoryIndex(
        [
            IndexedNode("message", "41", "five-a-side football on sunday", 1_700_000_000_000,
                        origin_room=_ROOM),
            IndexedNode("person", "miles_novak"),
        ],
        [("message", "41", "authored_by", "person", "miles_novak")],
        embed_model="m", **kw,
    )


async def test_flag_off_calls_honcho_and_answers_as_before(persons: Any) -> None:
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    before = await _call(_build(persons=persons, honcho=honcho,
                                question="佢鍾唔鍾意踢波")["ask_memory"], person="Miles")
    honcho_off = FakeMemoryStore(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    after = await _call(_tools(persons, honcho_off, None), person="Miles")
    assert honcho_off.chats == [("nora", "miles_novak", "佢鍾唔鍾意踢波", "low")]
    assert after == before


async def test_flag_on_answers_from_the_index_and_never_calls_honcho(persons: Any) -> None:
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    index = _index()
    metrics = Metrics()
    result = await _call(
        _tools(persons, honcho, _dense(index, metrics)),
        person="Miles", query="football sunday",
    )
    assert honcho.chats == [] and honcho.queries == []
    assert result["status"] == "ok" and result["note"]
    [item] = result["answer"]
    assert item["kind"] == "message" and item["room"] == "Friends"
    assert item["people"] == ["Miles Novak"]
    assert item["path"] == ["message (matched)"]
    assert "41" not in str(result) and "miles_novak" not in str(result)
    assert metrics.get("nora_memory_search_total", status="ok") == 1


@pytest.mark.parametrize(("index", "status"), [
    (FakeMemoryIndex(state=IndexState("complete", 0)), "nothing_found"),
    (FakeMemoryIndex(state=IndexState("building", 0)), "partial"),
    (FakeMemoryIndex(fail=RuntimeError("db down")), "unavailable"),
])
async def test_flag_on_every_status_carries_a_note(
    persons: Any, index: FakeMemoryIndex, status: str,
) -> None:
    honcho = FakeMemoryStore()
    result = await _call(
        _tools(persons, honcho, _dense(index, Metrics())), person="Miles",
        query="anything at all",
    )
    assert result["status"] == status
    assert result["note"]
    assert honcho.chats == []


async def test_flag_on_withholds_an_item_carrying_an_identifier(persons: Any) -> None:
    index = FakeMemoryIndex([IndexedNode(
        "message", "42", "football: call +85298765432", 1_700_000_000_000, origin_room=_ROOM,
    )], embed_model="m")
    result = await _call(
        _tools(persons, FakeMemoryStore(), _dense(index, Metrics())),
        person="Miles", query="football",
    )
    assert result["status"] == "nothing_found" and result["withheld"] == 1
    assert "98765432" not in str(result)


async def test_flag_on_a_render_room_that_raises_is_unavailable_with_a_note(
    persons: Any, monkeypatch: Any,
) -> None:
    def boom(self: Any, **_: Any) -> Any:
        raise RuntimeError("policy")

    monkeypatch.setattr(LineagePolicy, "render_room", boom)
    metrics = Metrics()
    result = await _call(
        _tools(persons, FakeMemoryStore(), _dense(_index(), metrics)),
        person="Miles", query="football",
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_flag_on_a_packet_that_raises_is_unavailable_with_a_note(
    persons: Any, monkeypatch: Any,
) -> None:
    import nora.agent.tools as tools_module

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("render")

    monkeypatch.setattr(tools_module, "render_packet", boom)
    result = await _call(
        _tools(persons, FakeMemoryStore(), _dense(_index(), Metrics())),
        person="Miles", query="football",
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_flag_on_partial_with_no_items_is_recorded_as_partial(persons: Any) -> None:
    metrics = Metrics()
    index = FakeMemoryIndex(state=IndexState("building", 0))
    tool = _build_with(persons, FakeMemoryStore(), _dense(index, Metrics()), metrics)
    result = await _call(tool["ask_memory"], person="Miles", query="football")
    assert result["status"] == "partial"
    assert metrics.get("nora_room_lookups_total", tool="ask_memory", status="partial") == 1
    assert metrics.get(
        "nora_room_lookups_total", tool="ask_memory", status="nothing_found") == 0


async def test_flag_on_a_listed_persons_name_in_the_question_is_pinned(persons: Any) -> None:
    """65 V2: the names the room's roster resolves reach the search, and a
    node holding one literally ranks first."""
    index = FakeMemoryIndex(
        [
            IndexedNode("message", "43", "Tyler kept the receipts", 1_600_000_000_000,
                        origin_room=_ROOM),
            IndexedNode("message", "44", "who kept the receipts", 1_700_000_000_000,
                        origin_room=_ROOM),
        ],
        embed_model="m",
    )
    result = await _call(
        _tools(persons, FakeMemoryStore(), _dense(index, Metrics())),
        person="Miles", query="who kept the receipts, Tyler?",
    )
    assert result["status"] == "ok"
    assert [i["path"] for i in result["answer"]] == [["message (exact)"], ["message (matched)"]]
