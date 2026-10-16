"""The retrieval walk (`nora.agent.memory_search`) over `FakeMemoryIndex`, which answers through
the same scope predicate the contract suite holds `SqlMemoryIndex` to. The scope suite
against Postgres is `tests/integration/test_memory_scope.py`; this is the walk's
own logic: seeds, the hub rule, the listed-room rule, adaptive depth, the
ceiling, ranking, statuses and the packet.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.agent import memory_search
from nora.agent.memory_search import (
    HOP_CEILING,
    RELEVANCE_FLOOR,
    MemorySearch,
    MemorySearchResult,
    exact_terms_of,
    render_packet,
    scope_for,
    terms_of,
    walk,
)
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import IndexedNode, IndexState, exact_holds
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing.memory_index import FakeMemoryIndex, FakeQueryEmbedder, bag_of_words

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
    scope = scope_for(_target(), social_off=())
    assert scope is not None
    return {
        h.node.key: h for h in await walk(
            index, scope, terms=terms_of(query), person_id=person, hops=hops,
            admits=lambda n: True,
        )
    }


# --- scope_for ----------------------------------------------------------------


def test_scope_is_the_unit_its_rooms_and_who_is_listed_here() -> None:
    scope = scope_for(_target(), social_off=())
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
        scope = scope_for(_target(live=live), social_off=())
        assert scope is not None and scope.exclude_strict
    star = RoomFacts("signal", frozenset({"s"}), "u1", None)
    scope = scope_for(_target(star), social_off=())
    assert scope is not None and scope.exclude_strict and scope.listed_here == frozenset()


def test_presence_passes_only_where_the_room_shares_location() -> None:
    scope = scope_for(_target(_C), social_off=())
    assert scope is not None and scope.presence_persons == frozenset()


def test_a_room_outside_the_policy_has_no_scope() -> None:
    assert scope_for(RenderRoom(room=None), social_off=()) is None


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
    scope = scope_for(_target(), social_off=())
    assert scope is not None
    index = FakeMemoryIndex([stay])
    kept = await walk(index, scope, terms=["stay"], person_id=None, hops=2,
                      admits=lambda n: n.kind != "stay")
    assert kept == []


# --- statuses -----------------------------------------------------------------


M = "m"


async def _search(index: FakeMemoryIndex, query: str = "ramen", metrics: Metrics | None = None,
                  target: RenderRoom | None = None, embedder: FakeQueryEmbedder | None = None,
                  people: tuple[PersonRef, ...] = ()):
    """A search with dense seeds over model `M`, the fake index's vectors
    (`embed_model=M`); without them it falls back to lexical (`_lexical`
    is the search with no embedder at all)."""
    return await MemorySearch(
        index, metrics or Metrics(), embedder=embedder or FakeQueryEmbedder(), model=M,
    ).search(query=query, person_id=None, target=target or _target(), depth="low",
             now_ms=NOW, people=people, social_off=())


async def _lexical(index: FakeMemoryIndex, query: str = "ramen", metrics: Metrics | None = None):
    return await MemorySearch(index, metrics or Metrics()).search(
        query=query, person_id=None, target=_target(), depth="low", now_ms=NOW,
        social_off=(),
    )


async def test_kinds_narrow_the_answer_not_the_hops_so_an_outing_is_reached() -> None:
    """An episode has no vector and its text is a kind and an interval, so applying the kinds inside
    the hops too would find almost nothing for a search for `places`. The message said during the
    outing seeds (the bridge pass), the hop reaches the outing, and the
    answer holds the outing alone. The outing is presence-permitted here
    (`_B` has `location`, lists Kit, and the row passes its lineage)."""
    outing = IndexedNode("outing", "o1", "outing", NOW, unit="u1", persons=("kit",),
                         lineage=[["presence", "kit", None]])
    index = FakeMemoryIndex(
        [_msg("1", "ramen at the night market"), outing],
        [("message", "1", "during", "outing", "o1")],
        embed_model=M,
    )
    found = await MemorySearch(
        index, Metrics(), embedder=FakeQueryEmbedder(), model=M,
    ).search(query="ramen night market", person_id=None, target=_target(),
             depth="low", now_ms=NOW, kinds=("outing", "stay", "trip"), social_off=())
    assert found.status == "ok", found
    assert [h.node.key for h in found.hits] == [("outing", "o1")]


@pytest.mark.parametrize(("nodes", "state", "status"), [
    ([_msg("1", "ramen")], IndexState("complete", 0), "ok"),
    ([], IndexState("complete", 0), "nothing_found"),
    ([], IndexState("never", 0), "partial"),
    ([_msg("1", "ramen")], IndexState("building", 0), "partial"),
    ([], IndexState("complete", 3), "partial"),
])
async def test_status(nodes: list[IndexedNode], state: IndexState, status: str) -> None:
    metrics = Metrics()
    found = await _search(FakeMemoryIndex(nodes, state=state, embed_model=M), metrics=metrics)
    assert found.status == status
    assert metrics.get("nora_memory_search_total", status=status) == 1


async def test_the_lexical_fallback_is_always_partial_never_nothing_found() -> None:
    """With no embedder (`[memory_index] embed` off) the seeds are
    lexical, and a lexical miss is not «you never said that»."""
    hit = await _lexical(FakeMemoryIndex([_msg("1", "ramen")]))
    assert (hit.status, [h.node.source_id for h in hit.hits]) == ("partial", ["1"])
    assert (await _lexical(FakeMemoryIndex([]))).status == "partial"


async def test_a_failing_embedder_falls_back_to_lexical_and_answers_partial() -> None:
    index = FakeMemoryIndex([_msg("1", "ramen crawl")], embed_model=M)
    found = await _search(index, "ramen", embedder=FakeQueryEmbedder(fail=TimeoutError()))
    assert found.status == "partial"
    assert [h.node.source_id for h in found.hits] == ["1"]
    assert "seed" in index.calls and "dense" not in index.calls


async def test_the_question_is_embedded_once_and_dense_seeds_replace_lexical() -> None:
    index = FakeMemoryIndex([_msg("1", "ramen crawl"), _msg("2", "noodle shop")], embed_model=M)
    embedder = FakeQueryEmbedder()
    found = await _search(index, "ramen crawl tonight", embedder=embedder)
    assert embedder.calls == ["ramen crawl tonight"]
    assert "dense" in index.calls and "seed" not in index.calls
    assert [h.node.source_id for h in found.hits] == ["1"] and found.status == "ok"


async def test_coverage_holds_it_partial_until_every_embeddable_node_has_a_vector() -> None:
    waiting = _msg("2", "noodle shop")
    index = FakeMemoryIndex([_msg("1", "ramen"), waiting], embed_model=M)
    del index.vectors[waiting.key]
    assert (await _search(index)).status == "partial"
    index.given_up.add(waiting.key)  # the worker gave up: not waited for
    assert (await _search(index)).status == "ok"


async def test_a_fresh_wait_is_read_by_its_words_and_is_not_partial() -> None:
    """A node inside the worker's few-tick window is normal operation: the
    lexical seed reads it beside the dense one, and the answer is `ok`. The
    same wait without the tick, or past the window, is a backlog."""
    fresh = _msg("2", "noodle shop")
    index = FakeMemoryIndex([_msg("1", "ramen"), fresh], embed_model=M, fresh=[fresh.key])
    del index.vectors[fresh.key]

    async def search(drain_seconds: float | None) -> MemorySearchResult:
        return await MemorySearch(
            index, Metrics(), embedder=FakeQueryEmbedder(), model=M,
            drain_seconds=drain_seconds,
        ).search(query="noodle shop", person_id=None, target=_target(), depth="low",
                 now_ms=NOW, social_off=())

    found = await search(60.0)
    assert (found.status, [h.node.source_id for h in found.hits]) == ("ok", ["2"])
    assert found.hits[0].path[0].how == "matched"
    assert (await search(None)).status == "partial"
    index.fresh.clear()  # still waiting past the window
    assert (await search(60.0)).status == "partial"


async def test_no_vector_yet_uses_lexical_without_calling_the_embedder() -> None:
    index = FakeMemoryIndex([_msg("1", "ramen")])  # nothing embedded
    embedder = FakeQueryEmbedder()
    found = await _search(index, embedder=embedder)
    assert (found.status, embedder.calls) == ("partial", [])
    assert [h.node.source_id for h in found.hits] == ["1"]


async def test_an_exact_hit_is_pinned_ahead_of_a_nearer_newer_dense_hit() -> None:
    named = _msg("named", "Rin booked a table", at=NOW - 60 * 86_400_000)
    twin = _msg("twin", "booked a table tonight", at=NOW)
    index = FakeMemoryIndex([named, twin], embed_model=M)
    q = "booked a table tonight"  # the twin's own words: cosine 1
    rin = PersonRef("kit", "Rin", "kit")  # listed in B under id `kit`
    found = await _search(index, "did Rin say " + q,
                          embedder=FakeQueryEmbedder({"did Rin say " + q: bag_of_words(q)}),
                          people=(rin,))
    assert [h.node.source_id for h in found.hits] == ["named", "twin"]
    assert found.hits[0].pin > 0 and found.hits[0].path[0].how == "exact"


async def test_a_name_of_someone_this_room_does_not_list_is_not_exact() -> None:
    index = FakeMemoryIndex([_msg("1", "Rin booked a table")], embed_model=M)
    stranger = PersonRef("rin", "Rin", "rin")  # not listed in B
    found = await _search(index, "did Rin book", people=(stranger,))
    assert all(h.pin == 0 for h in found.hits)
    assert "exact" not in index.calls


# --- what counts as exact -------------------------------------------------------


@pytest.mark.parametrize(("query", "names", "terms"), [
    ("what did they say about the restaurant downtown yesterday?", [], []),
    ("remember the 2026 trip, 3pm at gate 12, room 404?", [], []),
    # A time, an ordinal, a measure or a name with a number
    # is not an id.
    ("COVID-19 at 5pm-7pm on the 100th day, iPhone15, 128GB", [], []),
    # A person's name only as written, never a common word or a bare surname
    # (`_names` drops it), and a full-width name is the same name (NFKC).
    ("will you come, and may I ask about mark?", ["Will", "May", "Mark"], []),
    ("May I ask, Will?", ["Will", "May"], []),
    ("did kit come", ["Kit"], []),
    ("did Ｋｉｔ come", ["Kit"], ["Kit"]),
    ("ＺＸ４４７１ is the booking", [], ["ZX4471"]),
    ("did @night_owl post https://example.org/menu?x=1.", [],
     ["@night_owl", "https://example.org/menu?x=1"]),
    ("booking ZX4471 or order 88812345", [], ["ZX4471", "88812345"]),
    ("write to me@example.org", [], []),
    ("what did Kit Chan say in the kitchen", ["Kit", "Kit Chan", "Chan"], ["Kit Chan"]),
    ("阿豹妹今日講咩", ["阿豹", "阿豹妹"], ["阿豹妹"]),
    ("is X coming", ["X"], []),
    ("the status at https://example.org/s/1234567", [], ["https://example.org/s/1234567"]),
])
def test_exact_terms_are_literal_references_never_ordinary_words(
    query: str, names: list[str], terms: list[str],
) -> None:
    assert [t.text for t in exact_terms_of(query, names)] == terms


def test_a_name_is_cased_and_a_url_handle_or_id_is_not() -> None:
    found = exact_terms_of("Kit sent @night_owl ZX4471", ["Kit"])
    assert [(t.text, t.cased) for t in found] == [
        ("Kit", True), ("@night_owl", False), ("ZX4471", False)]


def test_a_person_is_pinned_by_full_name_and_alias_never_the_bare_surname() -> None:
    """A family name pins everything about the family, and a
    romanised one (Ho, Lo, Ma) is two letters."""
    kit = PersonRef("kit", "Kit", "kit", first_name="Kit", last_name="Ho", aliases=("阿傑",))
    assert sorted(memory_search._names(kit)) == ["Kit", "Kit", "Kit Ho", "阿傑"]
    assert exact_terms_of("the Ho family dinner", memory_search._names(kit)) == []
    called_by_surname = PersonRef("ho", "Ho", "ho", first_name="Kit", last_name="Ho")
    assert memory_search._names(called_by_surname) == ["Kit", "Kit Ho"]


@pytest.mark.parametrize(("term", "text", "holds"), [
    ("Zoë", "Zoëy came", False),       # accented Latin: a whole word too
    ("Zoë", "with Zoë today", True),
    ("Kit", "Kit今日唔得閒", True),      # a CJK neighbour does not continue a word
    ("阿豹", "阿豹妹今日", True),        # any other script: a substring
])
def test_a_latin_name_matches_as_a_whole_word(term: str, text: str, holds: bool) -> None:
    assert exact_holds(term, text, cased=True) is holds


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


# --- screening and the listed-room rule ------------------------------------------------------


@pytest.mark.parametrize("ident", ["+85291234567", "3f2a9c1e-8b7d-4e6f-9a01-23456789abcd"])
def test_an_identifier_across_the_text_cut_withholds_the_item(ident: str) -> None:
    """R-18: the FULL text is screened, not the truncated one, so an
    identifier straddling `TEXT_CHARS` never leaves as a fragment."""
    from nora.agent.egress import scan_identifiers
    from nora.agent.memory_search import TEXT_CHARS, Hit, Step

    checked = 0
    for cut in range(2, len(ident) - 1):
        pad = TEXT_CHARS - cut
        text = ("x " * pad)[:pad - 1] + " " + ident + " end"
        assert scan_identifiers(text) is not None  # the whole text carries it
        node = IndexedNode("message", "m1", text, NOW, origin_room="signal:b")
        items, withheld = render_packet(
            [Hit(node, 1.0, (Step("message", how="matched"),))], name_of=str,
            room_label=str, now=datetime.fromtimestamp(NOW / 1000, UTC),
        )
        assert (items, withheld) == ([], 1), cut
        checked += 1
    assert checked > 5


async def test_an_about_seed_said_in_a_room_not_listing_the_person_is_not_kept() -> None:
    """The listed-room rule holds for exact-reference seeds too: an entry
    about Kit scoped to a room that does not list Kit is not evidence about Kit."""
    index = FakeMemoryIndex(
        [IndexedNode("event", "e1", "dinner plans", NOW, origin_room="signal:c"),
         IndexedNode("event", "e2", "picnic", NOW, origin_room="signal:b"),
         IndexedNode("person", "kit")],
        [("event", "e1", "about", "person", "kit"), ("event", "e2", "about", "person", "kit")],
    )
    hits = await _walk(index, "", person="kit")
    assert set(hits) == {("event", "e2")}


async def test_a_scope_that_raises_is_unavailable_and_counted(monkeypatch) -> None:
    import nora.agent.memory_search as ms

    def boom(target: RenderRoom, **_: object) -> None:
        raise RuntimeError("policy")

    monkeypatch.setattr(ms, "scope_for", boom)
    metrics = Metrics()
    found = await _search(FakeMemoryIndex([_msg("1", "ramen")]), metrics=metrics)
    assert found.status == "unavailable"
    assert metrics.get("nora_memory_search_total", status="unavailable") == 1


def test_a_social_medium_carries_no_handle_and_says_what_it_is() -> None:
    """`media_fetch` resolves only this chat's media: a handle for a social
    item's medium would fail as if nothing were there, so it gets none. An
    attachment keeps its handle."""
    from nora.agent.memory_search import Hit, Step

    sha = "e5" * 32
    hits = [
        Hit(IndexedNode("social_media", f"instagram:1:{sha}", "a picnic", NOW,
                        persons=("kit",), sha256=sha), 0.9, (Step("social_media"),)),
        Hit(IndexedNode("social", "instagram:1", "Instagram story\npicnic plans", NOW,
                        persons=("kit",)), 0.8, (Step("social"),)),
        Hit(IndexedNode("media", f"7:{sha}", "a picnic", NOW, origin_room="signal:b",
                        sha256=sha), 0.7, (Step("media"),)),
    ]
    items, _ = render_packet(
        hits, name_of=str, room_label=str, now=datetime.fromtimestamp(NOW / 1000, UTC),
    )
    assert [(i["kind"], i.get("handle"), i["text"]) for i in items] == [
        ("Instagram picture or video", None, "a picnic"),
        ("Instagram story", None, "picnic plans"),
        ("media", sha[:6], "a picnic"),
    ]


def test_the_opt_outs_are_never_defaulted() -> None:
    """A scope that forgot `social_off` would read every opted-out person's
    items: every builder must name it."""
    import dataclasses

    from nora.domain.memory_index import MemoryScope

    with pytest.raises(TypeError, match="social_off"):
        scope_for(_target())  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="social_off"):
        MemoryScope(  # type: ignore[call-arg]
            units=(), rooms=(), listed_here=frozenset(), people_here=frozenset(),
            exclude_strict=False, presence_persons=frozenset(),
        )
    import inspect
    param = inspect.signature(MemorySearch.search).parameters["social_off"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert {f.name for f in dataclasses.fields(MemoryScope) if f.kw_only} == {"social_off"}
