"""`memory_search`, the one memory verb (operator ruling 2026-10-16), in both
states of `[memory_index] enabled` (design/unified-memory/60 S5, 65 V2).

Off (no `MemorySearch`, the shipped default and the rollback): Honcho, as the
two verbs it replaced did. With a person, `chat` (what `ask_memory` did);
without one, in a Dreamer pass the unit's message search (what
`search_memory_workspace` did, Dreamer-only), and in a room turn that room's
own session search (what `search_memory` does), never another room's. A
date or kind it cannot honour is said in the note.

On: the index answers and Honcho is not called. `person`, `since`, `until`
and `kinds` narrow the search INSIDE the index's reads, so a node outside
them is never returned and never takes a slot; every status carries a note,
and nothing in the result is an id.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import SEED_LIMIT, MemorySearch
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
from nora.domain.memory_index import IndexedNode, IndexState
from nora.domain.ports import MessageHits
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
#: 2023-11-14 22:13 UTC, and a day of milliseconds.
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
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=None, self_ids=NORA,
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=honcho, deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=room, transit_source=_UNREACHABLE_TRANSIT, question="佢鍾唔鍾意踢波",
        context_window=40, lineage=_LINEAGE, memory_retrieval=search, **extra,
    )
    return {t.tool_name: t for t in built}


def _dense(index: FakeMemoryIndex, metrics: Metrics) -> MemorySearch:
    """The search as `[memory_index] embed` builds it (65 V2): dense seeds
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


# ---- the index off: Honcho ---------------------------------------------------


async def test_off_with_a_person_is_what_ask_memory_did(persons: Any) -> None:
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    result = await _call(_tool(persons, honcho, None), query="football", person="Miles")
    assert honcho.chats == [("nora", "miles_novak", "football", "low")]
    assert honcho.searches == []
    assert result["status"] == "ok" and result["answer"] == "plays five-a-side on Sundays"


class _TwoRooms(FakeMemoryStore):
    """Honcho as it routes: the room's session search returns only that
    room's messages; the unit's workspace search returns every room of the
    unit, here room A's line and room B's (spoken by a member of THIS room,
    so the roster filter alone would not stop it)."""

    ROOM_A = ("miles_novak", "booked the pitch for sunday")
    ROOM_B = ("miles_novak", "the pitch thing I only said in the other room")

    async def search(self, *, query: str, conversation_id: str | None = None,
                     limit: int = 8, unit: str | None = None) -> MessageHits:
        self.routes.append(("search", conversation_id, unit))
        self.searches.append(query)
        if conversation_id == GROUP:
            return MessageHits(reachable=True, hits=(self.ROOM_A,))
        if conversation_id is None and unit is not None:
            return MessageHits(reachable=True, hits=(self.ROOM_A, self.ROOM_B))
        return MessageHits(reachable=False)


async def test_off_a_room_turn_without_a_person_stays_in_this_channel(
    persons: Any,
) -> None:
    """Parity with origin/master: a room turn never had the unit search
    (`search_memory_workspace` was Dreamer-only, #350), so `memory_search`
    in a room searches this room's own session, as `search_memory` does,
    and another room's words never come back, even a member's."""
    honcho = _TwoRooms()
    tool = _build_with(persons, honcho, None, unit_for=lambda *_: "u1")["memory_search"]
    result = await _call(tool, query="pitch")
    assert [r for r in honcho.routes if r[0] == "search"] == [("search", GROUP, None)]
    assert result["status"] == "ok"
    assert result["results"] == [{"who": "Miles Novak", "said": "booked the pitch for sunday"}]
    assert "other room" not in str(result)


async def test_off_a_dreamer_pass_searches_the_unit(persons: Any) -> None:
    """The unit search is the Dreamer's, as `search_memory_workspace` was."""
    honcho = _TwoRooms()
    tool = _build_with(
        persons, honcho, None, unit_for=lambda *_: "u1", dream_adaptor="proactive",
    )["memory_search"]
    result = await _call(tool, query="pitch")
    assert [r for r in honcho.routes if r[0] == "search"] == [("search", None, "u1")]
    assert len(result["results"]) == 2


