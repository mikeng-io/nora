"""Event citations: the store computes the lineage of an event from what it cites.

Real Postgres, one migrated database truncated between tests
(`test_withdrawal.py`'s pattern). Names are invented and no message is quoted.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-d.json`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    FieldMeta,
    Gathering,
    GatheringStatus,
)
from nora.domain.lineage import Lineage, message
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import (
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
                "event_revision", "event_rsvps", "events", "messages",
                "presence_stays", "rollup_colocation", "rollup_build", "reminders",
                "note_revision", "note", "row_history",
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


# ---------------------------------------------------------------------------
# the edge table
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# lineage, per field, from the citations
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
    """No row to cite, so no edge; the lineage is the room (an empty lineage
    renders nowhere but its origin)."""
    store = SqlGatheringStore(engine)
    event = await store.create(_event(), evidence=EventEvidence(
        parts=frozenset({"what", "status"}), room=ROOM_A,
    ))
    assert event.lineage.section("what") == (message(ROOM_A),)
    assert event.lineage.section("status") == (message(ROOM_A),)


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
# the service paths
# ---------------------------------------------------------------------------

def _service(store: SqlGatheringStore) -> GatheringService:
    return GatheringService(
        store=store,
        route_source=None, honcho=None, ai_peer="nora",
    )


async def test_a_tag_turn_s_event_cites_the_message_that_raised_it_and_rests_on_its_room(engine):
    """The tag path cites what `Room.trigger_message_ids` names (code's own
    knowledge): the lineage is `message:<room>` (the message was said in this room)."""
    mid = await _message(engine, conversation_id="ga")
    service = _service(SqlGatheringStore(engine))
    event = await service.create_event(
        conversation_id="ga", requester_person_id="p_ada", title="dinner",
        platform="signal", unit_id="u", message_ids=(mid,),
    )
    assert event.lineage.section("what") == (message(ROOM_A),)
    assert event.lineage.section("status") == (message(ROOM_A),)
    assert event.lineage.section("start") == ()            # it stated no time
    moved = await service.update_event(
        event_id=event.id, conversation_id="ga", platform="signal", unit_id="u",
        venue_text="place one", message_ids=(mid,),
    )
    assert moved.lineage.section("place") == (message(ROOM_A),)


async def test_the_tag_path_s_new_citation_changes_no_lineage_so_no_lineage_mode_reads_a_difference(
    engine,
):
    """Event lineage is read by one reader, the cognition grounding, which applies
    it per `[lineage] mode` (`off` applies nothing; `shadow` reports; `on`
    removes). The tag path cites the message that raised the turn, which is
    said in the room the turn is in, so the section it adds is the one the turn
    itself always added: the lineage is the SAME VALUE with and without the
    citation, hence no mode reads a different answer."""
    mid = await _message(engine, conversation_id="ga")
    service = _service(SqlGatheringStore(engine))
    cited = await service.create_event(
        conversation_id="ga", requester_person_id="p_ada", title="dinner",
        platform="signal", unit_id="u", message_ids=(mid,), venue_text="place one",
    )
    bare = await service.create_event(
        conversation_id="ga", requester_person_id="p_ada", title="dinner 2",
        platform="signal", unit_id="u", venue_text="place one",
    )
    assert cited.lineage == bare.lineage
    assert cited.lineage.section("place") == (message(ROOM_A),)


async def test_a_note_citing_a_rollup_row_takes_the_rows_lineage(engine):
    """A note resting on a presence row brings the row's own entries into its
    lineage, not its messages alone; a key that resolves to nothing is
    unresolved, not widened."""
    from nora.infra.sql.notes import SqlNoteStore

    build = await _seed_rollup(engine)
    start_ms = int(datetime(2026, 10, 2, 11, 0, tzinfo=UTC).timestamp() * 1000)
    end_ms = start_ms + 7_200_000
    async with engine.begin() as conn:
        await conn.execute(sa.insert(PRESENCE_STAYS).values(
            subject_id="p_ada", person_id="p_ada", circle_id="c", started_ts=start_ms,
            ended_ts=end_ms, lat=0.0, lng=0.0, radius_m=50.0, fix_count=3,
            build_id=build, lineage=[["presence", "p_ada", None]],
        ))
    hk = timezone(timedelta(hours=8))

    def hkt(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, hk).isoformat(timespec="seconds")

    stay = f"stay|p_ada|{hkt(start_ms)}/{hkt(end_ms)}"
    gone = f"stay|p_ada|{hkt(start_ms + 60_000)}/{hkt(end_ms)}"
    mid = await _message(engine, conversation_id="ga")
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:ga",
        body=f"a claim [{mid}, rollup({stay})]", established_by="test", now_ms=1000,
        anchor_date=20261002,
    )
    async with engine.connect() as conn:
        head = (await conn.execute(sa.text(
            "SELECT lineage FROM note WHERE id = :i"), {"i": note.id})).scalar_one()
    section = Lineage.from_json(head).section("row")
    assert any(e.kind == "presence" and e.subject == "p_ada" for e in section)
    assert message(ROOM_A) in section
    # A key that names no current row is unresolved at the origin.
    second = await notes.create(
        unit="u", title="t2", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:ga",
        body=f"a claim [rollup({gone})]", established_by="test", now_ms=1000,
        anchor_date=20261002,
    )
    async with engine.connect() as conn:
        head2 = (await conn.execute(sa.text(
            "SELECT lineage FROM note WHERE id = :i"), {"i": second.id})).scalar_one()
    flat = {e.kind for e in Lineage.from_json(head2).section("row")}
    assert "unresolved" in flat and "presence" not in flat


