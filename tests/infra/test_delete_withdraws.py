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
import dataclasses
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
from nora.app.person_forget import (
    clear_unsupported,
    forget_person,
    render_unforget,
    unforget_person,
)
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    Gathering,
    GatheringStatus,
)
from nora.domain.media import MediaBlob, MediaRef
from nora.domain.models import DeliveryStatus, EditTarget, NormalizedMessage, PersonRef, Touch
from nora.domain.record_query import RecordQuery
from nora.domain.standing import Standing
from nora.domain.withdrawal import render_supported
from nora.engagement.debrief_render import render_rollup_input
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.documents import SqlDocumentStore
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
    NOTE_CANDIDATE,
)
from nora.infra.sql.standing import SqlStandingStore
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.language.culture_render import read_culture
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.policy import permissive_policy
from nora.testing import FakeBlobStore, FakeMediaInterpreter, FakeMediaObjectStore, FakeMediaSource

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
                "note_candidate", "messages", "row_history",
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


class _GatedInterpreter(FakeMediaInterpreter):
    """A vision model that can be held mid-read (`release` lets it answer)."""

    def __init__(self) -> None:
        super().__init__(text="a cartoon cat of the unsent kind", title="a cat")
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def describe(self, *, data: bytes, media_type: str, label: str = ""):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        return await super().describe(data=data, media_type=media_type, label=label)


class _GatedSource(FakeMediaSource):
    """A custody fetch that can be held mid-fetch, as a slow attachment is."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        super().__init__(blobs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def fetch(self, *, ref: MediaRef):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        return await super().fetch(ref=ref)


class World:
    """The real pipeline over real Postgres, with a mapped author on every platform."""

    def __init__(
        self, engine: Any, *, withdrawer: Any = "sql",
        retry_delays: tuple[float, ...] = (0.0,), media: bool = False,
    ) -> None:
        self.engine = engine
        self.audit = SqlRecordStore(engine)
        self.metrics = Metrics()
        self.memory = _Memory()
        self._tasks: list[asyncio.Task[None]] = []
        persons = Persons(
            by_person={"ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")},
            index={
                **{(s.platform, s.author): "ada" for s in SHAPES},
                # The same person under WhatsApp's other id form (phone and LID).
                ("whatsapp", "w1-lid"): "ada",
            },
        )
        self.persons = persons
        self.interpreter = _GatedInterpreter()
        self.source = _GatedSource({"att1": b"\xff\xd8\xff\xe0 pretend jpeg"})
        wiring: dict[str, Any] = {}
        if media:
            blobs = FakeBlobStore()
            store = FakeMediaObjectStore()
            wiring = {
                "media_sources": MediaSourceRegistry([self.source]), "blobs": blobs,
                "media_store": store,
                "comprehension": MediaComprehension(
                    blobs=blobs, store=store, interpreter=self.interpreter,
                    memory=self.memory,  # type: ignore[arg-type]
                    metrics=self.metrics, spawn=self._spawn,
                ),
            }
        self.pipeline = IngestPipeline(
            **wiring,
            audit=self.audit, memory=self.memory,  # type: ignore[arg-type]
            persons=persons, policy=TriggerTextPolicy("nora.000"),
            allowlist=permissive_policy(), metrics=self.metrics, spawn=self._spawn,
            delete_withdrawer=SqlDeleteWithdrawer(engine) if withdrawer == "sql" else withdrawer,
            withdrawal_retry_delays=retry_delays,
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


async def _candidate(engine: Any, title: str, mid: int, *, status: str = "pending") -> str:
    """A staged note proposal read off message `mid` (`note-candidates`)."""
    async with engine.begin() as conn:
        return str((await conn.execute(
            sa.insert(NOTE_CANDIDATE).values(
                unit="u", kind="create", title=title, description="d",
                body=f"a staged claim {title} [{mid}]", closing_condition="c",
                anchor_date=20260901, status=status, message_ids=[mid],
                first_seen_at=1, last_seen_at=1,
            ).returning(NOTE_CANDIDATE.c.id)
        )).scalar_one())


async def _candidates(engine: Any) -> set[str]:
    async with engine.connect() as conn:
        return {str(i) for i in (await conn.execute(sa.select(NOTE_CANDIDATE.c.id))).scalars()}


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
    cand_m: str
    cand_s: str


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
        cand_m=await _candidate(e, "cand-m", m),
        cand_s=await _candidate(e, "cand-s", s),
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

    # Rows that cite it as EVIDENCE are unsupported; controls are not.
    assert await _note_unsupported(engine, sc.note_m) is not None
    assert await _note_unsupported(engine, sc.note_s) is None
    assert await _note_unsupported(engine, sc.note_o) is None
    events = SqlGatheringStore(engine)
    assert {e.title for e in await events.list_for_conversation(conversation_id=shape.room)} == {
        "event on s",
    }
    # Standing and culture cite a WATERMARK, not evidence (the orchestrator's
    # decision): a delete does not mark them, in the room or anywhere.
    for room in (shape.room, other):
        assert render_supported(await SqlStandingStore(engine).read(
            platform=shape.platform, conversation_id=room)) is not None
        assert await read_culture(
            SqlCultureStore(engine).head(key=room), stage="t", platform=shape.platform,
            metrics=None) is not None
    # The staged proposal read off it is gone; the sibling's stays.
    assert await _candidates(engine) == {sc.cand_s}
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
    w = World(engine, retry_delays=())
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

    w = World(engine, withdrawer=Broken(), retry_delays=())
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
    assert dry.marked == {"note": 1, "event": 1, "note_candidate": 1}
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


async def test_unforget_leaves_a_deleted_message_hidden_and_the_marks_on_what_cited_it(engine):
    """`nora person unforget` un-hides what `forget` hid, and NOT a message a platform
    delete withdrew: its author took it back, which un-forgetting a person is not
    entitled to undo. `--clear-unsupported` then has nothing to clear for a row that
    cites it, because the message is still hidden."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    await forget_person(
        engine, documents=SqlDocumentStore(engine), person_id="ada", apply=True,
        now_ms=T0 + 10_000,
    )
    assert await w.visible(shape.room) == []

    dry = await unforget_person(engine, person_id="ada", apply=False)
    assert (dry.restored, dry.kept_deleted) == (2, 1)           # s and o; m was deleted
    done = await unforget_person(engine, person_id="ada", apply=True)
    assert done.kept_deleted == 1

    assert sc.m not in await w.visible(shape.room)
    assert sc.s in await w.visible(shape.room)
    cleared = await clear_unsupported(engine, apply=True)
    assert "note" not in cleared.cleared or sc.note_m not in cleared.cleared["note"]
    assert await _note_unsupported(engine, sc.note_m) is not None
    assert "1 message(s) stay hidden" in "\n".join(render_unforget(done))