async def test_off_a_dreamer_pass_with_no_unit_is_refused_not_widened(
    persons: Any,
) -> None:
    """No unit to name is `unavailable`, never an unrouted search of a
    default workspace."""
    honcho = _TwoRooms()
    tool = _build_with(persons, honcho, None, dream_adaptor="proactive")["memory_search"]
    result = await _call(tool, query="pitch")
    assert result["status"] == "unavailable" and result["note"]
    assert honcho.searches == []


async def test_off_in_a_dreamer_pass_searches_with_the_whole_address_book(
    persons: Any,
) -> None:
    """A Dreamer pass keeps `search_memory_workspace`'s roster: everyone in
    identity.toml but Nora, because a unit-keyed pass has no room view."""
    honcho = FakeMemoryStore()
    honcho.messages = [("elsewhere_liu", "said in another room of the unit")]
    tool = _build_with(
        persons, honcho, None, unit_for=lambda *_: "u1", dream_adaptor="proactive",
    )["memory_search"]
    result = await _call(tool, query="room")
    assert result["status"] == "ok"
    assert result["results"] == [
        {"who": "Elsewhere Liu", "said": "said in another room of the unit"}]


@pytest.mark.parametrize("narrowing", [
    {"since": "2026-10-01"}, {"until": "2026-10-01"}, {"kinds": ["media"]},
])
async def test_off_a_narrowing_honcho_cannot_honour_is_said_in_the_note(
    persons: Any, narrowing: dict[str, Any],
) -> None:
    honcho = FakeMemoryStore()
    honcho.messages = [("miles_novak", "booked the pitch")]
    result = await _call(_tool(persons, honcho, None), query="pitch", **narrowing)
    assert result["status"] == "ok"
    assert "could not narrow this by date or kind" in result["note"]


async def test_off_without_a_narrowing_says_nothing_about_one(persons: Any) -> None:
    honcho = FakeMemoryStore()
    honcho.messages = [("miles_novak", "booked the pitch")]
    result = await _call(_tool(persons, honcho, None), query="pitch")
    assert "could not narrow" not in str(result)


# ---- arguments ---------------------------------------------------------------


@pytest.mark.parametrize("dates", [
    {"since": "last week"}, {"until": "2026-13-01"}, {"since": "yesterday-ish"},
])
@pytest.mark.parametrize("enabled", [False, True])
async def test_a_bad_date_is_unavailable_and_searches_nothing(
    persons: Any, dates: dict[str, str], enabled: bool,
) -> None:
    """Never silently ignored: a narrowing dropped would answer a question
    nobody asked (operator ruling 2026-10-16)."""
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["x"]})
    index = _index()
    search = _dense(index, Metrics()) if enabled else None
    result = await _call(_tool(persons, honcho, search), query="x", person="Miles", **dates)
    assert result["status"] == "unavailable" and "YYYY-MM-DD" in result["note"]
    assert honcho.chats == [] and honcho.searches == [] and index.calls == []


async def test_a_span_that_ends_before_it_starts_is_unavailable(persons: Any) -> None:
    result = await _call(
        _tool(persons, FakeMemoryStore(), None),
        query="x", since="2026-10-10", until="2026-10-01",
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_an_ambiguous_person_is_asked_about_not_searched(tmp_path: Any) -> None:
    path = tmp_path / "twins.toml"
    path.write_text(
        '[persons.a]\nname = "Ah Ming"\naliases = ["阿星"]\n'
        '[[persons.a.signal]]\nphone = "+85211111111"\n'
        '[persons.b]\nname = "Ming Chai"\naliases = ["阿星"]\n'
        '[[persons.b.signal]]\nphone = "+85222222222"\n'
    )
    honcho = FakeMemoryStore()
    tools = _build(
        persons=load_persons(path), honcho=honcho,
        members=(frozenset({"+85211111111"}), frozenset({"+85222222222"})),
    )
    result = await _call(tools["memory_search"], query="x", person="阿星")
    assert result["status"] == "ambiguous" and result["note"]
    assert sorted(result["could_be"]) == ["Ah Ming", "Ming Chai"]
    assert honcho.chats == [] and honcho.searches == []


# ---- the index on ------------------------------------------------------------


async def test_on_answers_from_the_index_and_never_calls_honcho(persons: Any) -> None:
    honcho = FakeMemoryStore(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    metrics = Metrics()
    result = await _call(
        _tool(persons, honcho, _dense(_index(), metrics)),
        person="Miles", query="football sunday",
    )
    assert honcho.chats == [] and honcho.queries == [] and honcho.searches == []
    assert result["status"] == "ok" and result["note"]
    [item] = result["results"]
    assert item["kind"] == "message" and item["room"] == "Friends"
    assert item["people"] == ["Miles Novak"]
    assert item["path"] == ["message (matched)"]
    assert "41" not in str(result) and "miles_novak" not in str(result)
    assert metrics.get("nora_memory_search_total", status="ok") == 1


async def test_on_without_a_person_searches_everyone_in_scope(persons: Any) -> None:
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(_index(), Metrics())), query="football",
    )
    assert result["status"] == "ok" and "person" not in result
    assert [i["kind"] for i in result["results"]] == ["message"]


