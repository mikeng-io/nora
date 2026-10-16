"""`memory_search`, the memory graph's one search verb (DEC-316).

It is offered only while `[memory_index] enabled` (the surface tests are
`tests/unit/test_memory_surfaces.py`), and there is no Honcho behind it: the
index answers. `person`, `since`, `until` and `kinds` narrow the search
INSIDE the index's reads, so a node outside them is never returned and never
takes a slot; every status carries a note, and nothing in the result is an
id.
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
    """The search as `[memory_index] embed` builds it: dense seeds
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


# ---- answers -----------------------------------------------------------------


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
    """The names the room's roster resolves reach the search, and a
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
    node at 22:13 UTC on 14 Nov 2023 is 06:13 HKT on the 15th, so `until`
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


def _lineage_listing(listed: frozenset[str] | None) -> LineagePolicy:
    facts = RoomFacts("signal", frozenset({GROUP}), "u1", listed, label="Friends")
    return LineagePolicy(rooms=(facts,), _by_id={("signal", GROUP): facts})


@pytest.mark.parametrize("listed", [
    # The live roster has Miles; policy lists only Tyler here.
    frozenset({"tyler_cortez"}),
    # A `*` room: policy lists nobody (persons=None).
    None,
])
async def test_on_a_person_the_room_does_not_list_is_unavailable_not_nothing(
    persons: Any, listed: frozenset[str] | None,
) -> None:
    """A person the live roster resolves but
    policy does not list for this room would be narrowed to false in every
    read, a false `nothing_found` over an index that holds their words. It
    is `unavailable` with a note, before any read."""
    index = _index()
    result = await _call(
        _build_with(
            persons, FakeMemoryStore(), _dense(index, Metrics()),
            lineage=_lineage_listing(listed),
        )["memory_search"],
        query="football", person="Miles",
    )
    assert result["status"] == "unavailable", result
    assert result["note"] and result["person"] == "Miles Novak"


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


# ---- who reads which rooms -----------------------------------------------------

_OTHER = "g-other"
_OTHER_ROOM = RoomFacts(
    "signal", frozenset({_OTHER}), "u1", frozenset({"miles_novak", "tyler_cortez"}),
    label="Other",
)
_TWO_ROOMS = LineagePolicy(
    rooms=(_FRIENDS, _OTHER_ROOM),
    _by_id={("signal", GROUP): _FRIENDS, ("signal", _OTHER): _OTHER_ROOM},
)


def _two_rooms_index() -> FakeMemoryIndex:
    """One line in this room and one in another room of the same unit, both
    by a member both rooms list, so only the room boundary separates them."""
    return FakeMemoryIndex(
        [
            IndexedNode("message", "71", "the pitch is booked here", _T, origin_room=_ROOM),
            IndexedNode("message", "72", "the pitch line from the other room", _T,
                        origin_room=f"signal:{_OTHER}"),
            IndexedNode("person", "miles_novak"),
        ],
        [
            ("message", "71", "authored_by", "person", "miles_novak"),
            ("message", "72", "authored_by", "person", "miles_novak"),
        ],
        embed_model="m",
    )


@pytest.mark.parametrize("person", [None, "Miles"])
async def test_a_culture_pass_never_reads_another_rooms_line(
    persons: Any, person: str | None,
) -> None:
    """DEC-316: a room's culture is written
    from that room's own speech, as the culture adaptor's Honcho
    `search_memory` read only its own session. Its `memory_search` reads its
    own room alone; the unit's other rooms never reach the pass."""
    tool = _build_with(
        persons, FakeMemoryStore(), _dense(_two_rooms_index(), Metrics()),
        lineage=_TWO_ROOMS, dream_adaptor="culture",
    )["memory_search"]
    result = await _call(tool, query="the pitch", **({"person": person} if person else {}))
    assert result["status"] == "ok", result
    assert [i["text"] for i in result["results"]] == ["the pitch is booked here"]
    assert "other room" not in str(result)


@pytest.mark.parametrize("adaptor", ["", "character-person"])
async def test_every_other_occasion_reads_the_unit_under_the_listed_room_rule(
    persons: Any, adaptor: str,
) -> None:
    """The positive control, and the rule for everything that is not the
    culture fold: the unit's other rooms are read (a member both rooms list
    said it)."""
    tool = _build_with(
        persons, FakeMemoryStore(), _dense(_two_rooms_index(), Metrics()),
        lineage=_TWO_ROOMS, dream_adaptor=adaptor,
    )["memory_search"]
    result = await _call(tool, query="the pitch")
    assert result["status"] == "ok", result
    assert sorted(i["text"] for i in result["results"]) == [
        "the pitch is booked here", "the pitch line from the other room"]


# ---- the social stream's items (design/cognition-loop/22 §9) ------------------


def _social_index() -> FakeMemoryIndex:
    """One story by each listed member, and one by a person no room here
    lists; and a message, which `kinds=media` leaves out."""
    return FakeMemoryIndex(
        [
            IndexedNode("social", "instagram:1", "Instagram story\nlantern picnic", _T,
                        persons=("miles_novak",)),
            IndexedNode("social", "instagram:2", "Instagram post\nlantern picnic", _T,
                        persons=("tyler_cortez",)),
            IndexedNode("social", "instagram:3", "Instagram reel\nlantern picnic", _T,
                        persons=("elsewhere_liu",)),
            IndexedNode("message", "60", "lantern picnic", _T, origin_room=_ROOM),
        ],
        embed_model="m",
    )


async def test_on_kinds_media_returns_social_items_saying_what_and_how_old(
    persons: Any,
) -> None:
    tool = _tool(persons, FakeMemoryStore(), _dense(_social_index(), Metrics()))
    result = await _call(tool, query="lantern picnic", kinds=["media"])
    assert result["status"] == "ok"
    shown = {(i["kind"], i["text"]) for i in result["results"]}
    assert shown == {
        ("Instagram story", "lantern picnic"), ("Instagram post", "lantern picnic"),
    }, "a person this room does not list is never read, and kinds=media has no message"
    assert all(i.get("age") for i in result["results"])


async def test_on_social_off_leaves_their_items_out_whenever_recorded(
    tmp_path: Any,
) -> None:
    """`social = false` in identity.toml, set after the item was indexed:
    the read leaves it out; the other member's stays."""
    path = tmp_path / "identity.toml"
    marked = IDENTITY.replace(
        'aliases = ["阿豹", "Tyler"]', 'aliases = ["阿豹", "Tyler"]\nsocial = false',
    )
    assert marked != IDENTITY
    path.write_text(marked)
    off = load_persons(path)
    assert off.social_off("tyler_cortez")
    tool = _tool(off, FakeMemoryStore(), _dense(_social_index(), Metrics()))
    result = await _call(tool, query="lantern picnic", kinds=["media"])
    assert [i["kind"] for i in result["results"]] == ["Instagram story"]
