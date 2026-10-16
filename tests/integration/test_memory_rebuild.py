"""`nora memory rebuild` equals live projection.

One database is seeded across every indexed kind THROUGH THE STORES, so the
index is what live writes projected: messages with an attachment, an edit and
a reading; a note through a revision and a supersession; a memo, a lesson, an
event with a venue and an RSVP; culture and Character accounts; and the
current Rollup build's outings, stays and trips (`test_rollup_builds`' seed,
whose Record rows are raw inserts and are projected here as ingestion would
have). A message is hidden after it was projected, cited by a memo and inside
an outing, so the live delete path is part of what a rebuild must reproduce.

The index is read as SETS of nodes and edges with their content and scope
(never the surrogate id or the write timestamps), so equality is the claim:
the same nodes, saying the same thing, at the same scope, joined the same way.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.culture import character_key, unit_culture_key
from nora.domain.gatherings import RSVP, Gathering, GatheringStatus, RSVPResponse
from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.domain.notes import NoteSupersedeReason
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_INDEX_STATE,
    MEMORY_NODE,
    MESSAGES,
)
from nora.subject.sandbox import sandbox_as_of
from tests.integration.test_rollup_builds import (
    _WATERMARK,
    ROOM,
    _runner,
    _seed_with_outing_talk,
)

pytestmark = pytest.mark.asyncio

_UNIT = "friends"
_SHA = "cd" * 32


async def _index(engine: Any) -> tuple[set[tuple], set[tuple]]:
    """Every node and edge by content: no surrogate id, no timestamp."""
    async with engine.connect() as conn:
        nodes = {
            (r.kind, r.source_id, r.unit, r.origin_room, tuple(r.persons),
             tuple(r.exclude_persons), repr(r.lineage), r.sha256, r.text, r.at_ms, r.state)
            for r in await conn.execute(sa.select(MEMORY_NODE))
        }
        edges = {
            (r.src_kind, r.src_id, r.rel, r.dst_kind, r.dst_id, r.provenance)
            for r in await conn.execute(sa.select(MEMORY_EDGE))
        }
    return nodes, edges


async def _truncate(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "TRUNCATE memory_node, memory_edge, memory_projection_queue"
        ))


async def _status(engine: Any) -> memory_index.IndexStatus:
    async with engine.connect() as conn:
        return await memory_index.index_status(conn)


def _message(body: str, *, ts: int, blobs: tuple[MediaBlob, ...] = ()) -> NormalizedMessage:
    return NormalizedMessage(
        "signal", "g1", "group", "+10000000001", "Member A", ts, body, {},
        media_blobs=blobs,
    )


async def _seed_everything(engine: Any) -> dict[str, Any]:
    """Every indexed kind, written by its own store. Returns the handles the
    tests assert on."""
    mobility = SqlMobilityStore(engine)
    talk = await _seed_with_outing_talk(engine, mobility)
    # The Rollup seed writes the Record raw; project it as ingestion would.
    async with engine.begin() as conn:
        for mid in (await conn.execute(sa.select(MESSAGES.c.id).order_by(MESSAGES.c.id))).scalars():
            await memory_index.project(conn, "message", mid)
    await _runner(engine).rebuild(through_ms=_WATERMARK)
    async with engine.connect() as conn:
        [outing] = (await conn.execute(
            sa.select(MEMORY_NODE.c.source_id).where(MEMORY_NODE.c.kind == "outing")
            .order_by(MEMORY_NODE.c.source_id).limit(1)
        )).scalars().all()

    record = SqlRecordStore(engine)
    blob = MediaBlob(sha256=_SHA, media_type="image/jpeg", size=10)
    shown = await record.append(message=_message("look at this", ts=1000, blobs=(blob,)),
                                person_id="member-a")
    said = await record.append(message=_message("dinner friday?", ts=1100),
                               person_id="member-b")
    await record.append_edit(message_id=said, message=_message("dinner saturday?", ts=1200))
    assert await SqlMediaObjectStore(engine).comprehend(
        sha256=_SHA, title="Menu", description="A noodle menu.", content="", model="m",
    ) is True

    notes = SqlNoteStore(engine)

    async def note(title: str) -> Any:
        return await notes.create(
            unit=_UNIT, title=title, description="open", uncertainty="not settled",
            closing_condition="someone confirms", established_in="g1", body=f"- {title} [1]",
            established_by="tag:member-a", now_ms=10, anchor_date=20261020,
            anchor_entity_ids=("Member B",),
        )

    first = await note("Saturday dinner")
    revision = await notes.append_revision(
        unit=_UNIT, note_id=first.id, body="- the noodle place [1]",
        parent_revision_id=first.head_revision_id, established_by="tag:member-a", now_ms=20,
    )
    successor = await note("Saturday dinner, booked")
    await notes.supersede(
        key=_UNIT, target=first.id, replaced_by=successor.id,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=30,
        expected_head=revision.id,
    )

    memo = await SqlMemoStore(engine).create(
        unit=_UNIT, platform=ROOM[0], conversation_id=ROOM[1], title="pub night",
        body="the usual place", citations=(str(talk), str(said), f"rollup(outing|{outing})"),
        mode="told", established_by="cognition", model=None, now_ms=40,
    )
    await SqlLessonStore(engine).create(
        unit=_UNIT, body="ask before booking", citations=(str(said),), written_at=50,
        established_by="lessons",
    )
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    gatherings = SqlGatheringStore(engine)
    event = await gatherings.create(Gathering(
        id=uuid4(), conversation_id="g1", title="Dinner", created_by="member-a",
        when=now + timedelta(days=3), venue_text="the noodle place", venue_place_id="place-1",
        venue_name="Noodle House", venue_address="1 Example Road", venue_rating=4.5,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=now,
        status=GatheringStatus.PROPOSED, reminder_id=None, created_at=now, unit_id=_UNIT,
        people=("member-a", "member-b"), exclude_persons=(),
    ))
    await gatherings.add_rsvp(RSVP(
        event_id=event.id, person_id="member-d", response=RSVPResponse.YES,
        responded_at=now,
    ))
    culture = SqlCultureStore(engine)
    for key in (unit_culture_key(_UNIT), character_key(_UNIT, "member-b"), "g1"):
        await culture.append(
            key=key, body=f"account of {key}", written_at=60, established_by="dreamer",
            citations=(str(said),),
        )

    # Hidden AFTER it was projected: it is inside the outing (a `during`
    # edge) and the memo cites it.
    async with engine.connect() as conn:
        assert (await conn.execute(sa.select(MEMORY_EDGE.c.dst_id).where(
            MEMORY_EDGE.c.rel == "during", MEMORY_EDGE.c.src_id == str(talk),
        ))).scalars().all(), "positive control: the hidden message had a `during` edge"
    assert await record.hide_message(message_id=talk, now_ms=9000) is True
    # The only message a person ever wrote, hidden: their hub has no edge left.
    lone = await record.append(message=_message("just once", ts=1300), person_id="member-z")
    assert await record.hide_message(message_id=lone, now_ms=9100) is True
    return {"talk": talk, "said": said, "shown": shown, "memo": memo, "outing": outing}


async def test_a_rebuild_equals_live_projection_and_is_idempotent(pg_url):
    engine = create_sql_engine(pg_url)
    try:
        seeded = await _seed_everything(engine)
        live_nodes, live_edges = await _index(engine)

        # Positive controls: every kind is in the live index, and the hide
        # left the memo's citation of the hidden message dangling, with no
        # node and no `during` edge from it.
        kinds = {n[0] for n in live_nodes}
        assert kinds >= {
            "message", "media", "note", "memo", "lesson", "event", "character",
            "outing", "stay", "trip", "person", "room", "nora", "place",
        }
        talk = str(seeded["talk"])
        memo = str(seeded["memo"].id)
        assert ("memo", memo, "cites", "message", talk, "memo_revision.citations") in live_edges
        assert ("message", talk) not in {(n[0], n[1]) for n in live_nodes}
        assert not any(e[0] == "message" and e[1] == talk for e in live_edges)
        # A hub no edge reaches is not in the index, live or rebuilt.
        assert ("person", "member-z") not in {(n[0], n[1]) for n in live_nodes}
        assert await _status(engine) == ("never", 0, 0)

        # Over the live index, from an empty one, and again: the same sets.
        first = await memory_index.rebuild(engine, batch=7)
        assert await _index(engine) == (live_nodes, live_edges)
        assert set(first.dropped.values()) == {0}
        await _truncate(engine)
        second = await memory_index.rebuild(engine, batch=7)
        assert await _index(engine) == (live_nodes, live_edges)
        third = await memory_index.rebuild(engine)
        assert await _index(engine) == (live_nodes, live_edges)
        assert first.projected == second.projected == third.projected
        assert second.projected["message"] == sum(1 for n in live_nodes if n[0] == "message")
        assert second.projected["outing"] == 2
        assert await _status(engine) == ("complete", 0, 0)
    finally:
        await engine.dispose()


async def test_the_final_pass_drops_what_a_batch_raced(pg_url):
    """A hide that lands while a batch holds the message, simulated by hiding
    it underneath the projector, and an episode node of no current build:
    the final pass removes both."""
    engine = create_sql_engine(pg_url)
    try:
        record = SqlRecordStore(engine)
        mid = await record.append(message=_message("gone soon", ts=1000), person_id="member-a")
        keep = await record.append(message=_message("stays", ts=1100), person_id="member-a")
        async with engine.begin() as conn:
            await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == mid).values(
                hidden_at=9000,
            ))
            await conn.execute(sa.insert(MEMORY_NODE).values(
                kind="outing", source_id="friends|a,b|x/y", unit=_UNIT, persons=["a", "b"],
                text="outing",
            ))
            await conn.execute(sa.insert(MEMORY_NODE).values(kind="person", source_id="nobody"))

        dry = await memory_index.rebuild(engine, dry_run=True)
        assert (dry.projected["message"], dry.dropped["message"], dry.dropped["outing"]) == (
            1, 1, 1,
        )
        assert await _status(engine) == ("never", 0, 0), "a dry run writes nothing"

        assert dry.dropped["person"] == 1
        report = await memory_index.rebuild(engine)
        assert (report.dropped["message"], report.dropped["outing"]) == (1, 1)
        assert report.dropped["person"] == 1
        held = {(n[0], n[1]) for n in (await _index(engine))[0]}
        assert ("message", str(mid)) not in held
        assert ("message", str(keep)) in held
        assert not any(k == "outing" for k, _ in held)
        assert ("person", "nobody") not in held
        assert ("person", "member-a") in held, "positive control: a hub with edges stays"
    finally:
        await engine.dispose()


async def test_status_runs_never_building_complete_and_counts_what_stayed_queued(
    pg_url, monkeypatch,
):
    engine = create_sql_engine(pg_url)
    try:
        await SqlRecordStore(engine).append(message=_message("hi", ts=1000), person_id="a")
        lesson = await SqlLessonStore(engine).create(
            unit=_UNIT, body="ask first", citations=("1",), written_at=5, established_by="lessons",
        )
        assert await _status(engine) == ("never", 0, 0)

        seen: list[memory_index.IndexStatus] = []

        async def broken(conn: Any, source_id: str) -> None:
            # Mid-rebuild, read by another connection.
            seen.append(await _status(engine))
            raise RuntimeError("projector down")

        monkeypatch.setitem(memory_index._PROJECTORS, "lesson", broken)
        report = await memory_index.rebuild(engine)
        # Twice: the batch, then the drain's one retry.
        assert [s.status for s in seen] == ["building", "building"]
        assert report.unprojected == 1
        assert await _status(engine) == ("complete", 1, 1)
        async with engine.connect() as conn:
            row = (await conn.execute(sa.select(MEMORY_INDEX_STATE))).one()
        assert row.started_at is not None and row.finished_at >= row.started_at

        monkeypatch.undo()
        await memory_index.rebuild(engine)
        assert await _status(engine) == ("complete", 0, 0)
        assert ("lesson", str(lesson.id)) in {(n[0], n[1]) for n in (await _index(engine))[0]}
    finally:
        await engine.dispose()


async def test_a_second_rebuild_is_refused_while_one_holds_the_lock(pg_url):
    engine = create_sql_engine(pg_url)
    try:
        await SqlRecordStore(engine).append(message=_message("hi", ts=1000), person_id="a")
        holder = await engine.connect()
        try:
            assert (await holder.execute(sa.select(
                sa.func.pg_try_advisory_lock(memory_index.REBUILD_LOCK_KEY)
            ))).scalar_one() is True
            await holder.commit()
            with pytest.raises(memory_index.RebuildRefusedError, match="advisory lock"):
                await memory_index.rebuild(engine)
            assert await _status(engine) == ("never", 0, 0), "refused before it wrote"
        finally:
            await holder.execute(sa.select(
                sa.func.pg_advisory_unlock(memory_index.REBUILD_LOCK_KEY)
            ))
            await holder.close()
        # Positive control: with the holder gone, it runs, and two at once
        # leave exactly one winner.
        results = await asyncio.gather(
            memory_index.rebuild(engine), memory_index.rebuild(engine),
            return_exceptions=True,
        )
        assert sum(isinstance(r, memory_index.RebuildReport) for r in results) >= 1
        assert all(
            isinstance(r, (memory_index.RebuildReport, memory_index.RebuildRefusedError))
            for r in results
        )
        assert (await _status(engine)).status == "complete"
    finally:
        await engine.dispose()


async def test_a_restored_sandbox_reads_never(pg_url, pg_template):
    engine = create_sql_engine(pg_url)
    try:
        await SqlRecordStore(engine).append(message=_message("hi", ts=1000), person_id="a")
        await memory_index.rebuild(engine)
        assert (await _status(engine)).status == "complete"
        async with engine.connect() as conn:
            at = (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()
    finally:
        await engine.dispose()

    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, report):
        assert report.copied["messages"] == 1  # positive control: it restored
        restored = create_sql_engine(url)
        try:
            assert await _status(restored) == ("never", 0, 0)
            assert await _index(restored) == (set(), set())
            # A database with no state row at all reads `never` too, and a
            # rebuild writes the row.
            async with restored.begin() as conn:
                await conn.execute(sa.delete(MEMORY_INDEX_STATE))
            assert await _status(restored) == ("never", 0, 0)
            await memory_index.rebuild(restored)
            assert await _status(restored) == ("complete", 0, 0)
            assert {n[0] for n in (await _index(restored))[0]} >= {"message", "room"}
        finally:
            await restored.dispose()


async def test_a_hub_being_attached_survives_a_concurrent_orphan_drop(pg_url):
    """Transaction A re-attributes a message to P while a hide of P's only other
    message commits. A holds P's hub (`_hub` locks it), so the hide's orphan drop
    must leave it; otherwise A would commit an `authored_by` edge to a missing
    hub."""
    engine = create_sql_engine(pg_url)
    try:
        record = SqlRecordStore(engine)
        m1 = await record.append(message=_message("only", ts=1000), person_id="P")
        m2 = await record.append(message=_message("other", ts=1100), person_id="Q")
        a = await engine.connect()
        try:
            await a.begin()
            await a.execute(sa.update(MESSAGES).where(MESSAGES.c.id == m2).values(person_id="P"))
            await memory_index.project(a, "message", m2)
            assert await asyncio.wait_for(record.hide_message(message_id=m1, now_ms=9000), 10)
            await a.commit()
        finally:
            await a.close()
        live = await _index(engine)
        held = {(n[0], n[1]) for n in live[0]}
        assert ("person", "P") in held
        assert ("person", "Q") not in held, "Q lost its only edge in A"
        await memory_index.rebuild(engine)
        assert await _index(engine) == live
    finally:
        await engine.dispose()


async def test_opposing_reattributions_do_not_deadlock(pg_url):
    """Two transactions move a message each between the same two people in
    opposite directions, at once. A projection holds the hub it attaches to
    and only SKIPS a hub another holds, so the one wait possible is on a hub
    the other already deleted, until that one commits: never a cycle (a
    Postgres deadlock would raise; a hang would time out)."""
    engine = create_sql_engine(pg_url)
    try:
        record = SqlRecordStore(engine)
        m1 = await record.append(message=_message("one", ts=1000), person_id="P")
        m2 = await record.append(message=_message("two", ts=1100), person_id="Q")
        both_open = asyncio.Barrier(2)

        async def move(mid: int, to: str) -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    sa.update(MESSAGES).where(MESSAGES.c.id == mid).values(person_id=to)
                )
                await both_open.wait()
                await memory_index.project(conn, "message", mid)

        await asyncio.wait_for(asyncio.gather(move(m1, "Q"), move(m2, "P")), 20)
        live = await _index(engine)
        held = {(n[0], n[1]) for n in live[0]}
        assert {("person", "P"), ("person", "Q")} <= held
        await memory_index.rebuild(engine)
        assert await _index(engine) == live
    finally:
        await engine.dispose()


async def test_a_person_known_only_through_a_stay_leaves_with_the_build(pg_url):
    """A build switch that drops the only episode about a person drops that
    person's hub too, live, as a rebuild from empty would."""
    from nora.infra.sql.rollups import SqlRollupStore
    from nora.infra.sql.schema import PRESENCE_STAYS, ROLLUP_BUILD

    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            old = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="t", params_sha256="", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
            new = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="t", params_sha256="", status="building",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
            await conn.execute(sa.insert(PRESENCE_STAYS).values(
                subject_id="solo", person_id="solo", circle_id="c1", started_ts=1_000_000,
                ended_ts=2_000_000, lat=0.0, lng=0.0, radius_m=10.0, fix_count=3,
                build_id=old, lineage=[],
            ))
            await memory_index.reproject_episodes(conn)
        assert ("person", "solo") in {(n[0], n[1]) for n in (await _index(engine))[0]}

        await SqlRollupStore(engine).switch_build(build_id=new)
        live = await _index(engine)
        assert live == (set(), set())
        await memory_index.rebuild(engine)
        assert await _index(engine) == live
    finally:
        await engine.dispose()
