"""The retrieval walk (`nora.agent.memory_search`, design/unified-memory/
25-memory-graph.md §4) over `FakeMemoryIndex`, which answers through the same
scope predicate the contract suite holds `SqlMemoryIndex` to. The scope suite
against Postgres is `tests/integration/test_memory_scope.py`; this is the walk's
own logic: seeds, the hub rule, the listed-room rule, adaptive depth, the
ceiling, ranking, statuses and the packet.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.agent.memory_search import (
    HOP_CEILING,
    RELEVANCE_FLOOR,
    MemorySearch,
    render_packet,
    scope_for,
    terms_of,
    walk,
)
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import IndexedNode, IndexState
from nora.obs.metrics import Metrics
from nora.testing.memory_index import FakeMemoryIndex

NOW = 1_790_000_000_000
_B = RoomFacts("signal", frozenset({"b"}), "u1", frozenset({"kit", "sam"}),
               location=True, label="B")
_C = RoomFacts("signal", frozenset({"c"}), "u1", frozenset({"sam"}), label="C")
_D = RoomFacts("signal", frozenset({"d"}), "u2", frozenset({"kit"}), label="D")


def _target(room: RoomFacts = _B, live: LiveRoom | None = None) -> RenderRoom:
    return RenderRoom(
        room=room, rooms=(_B, _C, _D), circles=(frozenset({"kit", "sam"}),),
        live=live if live is not None else LiveRoom(person_ids=room.persons or frozenset()),
    )


def _msg(sid: str, text: str, room: str = "signal:b", at: int = NOW) -> IndexedNode:
    return IndexedNode("message", sid, text, at, origin_room=room)


def _note(sid: str, text: str = "", at: int = NOW) -> IndexedNode:
    return IndexedNode("note", sid, text, at, unit="u1")


async def _walk(index: FakeMemoryIndex, query: str, hops: int = 2, person: str | None = None):
    scope = scope_for(_target())
    assert scope is not None
    return {
        h.node.key: h for h in await walk(
            index, scope, terms=terms_of(query), person_id=person, hops=hops,
            admits=lambda n: True,
        )
    }


# --- scope_for ----------------------------------------------------------------


def test_scope_is_the_unit_its_rooms_and_who_is_listed_here() -> None:
    scope = scope_for(_target())
    assert scope is not None
    assert scope.units == ("u1",)
    assert set(scope.rooms) == {"signal:b", "signal:c"}
    assert scope.listed_here == {"kit", "sam"}
    assert scope.presence_persons == {"kit", "sam"}
    assert not scope.exclude_strict
    assert scope.room_persons["signal:c"] == {"sam"}


def test_a_room_that_cannot_vouch_for_who_is_in_it_is_strict() -> None:
    for live in (LiveRoom(frozenset({"kit"}), unnamed=1),
                 LiveRoom(frozenset(), available=False),
                 LiveRoom(frozenset({"kit"}), degraded=True)):
        scope = scope_for(_target(live=live))
        assert scope is not None and scope.exclude_strict
    star = RoomFacts("signal", frozenset({"s"}), "u1", None)
    scope = scope_for(_target(star))
    assert scope is not None and scope.exclude_strict and scope.listed_here == frozenset()


def test_presence_passes_only_where_the_room_shares_location() -> None:
    scope = scope_for(_target(_C))
    assert scope is not None and scope.presence_persons == frozenset()


def test_a_room_outside_the_policy_has_no_scope() -> None:
    assert scope_for(RenderRoom(room=None)) is None


def test_terms_drop_grammar_and_keep_cjk_runs_whole() -> None:
    assert terms_of("What did Kit's sister say about 大步行 at the beach?") == [
        "kit", "sister", "大步行", "beach",
    ]


# --- the walk -----------------------------------------------------------------


async def test_the_hub_rule_a_person_is_a_neighbour_never_a_bridge() -> None:
    index = FakeMemoryIndex(
        [_msg("1", "ramen crawl"), _msg("2", "landlord rent"), IndexedNode("person", "kit")],
        [("message", "1", "authored_by", "person", "kit"),
         ("message", "2", "authored_by", "person", "kit")],
    )
    hits = await _walk(index, "ramen crawl", hops=4)
    assert set(hits) == {("message", "1")}
    assert hits[("message", "1")].people == {"kit"}


async def test_the_listed_room_rule() -> None:
    index = FakeMemoryIndex(
        [_msg("1", "farewell gift", room="signal:c"), _msg("2", "farewell drinks"),
         IndexedNode("person", "kit")],
        [("message", "1", "mentions", "person", "kit"),
         ("message", "2", "mentions", "person", "kit")],
    )
    hits = await _walk(index, "farewell")
    assert hits[("message", "1")].people == set()   # C does not list Kit
    assert hits[("message", "2")].people == {"kit"}


async def test_a_path_is_recorded_hop_by_hop() -> None:
    index = FakeMemoryIndex(
        [_msg("1", "ramen crawl"), _note("n1", "plans"), _msg("2", "bring cash")],
        [("note", "n1", "cites", "message", "1"), ("note", "n1", "cites", "message", "2")],
    )
    hits = await _walk(index, "ramen crawl")
    assert [(s.kind, s.rel, s.outgoing) for s in hits[("message", "2")].path] == [
        ("message", None, True), ("note", "cites", False), ("message", "cites", True),
    ]


async def test_depth_is_the_hop_count_and_never_past_the_ceiling() -> None:
    chain = [_msg("0", "ramen crawl")] + [_note(f"n{i}") for i in range(1, 8)]
    edges = [("note", "n1", "cites", "message", "0")] + [
        ("note", f"n{i + 1}", "cites", "note", f"n{i}") for i in range(1, 7)
    ]
    for hops, reached in ((2, 2), (3, 3), (9, HOP_CEILING)):
        index = FakeMemoryIndex(chain, edges)
        hits = await _walk(index, "ramen crawl", hops=hops)
        assert ("note", f"n{reached}") in hits
        assert ("note", f"n{reached + 1}") not in hits


async def test_the_walk_stops_when_a_hop_keeps_nothing_above_the_floor() -> None:
    # `in_room` carries 0.3: from a 1.0 seed, 0.3 then 0.09, below the floor.
    assert 0.3 * 0.3 < RELEVANCE_FLOOR < 0.3
    index = FakeMemoryIndex(
        [_msg("1", "ramen crawl"), _note("n1"), _note("n2"), _note("n3")],
        [("message", "1", "in_room", "note", "n1"), ("note", "n1", "in_room", "note", "n2"),
         ("note", "n2", "in_room", "note", "n3")],
    )
    hits = await _walk(index, "ramen crawl", hops=4)
    assert set(hits) == {("message", "1"), ("note", "n1")}
    assert index.calls.count("neighbours") == 2  # the second hop kept nothing


async def test_a_named_person_seeds_their_accounts_only_where_listed() -> None:
    index = FakeMemoryIndex(
        [IndexedNode("event", "e1", "picnic", NOW, unit="u1"), IndexedNode("person", "kit")],
        [("event", "e1", "about", "person", "kit")],
    )
    assert ("event", "e1") in await _walk(index, "", person="kit")
    assert await _walk(index, "", person="rin") == {}


async def test_an_episode_must_pass_its_own_lineage() -> None:
    stay = IndexedNode("stay", "s1", "stay", NOW, persons=("kit",),
                       lineage=[["presence", "kit", None]])
    scope = scope_for(_target())
    assert scope is not None
    index = FakeMemoryIndex([stay])
    kept = await walk(index, scope, terms=["stay"], person_id=None, hops=2,
                      admits=lambda n: n.kind != "stay")
    assert kept == []


# --- statuses -----------------------------------------------------------------


async def _search(index: FakeMemoryIndex, query: str = "ramen", metrics: Metrics | None = None,
                  target: RenderRoom | None = None):
    return await MemorySearch(index, metrics or Metrics()).search(
        query=query, person_id=None, target=target or _target(), depth="low", now_ms=NOW,
    )


@pytest.mark.parametrize(("nodes", "state", "status"), [
    ([_msg("1", "ramen")], IndexState("complete", 0), "ok"),
    ([], IndexState("complete", 0), "nothing_found"),
    ([], IndexState("never", 0), "partial"),
    ([_msg("1", "ramen")], IndexState("building", 0), "partial"),
    ([], IndexState("complete", 3), "partial"),
])
async def test_status(nodes: list[IndexedNode], state: IndexState, status: str) -> None:
    metrics = Metrics()
    found = await _search(FakeMemoryIndex(nodes, state=state), metrics=metrics)
    assert found.status == status
    assert metrics.get("nora_memory_search_total", status=status) == 1


async def test_any_error_is_unavailable_and_counted() -> None:
    metrics = Metrics()
    found = await _search(FakeMemoryIndex([_msg("1", "ramen")], fail=OSError()), metrics=metrics)
    assert (found.status, found.hits) == ("unavailable", ())
    assert metrics.get("nora_memory_search_total", status="unavailable") == 1


async def test_a_room_outside_the_policy_is_unavailable() -> None:
    index = FakeMemoryIndex([_msg("1", "ramen")])
    found = await _search(index, target=RenderRoom(room=None))
    assert found.status == "unavailable" and index.calls == []


async def test_newer_ranks_above_older_at_equal_relevance() -> None:
    index = FakeMemoryIndex([_msg("old", "ramen", at=NOW - 400 * 86_400_000),
                             _msg("new", "ramen", at=NOW - 86_400_000)])
    found = await _search(index)
    assert [h.node.source_id for h in found.hits] == ["new", "old"]


# --- the packet -----------------------------------------------------------------


async def test_the_packet_names_people_and_rooms_never_ids_and_screens_identifiers() -> None:
    index = FakeMemoryIndex(
        [_msg("1", "ramen at noon"), _msg("2", "ramen? call +85298765432"),
         IndexedNode("media", "1:" + "ab" * 32, "a bowl of ramen", NOW,
                     origin_room="signal:b", sha256="ab" * 32),
         IndexedNode("person", "kit")],
        [("message", "1", "authored_by", "person", "kit")],
    )
    found = await _search(index)
    items, withheld = render_packet(
        found.hits, name_of={"kit": "Kit"}.__getitem__, room_label=lambda k: "B",
        now=datetime.fromtimestamp(NOW / 1000, UTC),
    )
    assert withheld == 1
    by_kind = {i["kind"]: i for i in items}
    assert by_kind["message"]["people"] == ["Kit"] and by_kind["message"]["room"] == "B"
    assert by_kind["media"]["handle"] == "ababab"
    assert by_kind["message"]["path"] == ["message (matched)"]
    text = str(items)
    assert "98765432" not in text and "signal:b" not in text and "'kit'" not in text
