"""A platform delete or unsend withdraws the message (design/cognition-loop/
10-tiers.md §2 rule 7; 70-migration.md §2 row 2c; `nora.app.delete_withdrawal`).

Before this, all four platform parsers emitted the delete, the touch stage wrote a
`message_delete` row, and NOTHING hid the message or marked what cites it: it
rendered in every transcript, and every note, Standing, culture head and event
that cited it rendered too (measured live: two deletes on record, both targets
still visible). `withdraw_message` had no caller.

Real Postgres, one migrated database truncated between tests (`test_withdrawal.py`'s
pattern), driven through the REAL `IngestPipeline` so the claim is about an event
arriving, not about a function being callable. One shape per platform, because the
platforms name the target differently: Signal by the author's sent-timestamp,
WhatsApp, Discord and Instagram by a platform handle, and Discord and Instagram
name nobody as the deleter. Every case beside a control (a sibling message, a
sibling's derived rows, another room's, a second room that reuses the same
platform handle) because a withdrawal that hides too much is as wrong as one that
hides too little. Names, handles and bodies are invented.

Each behaviour has a test that goes red when it is reverted; the PR names them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import sqlalchemy as sa

import nora.app.delete_withdrawal as delete_withdrawal
from nora.app.adoption import backfill as adoption_backfill
from nora.app.delete_withdrawal import (
    SqlDeleteWithdrawer,
    reconcile_deletes,
    render_reconcile,
)
from nora.app.person_forget import unforget_person
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    Gathering,
    GatheringStatus,
)
from nora.domain.media import MediaBlob
from nora.domain.models import DeliveryStatus, EditTarget, NormalizedMessage, PersonRef, Touch
from nora.domain.record_query import RecordQuery
from nora.domain.standing import Standing
from nora.domain.withdrawal import render_supported
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.sql.schema import (
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGES,
    NOTE,
)
from nora.infra.sql.standing import SqlStandingStore
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.language.culture_render import read_culture
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy

pytestmark = pytest.mark.asyncio

T0 = int(datetime(2026, 10, 2, 11, 0, tzinfo=UTC).timestamp() * 1000)
NORA = "+8529999"


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_delete_withdraws_" + uuid4().hex
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
                "citation", "event_revision", "event_rsvps", "events", "note_revision",
                "note", "standing_revision", "standing", "culture_revision", "culture",
                "messages", "row_history",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


# --- the four platform shapes ------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """How one platform names a deleted message, as its parser emits it."""

    platform: str
    room: str
    author: str
    #: Who the delete names as acting: Signal's envelope sender IS the author,
    #: WhatsApp names the sender, Discord and Instagram name nobody.
    deleter: str
    #: Signal names the target by the author's sent-timestamp, the rest by handle.
    by_ts: bool

    def target(self, ts: int, handle: str) -> EditTarget:
        return EditTarget(sent_ts=ts) if self.by_ts else EditTarget(message_id=handle)

    def said(self, ts: int, body: str, handle: str, *, room: str | None = None,
             sender: str | None = None) -> NormalizedMessage:
        return NormalizedMessage(
            self.platform, room or self.room, "group", sender or self.author, "Ada", ts,
            body, {}, message_id="" if self.by_ts else handle,
        )

    def deletion(self, ts: int, handle: str, *, room: str | None = None,
                 sender: str | None = None, at: int = 0) -> NormalizedMessage:
        return NormalizedMessage(
            self.platform, room or self.room, "group",
            self.deleter if sender is None else sender, None, at, "", {},
            touch=Touch(kind="delete", target=self.target(ts, handle)),
        )

    def edit(self, ts: int, body: str, handle: str, *, at: int) -> NormalizedMessage:
        return NormalizedMessage(
            self.platform, self.room, "group", self.author, "Ada", at, body, {},
            edit_of=self.target(ts, handle),
        )


SHAPES = [
    Shape("signal", "sg", "+1", "+1", by_ts=True),
    Shape("whatsapp", "wg", "w1", "w1", by_ts=False),
    Shape("discord", "123/456", "d1", "", by_ts=False),
    Shape("instagram", "ig-room", "i1", "", by_ts=False),
]
by_platform = pytest.mark.parametrize("shape", SHAPES, ids=[s.platform for s in SHAPES])


class _Memory:
    """A `MemoryStore` that only counts what it was fed."""

    def __init__(self) -> None:
        self.fed: list[str] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, **_route: str | None) -> None:
        return None

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                   metadata: dict | None = None) -> bool:
        self.fed.append(text)
        return True

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        return []

    async def aclose(self) -> None:
        return None


class World:
    """The real pipeline over real Postgres, with a mapped author on every platform."""

    def __init__(self, engine: Any, *, withdrawer: Any = "sql") -> None:
        self.engine = engine
        self.audit = SqlRecordStore(engine)
        self.metrics = Metrics()
        self.memory = _Memory()
        self._tasks: list[asyncio.Task[None]] = []
        persons = Persons(
            by_person={"ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")},
            index={(s.platform, s.author): "ada" for s in SHAPES},
        )
        self.persons = persons
        self.pipeline = IngestPipeline(
            audit=self.audit, memory=self.memory,  # type: ignore[arg-type]
            persons=persons, policy=TriggerTextPolicy("nora.000"),
            allowlist=permissive_policy(), metrics=self.metrics, spawn=self._spawn,
            delete_withdrawer=SqlDeleteWithdrawer(engine) if withdrawer == "sql" else withdrawer,
        )

    def _spawn(self, coro: Any) -> None:
        self._tasks.append(asyncio.get_running_loop().create_task(coro))

    async def run(self, message: NormalizedMessage) -> Any:
        result = await self.pipeline.run(message=message)
        await self.settle()
        return result

    async def settle(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)
            self._tasks.clear()

    async def row(self, room: str, ts: int) -> int:
        async with self.engine.connect() as conn:
            return int((await conn.execute(
                sa.select(MESSAGES.c.id).where(
                    MESSAGES.c.conversation_id == room, MESSAGES.c.ts == ts,
                )
            )).scalar_one())

    async def hidden_at(self, mid: int) -> int | None:
        async with self.engine.connect() as conn:
            return (await conn.execute(
                sa.select(MESSAGES.c.hidden_at).where(MESSAGES.c.id == mid)
            )).scalar_one()

    async def visible(self, room: str) -> list[int]:
        return [r.id for r in await self.audit.recent(conversation_id=room, limit=50)]

    def count(self, outcome: str, platform: str) -> float:
        return self.metrics.get(
            "nora_delete_withdrawals_total", platform=platform, outcome=outcome,
        )


async def _note(engine: Any, *, cites: int, title: str, room: str, platform: str) -> str:
    note = await SqlNoteStore(engine).create(
        unit="u", title=title, description="d", uncertainty="open",
        closing_condition="c", established_in=f"{platform}:{room}",
        body=f"a claim {title} [{cites}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )
    return str(note.id)


async def _note_unsupported(engine: Any, note_id: str) -> int | None:
    async with engine.connect() as conn:
        return (await conn.execute(
            sa.select(NOTE.c.unsupported_at).where(NOTE.c.id == note_id)
        )).scalar_one()


def _event(room: str, title: str) -> Gathering:
    return Gathering(
        id=uuid4(), conversation_id=room, title=title, created_by="p_ada", when=None,
        venue_text=None, venue_place_id=None, venue_name=None, venue_address=None,
        venue_rating=None, venue_price_level="", venue_maps_url=None,
        venue_resolved_at=None, status=GatheringStatus.PROPOSED, reminder_id=None,
        created_at=datetime(2026, 10, 2, 11, 0, tzinfo=UTC), unit_id="u",
    )


async def _cited_event(engine: Any, room: str, title: str, mid: int) -> UUID:
    event = await SqlGatheringStore(engine).create(_event(room, title), evidence=EventEvidence(
        parts=frozenset({"what"}),
        citations=(EventCitation(part="what", store="message", ref=str(mid)),),
    ))
    return event.id


@dataclass
class Scene:
    """A room with a target `m`, a sibling `s` said before it, derived rows citing
    each, and a control room `o` whose derived rows cite its own message."""

    shape: Shape
    m: int
    s: int
    o: int
    note_m: str
    note_s: str
    note_o: str
    event_m: UUID
    event_s: UUID


M_TS, S_TS, O_TS = T0 + 2000, T0 + 1000, T0 + 3000


async def _scene(w: World, shape: Shape, *, edited: bool = False) -> Scene:
    other = shape.room + "-other"
    await w.run(shape.said(S_TS, "sibling words", "H-S"))
    await w.run(shape.said(M_TS, "the words to be unsent", "H-M"))
    if edited:
        # A message with an edit: the transcript shows the NEWEST text, so the
        # hide has to cover the row the edit hangs off.
        await w.run(shape.edit(M_TS, "the words, edited", "H-M", at=M_TS + 500))
    await w.run(shape.said(O_TS, "another room", "H-O", room=other))
    m, s, o = await w.row(shape.room, M_TS), await w.row(shape.room, S_TS), await w.row(other, O_TS)
    e = w.engine
    scene = Scene(
        shape=shape, m=m, s=s, o=o,
        note_m=await _note(e, cites=m, title="about-m", room=shape.room, platform=shape.platform),
        note_s=await _note(e, cites=s, title="about-s", room=shape.room, platform=shape.platform),
        note_o=await _note(e, cites=o, title="about-o", room=other, platform=shape.platform),
        event_m=await _cited_event(e, shape.room, "event on m", m),
        event_s=await _cited_event(e, shape.room, "event on s", s),
    )
    # The room's Standing and culture head cite its newest message (m), and the
    # other room's cite o.
    for room, mid in ((shape.room, m), (other, o)):
        await SqlStandingStore(e).write(standing=Standing(
            platform=shape.platform, conversation_id=room, body="a position",
            fold_count=1, updated_at=T0, last_folded_row_id=mid,
        ))
        await SqlCultureStore(e).append(
            key=room, body="an account", written_at=T0, established_by="fold",
        )
    return scene


# =============================================================================
# the defect, end to end, once per platform shape
# =============================================================================


@by_platform
async def test_a_delete_hides_the_message_and_withdraws_everything_derived_from_it(
    engine, shape,
):
    w = World(engine)
    sc = await _scene(w, shape, edited=True)
    other = shape.room + "-other"
    fed_before = list(w.memory.fed)
    assert fed_before, "positive control: the messages reached memory when said"
    assert sc.m in await w.visible(shape.room)

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    # Hidden, exactly that row; the sibling and the other room are untouched.
    assert await w.hidden_at(sc.m) is not None
    assert await w.hidden_at(sc.s) is None and await w.hidden_at(sc.o) is None
    assert await w.visible(shape.room) == [sc.s]
    assert await w.visible(other) == [sc.o]
    assert w.count("hidden", shape.platform) == 1
    # The delete is still on record, beside the row it names.
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MESSAGE_DELETE.c.message_id))).scalars().all()
    assert rows == [sc.m]

    # Derived rows citing it are unsupported, in every store; controls are not.
    assert await _note_unsupported(engine, sc.note_m) is not None
    assert await _note_unsupported(engine, sc.note_s) is None
    assert await _note_unsupported(engine, sc.note_o) is None
    events = SqlGatheringStore(engine)
    assert {e.title for e in await events.list_for_conversation(conversation_id=shape.room)} == {
        "event on s",
    }
    assert render_supported(await SqlStandingStore(engine).read(
        platform=shape.platform, conversation_id=shape.room)) is None
    assert render_supported(await SqlStandingStore(engine).read(
        platform=shape.platform, conversation_id=other)) is not None
    assert await read_culture(
        SqlCultureStore(engine).head(key=shape.room), stage="t", platform=shape.platform,
        metrics=None) is None
    assert await read_culture(
        SqlCultureStore(engine).head(key=other), stage="t", platform=shape.platform,
        metrics=None) is not None
    # …and the readers that feed a prompt leave the note out.
    notes = SqlNoteStore(engine)
    assert {n.id for n in await notes.standing(unit="u")} == {sc.note_s, sc.note_o}
    assert {n.id for n, _ in await notes.search(unit="u", query="claim", limit=10)} == {
        sc.note_s, sc.note_o,
    }

    # Nothing re-fed it, and nothing was fed because of the delete.
    assert w.memory.fed == fed_before


@by_platform
async def test_no_transcript_read_returns_a_deleted_message(engine, shape):
    """Every read that a prompt block, a tool result or the Cognition grounding
    is built from: one select, so one hide covers them, and this holds the list."""
    w = World(engine)
    sc = await _scene(w, shape, edited=True)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    room = shape.room
    audit = w.audit

    assert [r.id for r in await audit.recent(conversation_id=room, limit=50)] == [sc.s]
    assert [r.id for r in await audit.recent_before(
        conversation_id=room, before_ts=T0 + 99_000, limit=50)] == [sc.s]
    assert [r.id for r in await audit.since(conversation_id=room, after_id=0, limit=50)] == [sc.s]
    assert [r.id for r in await audit.since_including_nora(
        conversation_id=room, after_id=0, limit=50)] == [sc.s]

    query = SqlRecordQuery(engine)
    q = RecordQuery(rooms=((shape.platform, room),), start_ms=T0, end_ms=T0 + 99_000)
    hits, _, _ = await query.query(q)
    assert [h.row.id for h in hits] == [sc.s]
    # Not by its first words, and not by its edited ones.
    for needle in ("unsent", "edited"):
        found, _, _ = await query.query(RecordQuery(
            rooms=((shape.platform, room),), start_ms=T0, end_ms=T0 + 99_000, text=needle,
        ))
        assert found == ()
    # The Cognition grounding names the message by ref: it resolves to nothing.
    refs = await query.by_refs([("messages", sc.m), ("messages", sc.s)], [(shape.platform, room)])
    assert [r.hit.row.id for r in refs] == [sc.s]
    assert await query.own_sends([(shape.platform, room)], start_ms=T0, end_ms=T0 + 99_000,
                                 limit=10) == ()


async def test_a_deleted_messages_media_is_not_a_photo_in_the_room_any_more(engine):
    """`media_search`'s room scope and the media join both start from message rows,
    so a withdrawn row takes its readings with it. The control is a second photo."""
    shape = SHAPES[1]
    w = World(engine)
    blob = MediaBlob(sha256="a" * 64, media_type="image/jpeg", size=1)
    keep = MediaBlob(sha256="b" * 64, media_type="image/jpeg", size=1)
    for ts, handle, held in ((T0 + 1, "P-1", blob), (T0 + 2, "P-2", keep)):
        await w.audit.append(
            message=NormalizedMessage(
                shape.platform, shape.room, "group", shape.author, "Ada", ts,
                f"[image {held.sha256[:6]}]", {}, message_id=handle, media_blobs=(held,),
            ),
            person_id="ada",
        )
    got = await w.audit.media_rows(conversation_id=shape.room, since_ts=0, limit=10)
    assert len(got) == 2

    await w.run(shape.deletion(T0 + 1, "P-1", at=T0 + 9))

    got = await w.audit.media_rows(conversation_id=shape.room, since_ts=0, limit=10)
    assert [r.body for r in got] == [f"[image {keep.sha256[:6]}]"]


# =============================================================================
# idempotency, and what must not happen
# =============================================================================


@by_platform
async def test_a_second_delete_of_the_same_message_changes_nothing(engine, shape):
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    hid = await w.hidden_at(sc.m)
    marked = await _note_unsupported(engine, sc.note_m)

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9100))      # redelivered

    assert await w.hidden_at(sc.m) == hid, "the original hide time is kept"
    assert await _note_unsupported(engine, sc.note_m) == marked
    assert (w.count("hidden", shape.platform), w.count("already_hidden", shape.platform)) == (1, 1)
    assert await w.visible(shape.room) == [sc.s]
    assert await _note_unsupported(engine, sc.note_s) is None


@by_platform
async def test_a_delete_of_a_message_never_stored_changes_nothing(engine, shape):
    w = World(engine)
    sc = await _scene(w, shape)

    await w.run(shape.deletion(T0 + 123_456, "H-NEVER", at=T0 + 9000))

    for mid in (sc.m, sc.s, sc.o):
        assert await w.hidden_at(mid) is None
    assert await _note_unsupported(engine, sc.note_m) is None
    assert w.count("not_stored", shape.platform) == 1
    assert w.count("hidden", shape.platform) == 0
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one() == 0


@by_platform
async def test_the_same_handle_in_another_room_is_not_withdrawn(engine, shape):
    """A platform handle (or a Signal timestamp) is only unique per room: the
    delete names a room, and the other room's message with the same handle stays."""
    w = World(engine)
    sc = await _scene(w, shape)
    twin_room = shape.room + "-twin"
    await w.run(shape.said(M_TS, "same handle, another room", "H-M", room=twin_room))
    twin = await w.row(twin_room, M_TS)

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is not None
    assert await w.hidden_at(twin) is None
    assert await w.visible(twin_room) == [twin]


