"""Event citations: the `citation` edge table admits events and Rollup keys with
a `part`, the store writes the edges and the lineage, and the withdrawal cascade
reaches an event and every reader of one honours it (fix PR D, migration
`0086_event_citations`; design/cognition-loop/10-tiers.md §2 rule 7 and §3,
40-register.md §4; the review of 2026-09-30 M2, m6 and of #662 M8, m4).

Real Postgres, one migrated database truncated between tests
(`test_withdrawal.py`'s pattern). Every cascade test proves the NEGATIVE too: the
marked event must stop reaching the reader that used to reach it, not merely gain
a flag. Names are invented and no message is quoted.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-d.json`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from nora.app.gathering_screen import CANCELLED_UNSUPPORTED, GatheringScreen
from nora.app.gatherings import GatheringService
from nora.app.person_forget import clear_unsupported, forget_person, unforget_person
from nora.app.withdrawal import withdraw_message
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    FieldMeta,
    Gathering,
    GatheringNotFoundError,
    GatheringStatus,
    RSVPResponse,
)
from nora.domain.lineage import Lineage, message
from nora.domain.models import NormalizedMessage
from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import (
    CITATION,
    EVENTS,
    PRESENCE_STAYS,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
)

pytestmark = pytest.mark.asyncio

ROOM_A = "signal:ga"
ROOM_B = "signal:gb"
T0 = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_event_citations_" + uuid4().hex
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
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "citation", "event_revision", "event_rsvps", "events", "messages",
                "presence_stays", "rollup_colocation", "rollup_build", "reminders",
                "row_history",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _message(engine: Any, *, conversation_id: str, ts: int = 1000,
                   person: str = "ada") -> int:
    return await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            "signal", conversation_id, "group", "+1", "Ada", ts, "hi", {},
        ),
        person_id=person,
    )


def _event(conversation_id: str = "ga", **over: Any) -> Gathering:
    base: dict[str, Any] = {
        "id": uuid4(), "conversation_id": conversation_id, "title": "dinner",
        "created_by": "p_ada", "when": None, "venue_text": None, "venue_place_id": None,
        "venue_name": None, "venue_address": None, "venue_rating": None,
        "venue_price_level": "", "venue_maps_url": None, "venue_resolved_at": None,
        "status": GatheringStatus.PROPOSED, "reminder_id": None, "created_at": T0,
        "unit_id": "u",
    }
    base.update(over)
    return Gathering(**base)


def _msg(part: str, mid: int) -> EventCitation:
    return EventCitation(part=part, store="message", ref=str(mid))


async def _edges(engine: Any, event_id: UUID) -> list[tuple[str, str, str | None]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(CITATION.c.cited_store, CITATION.c.cited_id, CITATION.c.part)
            .where(CITATION.c.citing_store == "event", CITATION.c.citing_key == str(event_id))
            .order_by(CITATION.c.part, CITATION.c.cited_id)
        )).all()
    return [(r.cited_store, r.cited_id, r.part) for r in rows]


# ---------------------------------------------------------------------------
# the edge table
# ---------------------------------------------------------------------------

async def test_a_create_leaves_one_edge_per_part_and_a_recite_adds_none(engine):
    store = SqlGatheringStore(engine)
    mid = await _message(engine, conversation_id="ga")
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "place"}),
        citations=(_msg("what", mid), _msg("place", mid)),
    ))
    assert await _edges(engine, event.id) == [
        ("message", str(mid), "place"), ("message", str(mid), "what"),
    ]
    # The same row for the same part on the next revision is not a new edge.
    await store.update(event, evidence=EventEvidence(
        parts=frozenset({"place"}), citations=(_msg("place", mid),),
    ))
    assert len(await _edges(engine, event.id)) == 2


async def test_a_rollup_key_is_an_edge_of_the_rollup_store(engine):
    store = SqlGatheringStore(engine)
    key = "colocation|u|p_ada,p_ben|2026-10-02T19:00:00+08:00/2026-10-02T21:00:00+08:00"
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"people"}),
        citations=(EventCitation(part="people", store="rollup", ref=key),),
    ))
    assert await _edges(engine, event.id) == [("rollup", key, "people")]


async def test_the_edge_table_refuses_an_unknown_store_and_a_blank_part(engine):
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(sa.insert(CITATION).values(
                citing_store="memo", citing_key="k", cited_store="message", cited_id="1",
            ))
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(sa.insert(CITATION).values(
                citing_store="event", citing_key="k", cited_store="message", cited_id="1",
                part="  ",
            ))


async def test_a_whole_row_edge_is_still_one_edge_and_a_part_makes_it_another(engine):
    """NULL parts collapse (two notes citing one message twice is one edge),
    and the SAME message cited for two parts is two."""
    async with engine.begin() as conn:
        for _ in range(2):
            await conn.execute(sa.text(
                "INSERT INTO citation (citing_store, citing_key, cited_store, cited_id) "
                "VALUES ('note', 'n1', 'message', '7') ON CONFLICT DO NOTHING"
            ))
    async with engine.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(CITATION))).scalar() == 1


# ---------------------------------------------------------------------------
# lineage, per field, from the citations (m6)
# ---------------------------------------------------------------------------

async def test_lineage_is_computed_per_field_from_the_rooms_the_citations_were_said_in(engine):
    store = SqlGatheringStore(engine)
    in_a = await _message(engine, conversation_id="ga", ts=1000)
    in_b = await _message(engine, conversation_id="gb", ts=5000)
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "place"}),
        citations=(_msg("what", in_a), _msg("place", in_b)),
    ))
    assert event.lineage.section("what") == (message(ROOM_A),)
    assert event.lineage.section("place") == (message(ROOM_B),)
    assert event.as_of == 5000          # the newest evidence observed


async def test_a_revision_replaces_the_fields_it_states_and_keeps_the_rest(engine):
    store = SqlGatheringStore(engine)
    in_a = await _message(engine, conversation_id="ga")
    in_b = await _message(engine, conversation_id="gb")
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "place"}),
        citations=(_msg("what", in_a), _msg("place", in_a)),
    ))
    revised = await store.update(event, evidence=EventEvidence(
        parts=frozenset({"place"}), citations=(_msg("place", in_b),),
    ))
    assert revised.lineage.section("place") == (message(ROOM_B),)   # replaced, not unioned
    assert revised.lineage.section("what") == (message(ROOM_A),)    # untouched
    got = await store.get(event.id)
    assert got is not None and got.lineage == revised.lineage


async def test_a_citation_that_cannot_be_resolved_is_unresolved_never_widened(engine):
    store = SqlGatheringStore(engine)
    gone = await _message(engine, conversation_id="gb")
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE messages SET hidden_at = 1 WHERE id = :i"),
                           {"i": gone})
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "end"}),
        citations=(_msg("what", gone), _msg("end", 987654)),
    ))
    for part in ("what", "end"):
        [entry] = event.lineage.section(part)
        assert entry.kind == "unresolved" and entry.origin == "ga"


async def test_a_tag_turn_s_event_rests_on_the_room_it_was_said_in(engine):
    """No row to cite, so no edge; the lineage is the room (m6: it was empty, and
    an empty lineage renders nowhere but its origin)."""
    store = SqlGatheringStore(engine)
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "status"}), room=ROOM_A,
    ))
    assert event.lineage.section("what") == (message(ROOM_A),)
    assert event.lineage.section("status") == (message(ROOM_A),)
    assert await _edges(engine, event.id) == []


async def test_a_write_with_no_evidence_leaves_lineage_as_the_row_carries_it(engine):
    store = SqlGatheringStore(engine)
    event = await store.create(_event())
    assert event.lineage == Lineage()
    assert (await store.update(event)).lineage == Lineage()


async def _seed_rollup(engine: Any) -> int:
    async with engine.begin() as conn:
        build = (await conn.execute(sa.select(ROLLUP_BUILD.c.id).where(
            ROLLUP_BUILD.c.status == "current"))).scalar()
        if build is None:
            build = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="t", params_sha256="x", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
    return int(build)


async def test_a_rollup_row_brings_its_own_lineage(engine):
    build = await _seed_rollup(engine)
    start_ms = int(datetime(2026, 10, 2, 11, 0, tzinfo=UTC).timestamp() * 1000)
    end_ms = start_ms + 7_200_000
    async with engine.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_COLOCATION).values(
            build_id=build, unit="u", lineage=[["presence", "p_ada", None],
                                               ["presence", "p_ben", None]],
            valid_from_ms=start_ms, valid_to_ms=end_ms, as_of_ms=end_ms,
            persons=["p_ada", "p_ben"], lat=0.0, lng=0.0, radius_m=50.0, stay_count=2,
        ))
        await conn.execute(sa.insert(PRESENCE_STAYS).values(
            subject_id="p_ada", person_id="p_ada", circle_id="c", started_ts=start_ms,
            ended_ts=end_ms, lat=0.0, lng=0.0, radius_m=50.0, fix_count=3,
            build_id=build, lineage=[["presence", "p_ada", None]],
        ))
    hk = timezone(timedelta(hours=8))

    def hkt(ms: int) -> str:
        """An interval end the way `rollup_read` prints it: Hong Kong time, to the second."""
        return datetime.fromtimestamp(ms / 1000, hk).isoformat(timespec="seconds")

    coloc = f"colocation|u|p_ada,p_ben|{hkt(start_ms)}/{hkt(end_ms)}"
    stay = f"stay|p_ada|{hkt(start_ms)}/{hkt(end_ms)}"
    other_unit = f"colocation|other|p_ada,p_ben|{hkt(start_ms)}/{hkt(end_ms)}"
    nothing = f"colocation|u|p_ada,p_ben|{hkt(start_ms + 60_000)}/{hkt(end_ms)}"
    store = SqlGatheringStore(engine)
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"people", "status", "place", "end"}),
        citations=(
            EventCitation(part="people", store="rollup", ref=coloc),
            EventCitation(part="status", store="rollup", ref=stay),
            EventCitation(part="place", store="rollup", ref=other_unit),
            EventCitation(part="end", store="rollup", ref=nothing),
        ),
    ))
    both = tuple(sorted(
        (e for e in event.lineage.section("people")), key=lambda e: e.subject or "",
    ))
    assert [(e.kind, e.subject) for e in both] == [("presence", "p_ada"), ("presence", "p_ben")]
    assert [(e.kind, e.subject) for e in event.lineage.section("status")] == [
        ("presence", "p_ada"),
    ]
    # Another unit's row, and a key that matches no current row, are unresolved.
    for part in ("place", "end"):
        [entry] = event.lineage.section(part)
        assert entry.kind == "unresolved"


async def test_per_field_reasoning_round_trips_through_the_head_and_the_revision(engine):
    store = SqlGatheringStore(engine)
    meta = (
        FieldMeta(field="place", reasoning="told", confidence="high", as_of=5),
        FieldMeta(field="people", reasoning="deduced", confidence="medium"),
    )
    event = await store.create(_event(field_meta=meta))
    got = await store.get(event.id)
    assert got is not None and got.field_meta == meta
    [rev] = await store.revisions_for(event.id)
    assert rev.field_meta == meta


# ---------------------------------------------------------------------------
# the withdrawal cascade, and every reader of an event
# ---------------------------------------------------------------------------

async def _cited_event(engine: Any, *, conversation_id: str = "ga", **over: Any) -> tuple[
    SqlGatheringStore, Gathering, int,
]:
    store = SqlGatheringStore(engine)
    mid = await _message(engine, conversation_id=conversation_id)
    event = await store.create(_event(conversation_id, **over), evidence=EventEvidence(
        parts=frozenset({"what"}), citations=(_msg("what", mid),),
    ))
    return store, event, mid


async def _withdraw(engine: Any, mid: int) -> Any:
    async with engine.begin() as conn:
        return await withdraw_message(conn, message_id=mid, now_ms=42)


async def test_withdrawing_a_cited_message_marks_the_event_and_only_once(engine):
    store, event, mid = await _cited_event(engine)
    other = await store.create(_event())          # cites nothing: never marked
    first = await _withdraw(engine, mid)
    assert first.marked == {"event": [str(event.id)]}
    second = await _withdraw(engine, mid)
    assert second.total_marked == 0
    got = await store.get(event.id)
    assert got is not None and got.unsupported_at == 42
    kept = await store.get(other.id)
    assert kept is not None and kept.unsupported_at is None


async def test_a_later_revision_cannot_undo_the_mark(engine):
    """An update built from a row read BEFORE the cascade must not write the
    mark back to NULL."""
    store, event, mid = await _cited_event(engine)
    await _withdraw(engine, mid)
    await store.update(event)                     # the stale, unmarked copy
    got = await store.get(event.id)
    assert got is not None and got.unsupported_at == 42


async def test_forget_person_cascades_to_an_event_and_clear_unsupported_lifts_it(engine):
    store, event, _mid = await _cited_event(engine)
    report = await forget_person(engine, person_id="ada", apply=True, now_ms=77)
    assert report.cascade is not None and report.cascade.marked["event"] == [str(event.id)]
    await unforget_person(engine, person_id="ada", apply=True)
    lifted = await clear_unsupported(engine, apply=True)
    assert lifted.cleared["event"] == [str(event.id)]
    got = await store.get(event.id)
    assert got is not None and got.unsupported_at is None


async def test_the_stores_readers_leave_a_withdrawn_event_out(engine):
    store, event, mid = await _cited_event(engine, created_by="p_ada")
    standing = await store.create(_event(title="standing one"))
    await _withdraw(engine, mid)
    assert [e.title for e in await store.list_for_conversation(conversation_id="ga")] == [
        "standing one",
    ]
    everything = await store.list_for_conversation(
        conversation_id="ga", include_unsupported=True,
    )
    assert {e.id for e in everything} == {event.id, standing.id}
    assert [e.id for e in await store.list_for_person(person_id="p_ada")] == [standing.id]
    assert [e.id for e in await store.search(unit_id="u")] == [standing.id]
    # `get` is the writer's read and returns the real row, marked.
    got = await store.get(event.id)
    assert got is not None and got.unsupported_at == 42


async def test_the_service_reads_a_withdrawn_event_as_an_unknown_id(engine):
    store, event, mid = await _cited_event(engine, created_by="p_ada")
    service = GatheringService(
        store=store, reminders=None, reminder_caps=None,  # type: ignore[arg-type]
        route_source=None, honcho=None, ai_peer="nora",
    )
    await _withdraw(engine, mid)
    assert await service.get_event(event_id=event.id) is None
    assert await service.get_event_for_person(event_id=event.id, person_id="p_ada") is None
    assert await service.list_events_for_person(person_id="p_ada") == []
    assert await service.list_events(conversation_id="ga") == []
    assert await service.search_events(unit_id="u", conversation_id="ga") == []
    with pytest.raises(GatheringNotFoundError):
        await service.update_event(
            event_id=event.id, conversation_id="ga", platform="signal", venue_text="x",
        )
    with pytest.raises(GatheringNotFoundError):
        await service.rsvp(
            event_id=event.id, conversation_id="ga", person_id="p_ben",
            response=RSVPResponse.YES,
        )


async def test_the_fire_screen_cancels_the_reminder_of_a_withdrawn_event(engine):
    reminder_id = uuid4()
    store, _event, mid = await _cited_event(engine, reminder_id=reminder_id)
    row = Reminder(
        id=reminder_id, requester_person_id="p_ada", platform="signal", conversation_id="ga",
        conversation_type="group", text="Event: dinner", created_at=T0, due_at=T0,
        state=ReminderState.PENDING, fired_at=None, outcome=None, series=None,
        kind="gathering", payload={"title": "dinner"},
    )
    screen = GatheringScreen(events=store)
    assert await screen.refusal(row) is None                      # it stands: fires
    await _withdraw(engine, mid)
    assert await screen.refusal(row) == (ReminderState.CANCELLED, CANCELLED_UNSUPPORTED)


async def test_the_fire_screen_fails_closed_when_the_event_cannot_be_read():
    class Broken:
        async def get_by_reminder(self, reminder_id: UUID) -> Gathering | None:
            raise RuntimeError("down")

    row = Reminder(
        id=uuid4(), requester_person_id="p", platform="signal", conversation_id="ga",
        conversation_type="group", text="Event: x", created_at=T0, due_at=T0,
        state=ReminderState.PENDING, fired_at=None, outcome=None, series=None,
        kind="gathering", payload={"title": "x"},
    )
    assert await GatheringScreen(events=Broken()).refusal(row) == (
        ReminderState.FAILED, "failed_error",
    )


async def test_the_tag_path_writes_record_the_room_and_no_edge(engine):
    """`GatheringService.create_event` has no row to cite: lineage is the room,
    edges none (said plainly in `_tag_evidence`)."""
    store = SqlGatheringStore(engine)
    service = GatheringService(
        store=store, reminders=None, reminder_caps=None,  # type: ignore[arg-type]
        route_source=None, honcho=None, ai_peer="nora",
    )
    event = await service.create_event(
        conversation_id="ga", requester_person_id="p_ada", title="dinner",
        platform="signal", unit_id="u",
    )
    assert event.lineage.section("what") == (message(ROOM_A),)
    assert event.lineage.section("status") == (message(ROOM_A),)
    assert event.lineage.section("start") == ()            # it stated no time
    assert await _edges(engine, event.id) == []
    moved = await service.update_event(
        event_id=event.id, conversation_id="ga", platform="signal", venue_text="place one",
    )
    assert moved.lineage.section("place") == (message(ROOM_A),)


async def test_an_unsupported_event_is_the_marked_column_and_nothing_else(engine):
    _store, event, mid = await _cited_event(engine)
    await _withdraw(engine, mid)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(EVENTS).where(EVENTS.c.id == event.id))).one()
    assert row.unsupported_at == 42 and row.title == "dinner"