@pytest.mark.parametrize(("index", "status"), [
    (FakeMemoryIndex(state=IndexState("complete", 0)), "nothing_found"),
    (FakeMemoryIndex(state=IndexState("building", 0)), "partial"),
    (FakeMemoryIndex(fail=RuntimeError("db down")), "unavailable"),
])
async def test_on_every_status_carries_a_note(
    persons: Any, index: FakeMemoryIndex, status: str,
) -> None:
    honcho = FakeMemoryStore()
    result = await _call(
        _tool(persons, honcho, _dense(index, Metrics())), person="Miles",
        query="anything at all",
    )
    assert result["status"] == status
    assert result["note"]
    assert honcho.chats == []


async def test_on_an_item_carrying_an_identifier_is_withheld(persons: Any) -> None:
    index = FakeMemoryIndex([IndexedNode(
        "message", "42", "football: call +85298765432", _T, origin_room=_ROOM,
    )], embed_model="m")
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(index, Metrics())), query="football",
    )
    assert result["status"] == "withheld" and result["found_but_unquotable"] == 1
    assert result["note"] and "98765432" not in str(result)


async def test_on_a_render_room_that_raises_is_unavailable_with_a_note(
    persons: Any, monkeypatch: Any,
) -> None:
    def boom(self: Any, **_: Any) -> Any:
        raise RuntimeError("policy")

    monkeypatch.setattr(LineagePolicy, "render_room", boom)
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(_index(), Metrics())),
        person="Miles", query="football",
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_on_a_packet_that_raises_is_unavailable_with_a_note(
    persons: Any, monkeypatch: Any,
) -> None:
    import nora.agent.tools as tools_module

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("render")

    monkeypatch.setattr(tools_module, "render_packet", boom)
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(_index(), Metrics())),
        person="Miles", query="football",
    )
    assert result["status"] == "unavailable" and result["note"]


async def test_on_partial_with_no_items_is_recorded_as_partial(persons: Any) -> None:
    metrics = Metrics()
    index = FakeMemoryIndex(state=IndexState("building", 0))
    tools = _build_with(persons, FakeMemoryStore(), _dense(index, Metrics()), metrics)
    result = await _call(tools["memory_search"], person="Miles", query="football")
    assert result["status"] == "partial"
    assert metrics.get("nora_room_lookups_total", tool="memory_search", status="partial") == 1
    assert metrics.get(
        "nora_room_lookups_total", tool="memory_search", status="nothing_found") == 0


async def test_on_a_listed_persons_name_in_the_question_is_pinned(persons: Any) -> None:
    """65 V2: the names the room's roster resolves reach the search, and a
    node holding one literally ranks first."""
    index = FakeMemoryIndex(
        [
            IndexedNode("message", "43", "Tyler kept the receipts", _T - _DAY,
                        origin_room=_ROOM),
            IndexedNode("message", "44", "who kept the receipts", _T, origin_room=_ROOM),
        ],
        embed_model="m",
    )
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(index, Metrics())),
        query="who kept the receipts, Tyler?",
    )
    assert result["status"] == "ok"
    assert [i["path"] for i in result["results"]] == [["message (exact)"], ["message (matched)"]]