@by_platform
async def test_an_edit_after_the_delete_does_not_bring_the_message_back(engine, shape):
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    hid = await w.hidden_at(sc.m)
    fed = list(w.memory.fed)

    result = await w.run(shape.edit(M_TS, "second thoughts, still here", "H-M", at=T0 + 9500))

    assert result.message is None and result.decision is None, "no turn opens on it"
    assert await w.hidden_at(sc.m) == hid
    assert await w.visible(shape.room) == [sc.s]
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_EDIT)
            .where(MESSAGE_EDIT.c.message_id == sc.m))).scalar_one() == 0
    assert w.memory.fed == fed, "an edit of a withdrawn message feeds memory nothing"
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="edit_orphaned") == 1


async def test_a_delete_that_overtakes_its_message_withdraws_it_when_it_lands(engine):
    """Discord dispatches each event as its own task and a message with an
    attachment is recorded only after its custody fetch: the delete can arrive
    first. It finds nothing (`not_stored`), is held, and the message is hidden the
    moment it is recorded, before anything reads or feeds it."""
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9000))
    assert w.count("not_stored", shape.platform) == 1

    result = await w.run(shape.said(M_TS, "unsent before it was stored", "H-EARLY"))

    mid = await w.row(shape.room, M_TS)
    assert await w.hidden_at(mid) is not None
    assert result.message is None and result.decision is None
    assert w.memory.fed == [], "never fed to memory"
    assert await w.visible(shape.room) == []
    assert w.count("hidden", shape.platform) == 1


