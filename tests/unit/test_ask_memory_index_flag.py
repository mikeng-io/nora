"""`ask_memory` and `[memory_index] enabled` (design/unified-memory/60 S5,
DEC-316).

Off (no `memory_search`, the shipped default): today's Honcho path, the same
call and the same result. On: `ask_memory` is not offered; the memory graph's
`memory_search` is, and the index answers there.
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
        context_window=40, lineage=_LINEAGE, memory_retrieval=search,
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


async def test_flag_on_ask_memory_is_not_built(persons: Any) -> None:
    """DEC-316: with the index wired, the memory surface is the graph's, so
    `ask_memory` is not offered at all (its index branch went with it; the
    index answers through `memory_search`, tests/unit/test_memory_search_tool.py).
    The whole-surface pins are tests/unit/test_memory_surfaces.py."""
    tools = _build_with(persons, FakeMemoryStore(), _dense(_index(), Metrics()))
    assert "ask_memory" not in tools and "memory_search" in tools