# ---- the narrowing applies inside the index's reads --------------------------


def _crowded() -> FakeMemoryIndex:
    """More near-identical OUT-of-window messages than the dense seed takes,
    nearer the question than the one IN-window message. A filter applied
    after a top-k would see only the crowd and answer «nothing»."""
    crowd = [
        IndexedNode("message", f"c{i}", "receipts receipts receipts", _T - 30 * _DAY,
                    origin_room=_ROOM)
        for i in range(SEED_LIMIT + 5)
    ]
    inside = IndexedNode("message", "in", "the receipts are in the drawer", _T,
                         origin_room=_ROOM)
    return FakeMemoryIndex([*crowd, inside], embed_model="m")


async def test_on_an_out_of_window_node_is_never_returned_and_takes_no_slot(
    persons: Any,
) -> None:
    index = _crowded()
    unfiltered = await _call(
        _tool(persons, FakeMemoryStore(), _dense(index, Metrics())), query="receipts",
    )
    assert "the receipts are in the drawer" not in str(unfiltered)  # crowded out
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(_crowded(), Metrics())),
        query="receipts", since="2023-11-14",
    )
    assert result["status"] == "ok"
    assert [i["text"] for i in result["results"]] == ["the receipts are in the drawer"]


async def test_on_until_is_the_end_of_that_day(persons: Any) -> None:
    """`until` names a day, not its midnight (`_parse_until`'s reading): a
    node at 22:13 UTC on 2023-11-14 is 06:13 HKT on the 15th, so `until`
    the 14th leaves it out and `until` the 15th keeps it."""
    tool = _tool(persons, FakeMemoryStore(), _dense(_index(), Metrics()))
    out = await _call(tool, query="football", until="2023-11-14")
    kept = await _call(tool, query="football", until="2023-11-15")
    assert out["status"] == "nothing_found"
    assert kept["status"] == "ok"


async def test_on_kinds_narrow_to_their_node_kinds(persons: Any) -> None:
    index = FakeMemoryIndex(
        [
            IndexedNode("message", "50", "dinner at the harbour", _T, origin_room=_ROOM),
            IndexedNode("memo", "m1", "dinner at the harbour, kept", _T, unit="u1"),
            IndexedNode("event", "e1", "dinner at the harbour", _T, unit="u1"),
        ],
        embed_model="m",
    )
    tool = _tool(persons, FakeMemoryStore(), _dense(index, Metrics()))
    notes = await _call(tool, query="dinner harbour", kinds=["notes"])
    both = await _call(tool, query="dinner harbour", kinds=["notes", "events"])
    assert [i["kind"] for i in notes["results"]] == ["memo"]
    assert sorted(i["kind"] for i in both["results"]) == ["event", "memo"]


async def test_on_a_person_narrows_to_what_is_theirs(persons: Any) -> None:
    """The person narrowing (`nora.domain.memory_index.narrowed`): what they
    authored, what is about them; never another person's message, however
    near the question it is."""
    index = FakeMemoryIndex(
        [
            IndexedNode("message", "60", "pizza on friday", _T, origin_room=_ROOM),
            IndexedNode("message", "61", "pizza on friday please", _T, origin_room=_ROOM),
            IndexedNode("note", "n1", "pizza night plan", _T, unit="u1"),
            IndexedNode("person", "miles_novak"),
            IndexedNode("person", "tyler_cortez"),
        ],
        [
            ("message", "60", "authored_by", "person", "miles_novak"),
            ("message", "61", "authored_by", "person", "tyler_cortez"),
            ("note", "n1", "about", "person", "miles_novak"),
        ],
        embed_model="m",
    )
    result = await _call(
        _tool(persons, FakeMemoryStore(), _dense(index, Metrics())),
        query="pizza friday", person="Miles",
    )
    assert result["status"] == "ok"
    assert sorted(i["text"] for i in result["results"]) == [
        "pizza night plan", "pizza on friday"]