async def test_a_held_delete_does_not_withdraw_a_different_message(engine):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9000))
    await w.run(shape.said(M_TS + 1, "an unrelated message", "H-OTHER"))
    await w.run(shape.said(M_TS + 2, "same handle, other room", "H-EARLY", room="elsewhere"))
    for room, ts in ((shape.room, M_TS + 1), ("elsewhere", M_TS + 2)):
        assert await w.hidden_at(await w.row(room, ts)) is None


async def test_a_held_signal_delete_from_someone_else_does_not_withdraw_the_late_message(engine):
    """The held delete goes through the touch stage's own author check on replay:
    on Signal only the author can delete, so a stranger's delete naming the same
    timestamp must not hide the message that then lands."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "", sender="+7777", at=T0 + 9000))

    result = await w.run(shape.said(M_TS, "an ordinary message", "H-M"))

    assert await w.hidden_at(await w.row(shape.room, M_TS)) is None
    assert result.message is not None, "handled as an ordinary message"


# =============================================================================
# authorship, and Nora's own sends
# =============================================================================


async def test_a_signal_delete_naming_someone_elses_message_withdraws_nothing(engine):
    """Only an author can remote-delete on Signal, so a delete from another sender
    is a malformed or forged event: refused, as it always was, and nothing hides."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.said(M_TS, "someone elses words", "H-M"))
    mid = await w.row(shape.room, M_TS)

    await w.run(shape.deletion(M_TS, "H-M", sender="+7777", at=T0 + 9000))

    assert await w.hidden_at(mid) is None
    assert w.count("hidden", shape.platform) == 0
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform="signal", conversation_type="group",
        outcome="delete_refused") == 1


