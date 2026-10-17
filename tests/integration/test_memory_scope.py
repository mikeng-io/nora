"""The memory index's read stays inside the reading room's scope, on every
node of every path (the decision index's scope row).

One migrated database for the module, truncated between tests. Messages,
memos and hides go THROUGH THE STORES, so the index is what live writes
projected; the shapes no store projects yet (a `mentions` edge, a stay with
its presence lineage, a place hub) are written into the index directly, the
way a later projector will write them.

The policy: unit `u1` holds room B (Kit, Sam, Rin; location on), B2 (Kit,
Rin; location off) and C (Sam, Rin); unit `u2` holds D (Kit, Sam). Every read
is from B unless a test says otherwise, with its whole roster present.

Every test runs three times: with DENSE seeds, every embeddable node given
the toy `bag_of_words` vector of its text under the search's model and the
question embedded the same way, so «near» means shared words and an
out-of-scope node can be as near as an in-scope one; with LEXICAL seeds,
the fallback, whose every answer is `partial`; and FRESH, dense seeds with
no node the test wrote embedded yet (only an unrelated anchor holds a
vector), as when every line was said a moment ago: each is reached by the
lexical seed over the nodes no vector reaches, inside the same scope,
and the answer is `ok`. The scope rules are the same in all three, because
every seed runs the same filter inside its own scan.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.agent.memory_search import MemorySearch, MemorySearchResult, render_packet
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import EMBEDDED_KINDS
from nora.domain.models import NormalizedMessage
from nora.domain.social_stream import MediaItem, SocialItem
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_INDEX_STATE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
)
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.obs.metrics import Metrics
from nora.testing.memory_index import FakeQueryEmbedder, bag_of_words

NOW = 1_790_000_000_000
MODEL = "model-a.gguf@0000+fmt1"
_B = RoomFacts("signal", frozenset({"b"}), "u1", frozenset({"kit", "sam", "rin"}),
               location=True, label="B")
_B2 = RoomFacts("signal", frozenset({"b2"}), "u1", frozenset({"kit", "rin"}), label="B2")
_C = RoomFacts("signal", frozenset({"c"}), "u1", frozenset({"sam", "rin"}), label="C")
_D = RoomFacts("signal", frozenset({"d"}), "u2", frozenset({"kit", "sam"}), label="D")
_ROOMS = (_B, _B2, _C, _D)
_CIRCLES = (frozenset({"kit", "sam", "rin"}),)


def _target(room: RoomFacts = _B) -> RenderRoom:
    return RenderRoom(
        room=room, rooms=_ROOMS, circles=_CIRCLES,
        live=LiveRoom(person_ids=room.persons or frozenset()),
    )


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_scope_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    async with eng.begin() as conn:
        await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="complete"))
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "messages", "memo", "memo_revision", "row_history", "memory_node",
                "memory_edge", "memory_projection_queue", "social_item",
                "social_item_state", "social_perception",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _say(engine: Any, room: str, body: str, person: str, ts: int = NOW - 3_600_000) -> str:
    mid = await SqlRecordStore(engine).append(
        message=NormalizedMessage("signal", room, "group", f"sender-{person}", person, ts,
                                  body, {}),
        person_id=person,
    )
    return str(mid)


async def _memo(engine: Any, unit: str, room: str, title: str, cites: list[str]) -> str:
    memo = await SqlMemoStore(engine).create(
        unit=unit, platform="signal", conversation_id=room, title=title,
        body="## Kept\n- " + title + " [" + cites[0] + "]", citations=tuple(cites),
        mode="told", established_by="cognition:" + unit, model="m", now_ms=NOW - 1000,
    )
    return str(memo.id)


async def _node(engine: Any, kind: str, sid: str, text: str = "", **scope: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_NODE).values(
            kind=kind, source_id=sid, text=text, at_ms=NOW - 7_200_000, **scope,
        ))


async def _edge(engine: Any, src: tuple[str, str], rel: str, dst: tuple[str, str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_EDGE).values(
            src_kind=src[0], src_id=src[1], rel=rel, dst_kind=dst[0], dst_id=dst[1],
            provenance="test",
        ))


@pytest.fixture(params=["dense", "lexical", "fresh"])
def seeds(request: Any) -> str:
    return str(request.param)


def _settled(seeds: str) -> str:
    """What a complete index answers with hits: `ok`, or, on the lexical
    fallback, `partial`."""
    return "partial" if seeds == "lexical" else "ok"


async def _embed_all(engine: Any) -> None:
    """Every embeddable node without a vector gets its text's toy vector
    under `MODEL`, as the worker would."""
    async with engine.begin() as conn:
        rows = (await conn.execute(
            sa.select(MEMORY_NODE.c.id, MEMORY_NODE.c.text).where(
                MEMORY_NODE.c.embedding.is_(None), MEMORY_NODE.c.kind.in_(EMBEDDED_KINDS),
                MEMORY_NODE.c.text != "",
            )
        )).all()
        for r in rows:
            await conn.execute(sa.update(MEMORY_NODE).where(MEMORY_NODE.c.id == r.id).values(
                embedding=bag_of_words(r.text), embedding_model=MODEL,
            ))


async def _anchor(engine: Any) -> None:
    """The one vector in the FRESH run: a unit note sharing no word with any
    question here, so its cosine is 0, under the floor, and it is never a
    hit. It makes the search dense; every node a test wrote waits."""
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_NODE).values(
            kind="note", source_id="anchor", text="zzyzx", at_ms=NOW - 7_200_000,
            unit="u1", embedding=bag_of_words("zzyzx"), embedding_model=MODEL,
        ).on_conflict_do_nothing())


async def _search(
    engine: Any, query: str, *, seeds: str, room: RoomFacts = _B,
    person: str | None = None, metrics: Metrics | None = None, embed: bool = True,
    target: RenderRoom | None = None,
) -> MemorySearchResult:
    if seeds in ("dense", "fresh"):
        if seeds == "fresh":
            if embed:
                await _anchor(engine)
        elif embed:
            await _embed_all(engine)
        search = MemorySearch(SqlMemoryIndex(engine), metrics or Metrics(),
                              embedder=FakeQueryEmbedder(), model=MODEL, drain_seconds=60.0)
    else:
        search = MemorySearch(SqlMemoryIndex(engine), metrics or Metrics())
    return await search.search(
        query=query, person_id=person, target=target or _target(room), depth="low",
        now_ms=NOW, social_off=(),
    )


def _keys(result: MemorySearchResult) -> set[tuple[str, str]]:
    return {h.node.key for h in result.hits}


def _paths(result: MemorySearchResult) -> dict[tuple[str, str], list[tuple[Any, ...]]]:
    return {
        h.node.key: [(s.kind, s.rel, s.outgoing) for s in h.path] for h in result.hits
    }


# --- (a) (b) (c): the unit is the boundary, on every node of the path ---------


async def test_a_another_unit_two_hops_away_is_unreachable(engine: Any, seeds: str) -> None:
    seed = await _say(engine, "b", "ramen crawl tonight", "kit")
    target = await _say(engine, "d", "the noodle shop owner said hello", "sam")
    bridge = await _memo(engine, "u2", "d", "night out", [seed, target])
    found = await _search(engine, seeds=seeds, query="ramen crawl")
    assert ("message", seed) in _keys(found)  # positive control: the seed is a hit
    assert ("memo", bridge) not in _keys(found)
    assert ("message", target) not in _keys(found)


async def test_b_a_same_unit_path_through_another_unit_is_cut(engine: Any, seeds: str) -> None:
    seed = await _say(engine, "b", "ramen crawl tonight", "kit")
    target = await _say(engine, "b2", "bring cash for the stall", "rin")
    bridge = await _memo(engine, "u2", "d", "night out", [seed, target])
    found = await _search(engine, seeds=seeds, query="ramen crawl")
    assert ("message", seed) in _keys(found)
    assert ("memo", bridge) not in _keys(found)
    # In scope itself, but its only path crosses `u2`: cut at the bridge.
    assert ("message", target) not in _keys(found)


async def test_c_an_in_unit_target_is_reached_with_its_path(engine: Any, seeds: str) -> None:
    seed = await _say(engine, "b", "ramen crawl tonight", "kit")
    target = await _say(engine, "b2", "bring cash for the stall", "rin")
    memo = await _memo(engine, "u1", "b", "night out", [seed, target])
    found = await _search(engine, seeds=seeds, query="ramen crawl")
    paths = _paths(found)
    assert paths[("memo", memo)] == [("message", None, True), ("memo", "cites", False)]
    assert paths[("message", target)] == [
        ("message", None, True), ("memo", "cites", False), ("message", "cites", True),
    ]
    assert found.status == _settled(seeds)


# --- (d) (e): hubs are not bridges; the listed-room rule ----------------------


async def test_d_two_messages_by_kit_do_not_reach_each_other_through_kit(
    engine: Any, seeds: str,
) -> None:
    first = await _say(engine, "b", "ramen crawl tonight", "kit")
    second = await _say(engine, "b", "my landlord raised the rent", "kit")
    found = await _search(engine, seeds=seeds, query="ramen crawl")
    hit = {h.node.key: h for h in found.hits}
    assert ("message", first) in hit
    assert hit[("message", first)].people == {"kit"}  # the hub is its neighbour
    assert ("message", second) not in hit
    assert all(not h.node.is_hub for h in found.hits)


async def test_e_talk_about_kit_where_kit_is_not_listed_is_not_followed_to_kit(
    engine: Any, seeds: str,
) -> None:
    elsewhere = await _say(engine, "c", "farewell gift for the leaver", "sam")
    here = await _say(engine, "b", "farewell drinks friday", "sam")
    await _node(engine, "person", "kit")  # the hub a `mentions` projector writes
    await _edge(engine, ("message", elsewhere), "mentions", ("person", "kit"))
    await _edge(engine, ("message", here), "mentions", ("person", "kit"))
    found = await _search(engine, seeds=seeds, query="farewell")
    hit = {h.node.key: h for h in found.hits}
    assert "kit" not in hit[("message", elsewhere)].people
    assert "kit" in hit[("message", here)].people  # positive control


# --- (f) (g) (h) (i) (j): what is never a hit ---------------------------------


async def test_f_a_hidden_message_is_never_a_hit(engine: Any, seeds: str) -> None:
    gone = await _say(engine, "b", "ramen crawl secret spot", "kit")
    kept = await _say(engine, "b", "ramen crawl again", "rin")
    memo = await _memo(engine, "u1", "b", "crawl plans", [gone])
    await SqlRecordStore(engine).hide_message(message_id=int(gone), now_ms=NOW)
    found = await _search(engine, seeds=seeds, query="ramen crawl")
    assert ("message", kept) in _keys(found)
    assert ("message", gone) not in _keys(found)
    # The memo citing it is found by its own words; the edge dangles.
    found = await _search(engine, seeds=seeds, query="crawl plans")
    assert ("memo", memo) in _keys(found)
    assert ("message", gone) not in _keys(found)


async def test_g_a_stay_only_where_its_person_is_listed_and_presence_passes(
    engine: Any, seeds: str,
) -> None:
    # A stay is never embedded (lexical only): under dense seeds it
    # is reached as Kit's `about` seed, under lexical ones by its text too.
    stay = "kit|2026-09-01T10:00:00+08:00/2026-09-01T12:00:00+08:00"
    await _node(
        engine, "stay", stay, "stay 2026-09-01T10:00:00+08:00 to 2026-09-01T12:00:00+08:00",
        persons=["kit"], lineage=[["presence", "kit", None]],
    )
    await _edge(engine, ("stay", stay), "about", ("person", "kit"))

    async def kinds(room: RoomFacts) -> set[str]:
        found = await _search(engine, seeds=seeds, query="stay", room=room, person="kit")
        return {k for k, _ in _keys(found)}

    assert await kinds(_B) == {"stay"}
    assert not await kinds(_B2)  # location off
    assert not await kinds(_C)   # Kit not listed


async def test_h_a_person_account_is_absent_where_the_person_is_not_listed(
    engine: Any, seeds: str,
) -> None:
    await _node(engine, "character", "u1#kit", "loves spicy ramen", unit="u1",
                persons=["kit"])
    found = await _search(engine, seeds=seeds, query="spicy ramen")
    assert ("character", "u1#kit") in _keys(found)
    assert not _keys(await _search(engine, seeds=seeds, query="spicy ramen", room=_C))


async def test_i_an_unscoped_place_hub_never_seeds(engine: Any, seeds: str) -> None:
    await _node(engine, "place", "venue-1", "Golden Ramen House")
    await _node(engine, "event", "ev-1", "Golden Ramen House dinner", unit="u1")
    await _edge(engine, ("event", "ev-1"), "about", ("place", "venue-1"))
    found = await _search(engine, seeds=seeds, query="golden ramen house")
    assert _keys(found) == {("event", "ev-1")}
    assert found.hits[0].places == {"Golden Ramen House"}  # reached as a neighbour


async def test_m_near_a_named_district_reaches_only_what_in_scope_lies_in_it(
    engine: Any, seeds: str,
) -> None:
    """Step 3a (25 §6a): «near Mong Kok» names a district hub; the walk
    descends to the venue in it and reaches the event there and the outing
    located in the district, though no word of the question is in either.
    Each passes the scope like any node: another unit's event at the same
    venue is never reached, and the outing only where its person passes
    presence: B lists both and shares location, B2 lists both and does not.
    No place is returned."""
    mk = ("place", "district:旺角 / Mong Kok")
    await _node(engine, *mk, "旺角 / Mong Kok")
    await _node(engine, "place", "venue-1", "Langham Place")
    await _edge(engine, ("place", "venue-1"), "located_in", mk)
    await _node(engine, "event", "ev-in", "friday plan", unit="u1")
    await _node(engine, "event", "ev-out", "friday plan", unit="u2")
    for ev in ("ev-in", "ev-out"):
        await _edge(engine, ("event", ev), "about", ("place", "venue-1"))
    outing = "u1|kit,rin|2026-09-01T19:00:00+08:00/2026-09-01T22:00:00+08:00"
    await _node(engine, "outing", outing, "outing", unit="u1", persons=["kit", "rin"],
                lineage=[["presence", "kit", None], ["presence", "rin", None]])
    await _edge(engine, ("outing", outing), "located_in", mk)

    found = await _search(engine, seeds=seeds, query="anything good near Mong Kok?")
    assert _keys(found) == {("event", "ev-in"), ("outing", outing)}
    assert found.status == _settled(seeds)
    assert _paths(found)[("event", "ev-in")] == [
        ("place", None, True), ("place", "located_in", False), ("event", "about", False),
    ]
    assert {h.node.key: h.places for h in found.hits}[("outing", outing)] == {
        "旺角 / Mong Kok",
    }
    b2 = _keys(await _search(engine, seeds=seeds, query="near Mong Kok", room=_B2, embed=False))
    assert b2 == {("event", "ev-in")}


async def test_j_exclude_persons_is_respected(engine: Any, seeds: str) -> None:
    await _node(engine, "note", "n-1", "surprise party plan", unit="u1",
                exclude_persons=["sam"])
    # Sam is listed in B, not in B2.
    assert not _keys(await _search(engine, seeds=seeds, query="surprise party"))
    found = await _search(engine, seeds=seeds, query="surprise party", room=_B2)
    assert ("note", "n-1") in _keys(found)


async def test_a_named_person_seeds_only_where_this_room_lists_them(
    engine: Any, seeds: str,
) -> None:
    await _node(engine, "event", "ev-2", "a quiet evening", unit="u1")
    await _edge(engine, ("event", "ev-2"), "about", ("person", "kit"))
    await _node(engine, "person", "kit")
    found = await _search(engine, seeds=seeds, query="", person="kit")
    assert ("event", "ev-2") in _keys(found)
    assert not _keys(await _search(engine, seeds=seeds, query="", room=_C, person="kit"))


# --- (l): a social item's tagged place is gated by presence -------------------


async def _post(engine: Any, *, caption: str, location: str, alt: str) -> None:
    """Kit's post, written THROUGH the store, so the index holds what the
    live projection wrote."""
    taken = datetime.fromtimestamp((NOW - 7_200_000) / 1000, tz=UTC)
    assert await SqlSocialStreamStore(engine).insert_item(
        SocialItem(
            network="instagram", item_id="9001", account="10000001", kind="post",
            taken_at=taken, expires_at=None, caption=caption, alt_text=alt,
            media=(MediaItem("image", "https://example.invalid/x"),),
        ),
        person="kit", origin="poll", seen_at=taken, location=location,
    )


def _rendered(result: MemorySearchResult) -> str:
    items, _ = render_packet(
        result.hits, name_of=lambda p: p, room_label=lambda r: r,
        now=datetime.fromtimestamp(NOW / 1000, tz=UTC),
    )
    return json.dumps(items, ensure_ascii=False)


#: B lists Kit and has `location` on, but a person outside the
#: location-sharing circle is in it now: the DisclosureGuard denies.
_B_STRANGER = RenderRoom(
    room=_B, rooms=_ROOMS, circles=_CIRCLES,
    live=LiveRoom(person_ids=frozenset({"kit", "sam", "rin", "stranger"})),
)


async def test_l_a_tagged_place_matches_and_renders_where_presence_passes(
    engine: Any, seeds: str,
) -> None:
    await _post(engine, caption="picnic plans", location="Lantern Cove",
                alt="two cups at Lantern Cove")
    found = await _search(engine, seeds=seeds, query="lantern cove")
    place = ("social_location", "instagram:9001")
    assert place in _keys(found)
    assert ("social", "instagram:9001") in _keys(found), "the place reaches its item"
    assert '"kind": "Instagram tagged place", "age"' in _rendered(found)
    assert "Lantern Cove" in _rendered(found)


async def test_l_a_tagged_place_neither_matches_nor_renders_where_presence_fails(
    engine: Any, seeds: str,
) -> None:
    """B2 has `location` off; in B a stranger is present, which the guard
    denies. The item is still found by its caption in both, and its place is
    neither a hit, a neighbour, nor a word of the rendered answer; nor is the
    alt text, which names the place too."""
    await _post(engine, caption="picnic plans", location="Lantern Cove",
                alt="two cups at Lantern Cove")
    for where in ({"room": _B2}, {"target": _B_STRANGER}):
        by_place = await _search(engine, seeds=seeds, query="lantern cove", **where)
        assert not [k for k in _keys(by_place) if k[0] == "social_location"]
        assert "Lantern" not in _rendered(by_place)
        by_caption = await _search(engine, seeds=seeds, query="picnic plans", **where)
        assert ("social", "instagram:9001") in _keys(by_caption)  # positive control
        assert not [k for k in _keys(by_caption) if k[0] == "social_location"]
        assert "Lantern" not in _rendered(by_caption)


# --- (k): partial and unavailable ----------------------------------------------


async def test_k_partial_while_the_index_is_incomplete_or_queued(
    engine: Any, seeds: str,
) -> None:
    await _say(engine, "b", "ramen crawl tonight", "kit")
    assert (await _search(engine, seeds=seeds, query="ramen")).status == _settled(seeds)
    # Only a complete index with a query vector says «nothing»; the lexical
    # fallback never does.
    empty = await _search(engine, seeds=seeds, query="nothing like this")
    assert empty.status == ("partial" if seeds == "lexical" else "nothing_found")
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="building"))
    assert (await _search(engine, seeds=seeds, query="ramen")).status == "partial"
    assert (await _search(engine, seeds=seeds, query="nothing like this")).status == "partial"
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="complete"))
        await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE).values(
            kind="message", source_id="1", last_error="X",
        ))
    assert (await _search(engine, seeds=seeds, query="nothing like this")).status == "partial"


async def test_k_unavailable_on_a_database_error(pg_admin: Any, seeds: str) -> None:
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/t_memory_scope_no_such_db"
    )
    eng = create_sql_engine(url)
    metrics = Metrics()
    try:
        found = await _search(eng, seeds=seeds, query="ramen", metrics=metrics, embed=False)
    finally:
        await eng.dispose()
    assert found.status == "unavailable"
    assert found.hits == ()
    assert metrics.get("nora_memory_search_total", status="unavailable") == 1