async def test_the_reconcile_hides_a_deleted_message_a_stray_unhide_brought_back(engine):
    """Whatever un-hid it (an operator's SQL, a restore), the delete record is still
    there and the reconcile hides it again."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == sc.m).values(hidden_at=None))
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



# =============================================================================
# a forged delete: authorship on every platform that can be checked
# =============================================================================


@pytest.mark.parametrize("shape", SHAPES[:2], ids=["signal", "whatsapp"])
async def test_a_delete_from_someone_who_is_not_the_author_changes_nothing(engine, shape):
    """Only the author can delete on Signal and on WhatsApp. WhatsApp is end to end
    encrypted, so the server cannot refuse a forged REVOKE and the bridge forwards
    any: the event names its REVOKER (`sender_*`), and a revoker who is not the
    message's author, or the same person under another id, is refused. That covers
    a group admin removing a member's message, which cannot be told from a forgery
    without group metadata (the message stays visible)."""
    w = World(engine)
    sc = await _scene(w, shape)

    await w.run(shape.deletion(M_TS, "H-M", sender="stranger-2", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is None
    assert await w.visible(shape.room) == [sc.s, sc.m]
    assert await _note_unsupported(engine, sc.note_m) is None
    assert await _candidates(engine) == {sc.cand_m, sc.cand_s}
    assert w.count("refused_not_author", shape.platform) == 1
    assert w.count("hidden", shape.platform) == 0
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one() == 0, (
            "a refused delete leaves no record that could be replayed")

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9100))       # the author, now

    assert await w.hidden_at(sc.m) is not None


async def test_a_whatsapp_author_deleting_under_the_other_id_form_is_the_author(engine):
    """The same person is one WhatsApp account in two id forms (phone, LID): both
    resolve to one person in identity.toml, so the revoke is theirs."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)

    await w.run(shape.deletion(M_TS, "H-M", sender="w1-lid", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is not None
    assert w.count("refused_not_author", shape.platform) == 0


@pytest.mark.parametrize("shape", SHAPES[2:], ids=["discord", "instagram"])
async def test_a_delete_that_names_nobody_is_honoured_as_the_platform_reports_it(engine, shape):
    """Discord's gateway and Instagram's unsend name no deleter (`sender_id == ""`):
    a moderator's removal is a legitimate withdrawal from the room, and there is
    nothing to compare. Both events are authenticated by the platform, not by the
    sender, so this is the platform's word and is taken. (The parser sets the sender
    to "" on both, so a non-author case cannot be built on these two.)"""
    w = World(engine)
    sc = await _scene(w, shape)
    assert shape.deletion(M_TS, "H-M").sender_id == ""

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is not None
    assert w.count("refused_not_author", shape.platform) == 0


# =============================================================================
# Standing and culture are not marked by a delete; notes and events are
# =============================================================================


async def test_deleting_a_fold_boundary_message_leaves_standing_and_culture_standing(engine):
    """A Standing write cites its fold's LAST message and a culture write the room's
    newest: a watermark, not evidence. Citation edges pile up across revisions, so a
    delete of any message that was ever a boundary would hide the CURRENT head for
    good, and no later fold or `clear_unsupported` lifts it while the message stays
    hidden. A delete therefore withdraws the evidence stores only. What cites the
    boundary message AS EVIDENCE (a note, an event, a staged proposal) is withdrawn."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)                 # standing and culture cite `m`, the newest
    # A later fold wrote the CURRENT head from a later message; `m`'s edge is still there.
    later = await w.audit.append(
        message=shape.said(T0 + 5000, "said after the boundary", "H-L"), person_id="ada",
    )
    await SqlStandingStore(engine).write(standing=Standing(
        platform=shape.platform, conversation_id=shape.room, body="a newer position",
        fold_count=2, updated_at=T0 + 5000, last_folded_row_id=later,
    ))

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    standing = render_supported(await SqlStandingStore(engine).read(
        platform=shape.platform, conversation_id=shape.room))
    assert standing is not None and standing.body == "a newer position"
    assert await read_culture(
        SqlCultureStore(engine).head(key=shape.room), stage="t", platform=shape.platform,
        metrics=None) is not None
    assert await _note_unsupported(engine, sc.note_m) is not None
    assert await _candidates(engine) == {sc.cand_s}
    assert {e.title for e in await SqlGatheringStore(engine).list_for_conversation(
        conversation_id=shape.room)} == {"event on s"}


async def test_the_next_folds_input_does_not_hold_the_deleted_message(engine):
    """The real fold input builders: the batch the Standing fold reads
    (`since_including_nora` after its watermark) and the prompt text it renders
    (`render_rollup_input`), with the culture head the fold reads beside it. The
    deleted message's words are in neither; its neighbours' are."""
    shape = SHAPES[1]
    w = World(engine)
    boundary = await w.audit.append(
        message=shape.said(T0 + 100, "the boundary words", "H-B"), person_id="ada")
    await SqlStandingStore(engine).write(standing=Standing(
        platform=shape.platform, conversation_id=shape.room, body="the prior position",
        fold_count=1, updated_at=T0 + 100, last_folded_row_id=boundary,
    ))
    await SqlCultureStore(engine).append(
        key=shape.room, body="the prior account", written_at=T0 + 100, established_by="fold")
    await w.run(shape.said(T0 + 200, "keepable neighbour words", "H-1"))
    await w.run(shape.said(T0 + 300, "the unsent secret words", "H-2"))
    await w.run(shape.said(T0 + 400, "another neighbour words", "H-3"))

    await w.run(shape.deletion(T0 + 300, "H-2", at=T0 + 9000))

    prior = await SqlStandingStore(engine).read(
        platform=shape.platform, conversation_id=shape.room)
    assert prior is not None and prior.last_folded_row_id == boundary
    rows = await w.audit.since_including_nora(
        conversation_id=shape.room, after_id=prior.last_folded_row_id or 0, limit=50)
    rendered = render_rollup_input(
        prior=prior, rows=rows, display_name="Nora", now_ms=T0 + 10_000,
        culture=await read_culture(
            SqlCultureStore(engine).head(key=shape.room), stage="t",
            platform=shape.platform, metrics=None),
    )
    assert "unsent secret" not in rendered
    assert "keepable neighbour" in rendered and "another neighbour" in rendered
    assert "the prior position" in rendered and "the prior account" in rendered
    # `render_rollup_input`'s transcript line for the deleted message is absent too.
    assert [r.body for r in rows] == ["keepable neighbour words", "another neighbour words"]


# =============================================================================
# a row written AFTER the delete, and a second delete of a hidden row
# =============================================================================


async def test_a_note_and_an_event_written_after_the_delete_are_born_unsupported(engine):
    """The cascade runs once, when the message is withdrawn. A note a session writes
    afterwards, from a transcript it was handed before the delete, writes its edge
    with `unsupported_at` NULL and nothing revisits it, so the one place edges are
    written asks whether what they cite is hidden. Controls: a row citing a visible
    message is supported."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    late_note = await _note(engine, cites=sc.m, title="late-note", room=shape.room,
                            platform=shape.platform)
    ok_note = await _note(engine, cites=sc.s, title="ok-note", room=shape.room,
                          platform=shape.platform)
    late_event = await _cited_event(engine, shape.room, "late event", sc.m)
    ok_event = await _cited_event(engine, shape.room, "ok event", sc.s)

    assert await _note_unsupported(engine, late_note) is not None
    assert await _note_unsupported(engine, ok_note) is None
    events = SqlGatheringStore(engine)
    assert (await events.get(late_event)).unsupported_at is not None  # type: ignore[union-attr]
    assert (await events.get(ok_event)).unsupported_at is None  # type: ignore[union-attr]
    assert {n.id for n in await SqlNoteStore(engine).standing(unit="u")} >= {ok_note}
    assert late_note not in {n.id for n in await SqlNoteStore(engine).standing(unit="u")}

    # And a REVISION that cites it: the second place a note's edges are written.
    notes = SqlNoteStore(engine)
    head = (await notes.note_by_id(unit="u", note_id=ok_note)).head_revision_id  # type: ignore[union-attr]
    await notes.append_revision(
        unit="u", note_id=ok_note, parent_revision_id=head,
        body=f"now resting on the deleted line [{sc.m}]", established_by="test",
        now_ms=T0 + 20_000,
    )
    assert await _note_unsupported(engine, ok_note) == T0 + 20_000


async def test_a_redelivered_delete_cascades_again_over_a_row_the_first_one_did_not_reach(
    engine,
):
    """A delete event redelivered after something that cites the message was
    written (or after a mark was cleared) must still cascade: the second delete of
    an already hidden row is not a no-op."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    withdrawer = SqlDeleteWithdrawer(engine)
    assert (await withdrawer.withdraw(message_id=sc.m, now_ms=T0 + 1)).marked == 3
    async with engine.begin() as conn:       # the mark is cleared, as an operator might
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == sc.note_m)
                           .values(unsupported_at=None))
    sc_cand = await _candidate(engine, "cand-late", sc.m)

    again = await withdrawer.withdraw(message_id=sc.m, now_ms=T0 + 2)

    assert again.outcome == "already_hidden"
    assert again.marked == 2                                    # the note and the candidate
    assert await _note_unsupported(engine, sc.note_m) == T0 + 2
    assert sc_cand not in await _candidates(engine)


# =============================================================================
# the record first, then the hide; a retry; the start-up sweep
# =============================================================================


async def test_the_delete_is_recorded_before_the_hide_is_attempted(engine, monkeypatch):
    shape = SHAPES[1]
    seen: list[int] = []
    withdrawer = SqlDeleteWithdrawer(engine)
    real = withdrawer.withdraw

    async def spy(*, message_id: int, now_ms: int) -> Any:
        async with engine.connect() as conn:
            seen.append((await conn.execute(
                sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one())
        return await real(message_id=message_id, now_ms=now_ms)

    monkeypatch.setattr(withdrawer, "withdraw", spy)
    w = World(engine, withdrawer=withdrawer)
    await w.run(shape.said(M_TS, "words", "H-M"))
    mid = await w.row(shape.room, M_TS)

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert seen == [1], "the message_delete row was already committed when the hide began"
    assert await w.hidden_at(mid) is not None


async def test_a_record_that_cannot_be_written_does_not_stop_the_hide(engine, monkeypatch):
    """If even the record fails the platform event leaves no row, but the hide, which is
    what honours the delete, is still attempted."""
    shape = SHAPES[1]
    w = World(engine)
    await w.run(shape.said(M_TS, "words", "H-M"))
    mid = await w.row(shape.room, M_TS)

    async def boom(**_k: Any) -> int:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(w.audit, "append_delete", boom)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await w.hidden_at(mid) is not None
    assert w.metrics.get("nora_errors_total", stage="touch_record",
                         platform=shape.platform) == 1


async def test_a_withdrawal_that_failed_is_retried_and_then_counted_reconciled(
    engine, monkeypatch,
):
    shape = SHAPES[1]
    w = World(engine, retry_delays=(0.0, 0.0))
    sc = await _scene(w, shape)
    real = delete_withdrawal.withdraw_message
    calls = {"n": 0}

    async def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cascade down once")
        return await real(*a, **k)

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", flaky)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is not None
    assert await _note_unsupported(engine, sc.note_m) is not None
    assert w.count("cascade_failed", shape.platform) == 1
    assert w.count("reconciled", shape.platform) == 1
    assert calls["n"] == 2, "one failure, one retry that landed, no more attempts"


async def test_a_withdrawal_that_keeps_failing_is_tried_a_bounded_number_of_times(
    engine, monkeypatch,
):
    shape = SHAPES[1]
    w = World(engine, retry_delays=(0.0, 0.0))
    sc = await _scene(w, shape)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", boom)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await w.hidden_at(sc.m) is None
    assert w.count("cascade_failed", shape.platform) == 3       # the event and two retries
    assert w.count("reconciled", shape.platform) == 0


# =============================================================================
# the hold: keyed to the message it names
# =============================================================================


async def test_a_held_signal_delete_is_not_consumed_by_another_members_message_in_the_same_ms(
    engine,
):
    """Two members posting in one millisecond are two messages with one timestamp.
    The hold is the author's: the other member's message must not consume it, and
    the author's, when it lands, is the one withdrawn."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "", at=T0 + 9000))          # +1's delete, held

    other = await w.run(shape.said(M_TS, "someone else, same millisecond", "",
                                   sender="+7777"))
    assert other.message is not None, "an ordinary message from someone else"
    mine = await w.run(shape.said(M_TS, "the author's own words", ""))

    rows = {r.sender_id: r for r in await w.audit.recent(conversation_id=shape.room, limit=10)}
    assert list(rows) == ["+7777"], "the author's message is the one hidden"
    assert mine.message is None and mine.decision is None


async def test_a_held_delete_does_not_match_a_message_that_has_no_handle(engine):
    """Signal's delete names a timestamp and its hold carries an EMPTY handle; a
    message of the same author with no handle at a different timestamp must not match
    it on `"" == ""`."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "", at=T0 + 9000))

    result = await w.run(shape.said(M_TS + 5, "a different message, same author", ""))

    assert result.message is not None
    assert await w.hidden_at(await w.row(shape.room, M_TS + 5)) is None


async def test_a_held_delete_on_one_platform_does_not_withdraw_the_same_handle_on_another(
    engine,
):
    """The hold is per platform AND room: a Discord delete for handle H must not hide a
    WhatsApp message that carries H in a room with the same id."""
    discord, whatsapp = SHAPES[2], SHAPES[1]
    w = World(engine)
    await w.run(dataclasses.replace(
        discord.deletion(M_TS, "H-X", room="shared-room", at=T0 + 9000)))

    result = await w.run(whatsapp.said(M_TS, "same handle, other platform", "H-X",
                                       room="shared-room"))

    assert result.message is not None
    assert await w.hidden_at(await w.row("shared-room", M_TS)) is None


async def test_a_held_delete_that_ran_out_is_counted_hold_expired_not_not_stored(engine):
    shape = SHAPES[2]
    w = World(engine)
    now = [0.0]
    w.pipeline._pending_deletes = type(w.pipeline._pending_deletes)(
        ttl_seconds=10, clock=lambda: now[0], on_expired=w.pipeline._hold_expired)
    await w.run(shape.deletion(M_TS, "H-NEVER", at=T0 + 9000))
    assert w.count("not_stored", shape.platform) == 1
    now[0] = 11.0

    await w.run(shape.said(M_TS + 1, "an unrelated message", "H-OTHER"))

    assert w.count("hold_expired", shape.platform) == 1
    assert w.count("not_stored", shape.platform) == 1, "an expiry is not another not_stored"


# =============================================================================
# a photo deleted early is not read and not fed
# =============================================================================


def _photo(shape: Shape, ts: int, handle: str) -> NormalizedMessage:
    return dataclasses.replace(
        shape.said(ts, "[image]", handle),
        media=(MediaRef(platform_id="att1", media_type="image/jpeg", filename="a.jpg"),),
    )


async def test_a_photo_with_no_delete_is_read_and_fed_which_is_what_the_next_two_prove_absent(
    engine,
):
    """The control: the same photo, no delete, reaches the vision model and Honcho, so
    a test that asserts both absent is able to see them."""
    shape = SHAPES[0]
    w = World(engine, media=True)
    await w.run(_photo(shape, M_TS, ""))
    assert len(w.interpreter.calls) == 1
    assert any("unsent kind" in t for t in w.memory.fed)


async def test_a_photo_unsent_while_it_was_being_fetched_is_never_read_or_fed(engine):
    shape = SHAPES[0]
    w = World(engine, media=True)
    w.source.release.clear()
    task = asyncio.create_task(w.pipeline.run(message=_photo(shape, M_TS, "")))
    await w.source.entered.wait()
    await w.pipeline.run(message=shape.deletion(M_TS, "", at=T0 + 9000))  # mid-custody: held
    w.source.release.set()
    await task
    await w.settle()

    mid = await w.row(shape.room, M_TS)
    assert await w.hidden_at(mid) is not None
    assert w.interpreter.calls == [], "the vision model was never asked about it"
    assert w.memory.fed == [], "nothing of it, not even its marker, reached Honcho"


async def test_a_clip_unsent_while_it_was_being_read_is_not_fed_afterwards(engine):
    """The read is already in flight when the delete lands (a long clip is minutes of
    model calls): the job asks again just before the Honcho feed."""
    shape = SHAPES[0]
    w = World(engine, media=True)
    w.interpreter.release.clear()
    await w.pipeline.run(message=_photo(shape, M_TS, ""))
    await w.interpreter.entered.wait()

    # `pipeline.run`, not `w.run`: `run` settles every spawned task, which includes the
    # read this test is holding in flight.
    await w.pipeline.run(message=shape.deletion(M_TS, "", at=T0 + 9000))
    w.interpreter.release.set()
    await w.settle()

    assert len(w.interpreter.calls) == 1, "the read was already under way"
    assert not any("unsent kind" in t for t in w.memory.fed), "its reading was not fed to Honcho"
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None


async def test_the_withdrawn_check_failing_skips_the_feed_rather_than_leaking_it(
    engine, monkeypatch,
):
    shape = SHAPES[0]
    w = World(engine, media=True)

    async def boom(**_k: Any) -> Any:
        raise RuntimeError("db down")

    w.interpreter.release.clear()
    await w.pipeline.run(message=_photo(shape, M_TS, ""))
    await w.interpreter.entered.wait()
    monkeypatch.setattr(w.audit, "find_edited", boom)
    w.interpreter.release.set()
    await w.settle()
    assert not any("unsent kind" in t for t in w.memory.fed)


async def test_the_reconcile_counts_a_message_hidden_in_the_meantime_as_already_hidden(
    engine, monkeypatch,
):
    """Between the reconcile reading which messages are still visible and reaching
    one, something else (the live path, another run) hid it: that one is
    `already_hidden`, not a hide this run wrote."""
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    await _legacy_delete(w, shape, M_TS, "H-M")
    real = delete_withdrawal._withdraw_in

    async def hidden_first(conn: Any, *, message_id: int, now_ms: int) -> Any:
        async with engine.begin() as other:
            await other.execute(
                sa.update(MESSAGES).where(MESSAGES.c.id == message_id).values(hidden_at=1))
        return await real(conn, message_id=message_id, now_ms=now_ms)

    monkeypatch.setattr(delete_withdrawal, "_withdraw_in", hidden_first)
    report = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert (report.visible, report.hidden, report.already_hidden) == (1, 0, 1)
    assert await w.hidden_at(sc.m) == 1


async def test_the_reconcile_dry_run_is_bounded_by_timeouts_inside_its_one_transaction(
    engine, monkeypatch,
):
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    await _scene(w, shape)
    await _legacy_delete(w, shape, M_TS, "H-M")
    seen: list[str] = []
    real = delete_withdrawal._withdraw_in

    async def spy(conn: Any, *, message_id: int, now_ms: int) -> Any:
        for name in ("lock_timeout", "statement_timeout"):
            seen.append((await conn.execute(sa.text(f"SHOW {name}"))).scalar_one())
        return await real(conn, message_id=message_id, now_ms=now_ms)

    monkeypatch.setattr(delete_withdrawal, "_withdraw_in", spy)
    await reconcile_deletes(engine, apply=False, now_ms=T0 + 50_000)

    assert seen == ["5s", "1min"], "a dry run never waits on a lock or a statement for long"