async def test_nora_unsending_her_own_signal_message_withdraws_it_and_what_cites_it(engine):
    """Own-send semantics: her row is `is_nora`, `person_id` NULL and carries no
    platform handle, so Signal's timestamp is the only way a delete reaches it. A
    message gone from the room is withdrawn like any other; a note that cites it
    goes unsupported, and Cognition's `own_sends` no longer lists it."""
    shape = SHAPES[0]
    w = World(engine)
    own_ts = T0 + 500
    own = await w.audit.append(
        message=NormalizedMessage("signal", shape.room, "group", NORA, None, own_ts,
                                  "i said this", {}),
        is_nora=True,
    )
    await w.audit.set_delivery_outcome(row_id=own, status=DeliveryStatus.SENT, ts=own_ts)
    note = await _note(engine, cites=own, title="about-her-line", room=shape.room,
                       platform="signal")
    query = SqlRecordQuery(engine)
    assert len(await query.own_sends([("signal", shape.room)], start_ms=T0, end_ms=T0 + 99_000,
                                     limit=5)) == 1

    await w.run(shape.deletion(own_ts, "", sender=NORA, at=T0 + 9000))

    assert await w.hidden_at(own) is not None
    assert await _note_unsupported(engine, note) is not None
    assert await query.own_sends([("signal", shape.room)], start_ms=T0, end_ms=T0 + 99_000,
                                 limit=5) == ()


# =============================================================================
# the transaction, and what the failure leaves
# =============================================================================


async def test_a_cascade_that_fails_rolls_the_hide_back_and_the_reconcile_finishes_it(
    engine, monkeypatch,
):
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("cascade down")

    with monkeypatch.context() as patch:
        patch.setattr(delete_withdrawal, "withdraw_message", boom)
        await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    # One fact: no hide without its cascade. The message is still visible, the
    # failure is counted, and the delete record is written so it can be found.
    assert await w.hidden_at(sc.m) is None
    assert await _note_unsupported(engine, sc.note_m) is None
    assert w.count("cascade_failed", shape.platform) == 1
    assert sc.m in await w.visible(shape.room)

    report = await reconcile_deletes(engine, apply=True, now_ms=T0 + 20_000)
    assert (report.visible, report.hidden, report.failed) == (1, 1, 0)
    assert await w.hidden_at(sc.m) == T0 + 20_000
    assert await _note_unsupported(engine, sc.note_m) == T0 + 20_000


async def test_a_withdrawer_that_raises_is_counted_and_never_stops_the_ingest(engine):
    shape = SHAPES[1]

    class Broken:
        async def withdraw(self, *, message_id: int, now_ms: int) -> Any:
            raise RuntimeError("down")

    w = World(engine, withdrawer=Broken())
    await w.run(shape.said(M_TS, "words", "H-M"))
    mid = await w.row(shape.room, M_TS)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    assert await w.hidden_at(mid) is None
    assert w.count("cascade_failed", shape.platform) == 1
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one() == 1


async def test_a_pipeline_with_no_withdrawer_records_the_delete_and_hides_nothing(engine):
    """The offline and eval compositions: behaviour as before, and the reason the
    composition root wires one unconditionally."""
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    await w.run(shape.said(M_TS, "words", "H-M"))
    mid = await w.row(shape.room, M_TS)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    assert await w.hidden_at(mid) is None


# =============================================================================
# Honcho: nothing re-feeds a hidden message
# =============================================================================


async def test_adoption_never_replays_a_deleted_message_into_memory(engine):
    """`RecordStore.unattributed` feeds Honcho the rows of a sender who has just
    been mapped. A message its author deleted before then must not be among them:
    this is the one replay that did not read through `transcript_select`."""
    shape = SHAPES[1]
    w = World(engine)
    stranger = "w-unmapped"
    await w.run(shape.said(T0 + 1, "kept words", "U-1", sender=stranger))
    await w.run(shape.said(T0 + 2, "deleted words", "U-2", sender=stranger))
    await w.run(shape.deletion(T0 + 2, "U-2", sender=stranger, at=T0 + 9))
    assert await w.hidden_at(await w.row(shape.room, T0 + 2)) is not None

    unattributed = await w.audit.unattributed(limit=50)
    assert [r.body for r in unattributed] == ["kept words"]

    adopted = Persons(
        by_person={"ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")},
        index={(shape.platform, stranger): "ada"},
    )
    memory = _Memory()
    counts = await adoption_backfill(
        audit=w.audit, memory=memory, persons=adopted,  # type: ignore[arg-type]
        metrics=w.metrics, start_delay_seconds=0,
    )
    assert memory.fed == ["kept words"]
    assert counts.fed == 1


async def test_a_replay_of_a_rooms_transcript_into_memory_skips_it(engine):
    """`nora memory backfill` reads `RecordStore.since`; the ordinary transcript
    select, so a hidden row is not in what it replays."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    replayed = [r.id for r in await w.audit.since(conversation_id=shape.room, after_id=0,
                                                  limit=100)]
    assert sc.m not in replayed and sc.s in replayed


# =============================================================================
# the reconcile: what already exists
# =============================================================================


async def _legacy_delete(w: World, shape: Shape, ts: int, handle: str) -> None:
    """A delete recorded the way the old touch stage did: the record, and nothing else."""
    target = await w.audit.find_edited(
        conversation_id=shape.room, target=shape.target(ts, handle),
        sender_id=shape.author,
    )
    assert target is not None
    await w.audit.append_delete(
        message_id=target.id, message=shape.deletion(ts, handle, at=T0 + 8000),
        person_id="ada",
    )


async def test_the_reconcile_lists_in_a_dry_run_and_fixes_on_apply(engine):
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)           # the code as it was: records, withdraws nothing
    sc = await _scene(w, shape)
    await _legacy_delete(w, shape, M_TS, "H-M")
    assert await w.hidden_at(sc.m) is None

    dry = await reconcile_deletes(engine, apply=False, now_ms=T0 + 50_000)
    assert (dry.visible, dry.hidden, dry.failed, dry.applied) == (1, 1, 0, False)
    assert dry.marked == {"note": 1, "standing": 1, "culture": 1, "event": 1}
    assert await w.hidden_at(sc.m) is None, "a dry run writes nothing"
    assert await _note_unsupported(engine, sc.note_m) is None
    text = "\n".join(render_reconcile(dry))
    assert "dry run" in text and "would hide 1 message(s)" in text
    assert "H-M" not in text and "words" not in text, "counts only"

    applied = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)
    assert (applied.visible, applied.hidden) == (1, 1)
    assert applied.marked == dry.marked, "the dry run reports exactly what apply wrote"
    assert await w.hidden_at(sc.m) == T0 + 50_000
    assert await w.hidden_at(sc.s) is None and await w.hidden_at(sc.o) is None
    assert await _note_unsupported(engine, sc.note_m) == T0 + 50_000
    assert await _note_unsupported(engine, sc.note_s) is None
    assert await _note_unsupported(engine, sc.note_o) is None
    assert await w.visible(shape.room) == [sc.s]

    again = await reconcile_deletes(engine, apply=True, now_ms=T0 + 60_000)
    assert (again.visible, again.hidden, again.total_marked) == (0, 0, 0)
    assert await w.hidden_at(sc.m) == T0 + 50_000


async def test_the_reconcile_leaves_a_message_that_was_never_deleted_alone(engine):
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    report = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)
    assert (report.visible, report.hidden) == (0, 0)
    for mid in (sc.m, sc.s, sc.o):
        assert await w.hidden_at(mid) is None


async def test_unforget_brings_a_deleted_message_back_and_the_reconcile_hides_it_again(engine):
    """`nora person unforget` un-hides EVERY hidden message of a person, one a
    delete hid included (not changed here). The delete record is still there, so
    the reconcile restores the withdrawal."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    await unforget_person(engine, person_id="ada", apply=True)
    assert sc.m in await w.visible(shape.room)

    report = await reconcile_deletes(engine, apply=True, now_ms=T0 + 70_000)

    assert (report.visible, report.hidden) == (1, 1)
    assert sc.m not in await w.visible(shape.room)


# =============================================================================
# the withdrawer on its own
# =============================================================================


async def test_two_withdrawals_racing_one_message_hide_it_once(engine):
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    withdrawer = SqlDeleteWithdrawer(engine)
    results = await asyncio.gather(
        withdrawer.withdraw(message_id=sc.m, now_ms=T0 + 1),
        withdrawer.withdraw(message_id=sc.m, now_ms=T0 + 2),
    )
    assert sorted(r.outcome for r in results) == ["already_hidden", "hidden"]
    assert await w.hidden_at(sc.m) in (T0 + 1, T0 + 2)
    assert (await withdrawer.withdraw(message_id=999_999, now_ms=T0)).outcome == "not_stored"

