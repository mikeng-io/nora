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
from nora.domain.delete_authorship import ORPHAN_CAP_PER_DELETER
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
from nora.infra.sql.citation import mark_if_cites_hidden, record_citations
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.event_evidence import record_event_citations
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
    ORPHAN_DELETE,
)
from nora.infra.sql.standing import SqlStandingStore
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.language.culture_render import read_culture
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.platform.whatsapp.parser import parse_event
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
                "note_candidate", "orphan_delete", "messages", "row_history",
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
# a held delete: keyed to the message it names
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


async def test_a_withdrawn_check_that_keeps_failing_skips_this_pass_and_is_not_counted_a_delete(
    engine, monkeypatch,
):
    """A database outage must not read as a run of deletes: the pass is skipped (fail
    closed) and counted `check_failed`, never `withdrawn`, and nothing is marked read,
    so the next sighting of the bytes runs the job again."""
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
    assert w.metrics.get(
        "nora_media_comprehension_total", platform=shape.platform, label="image",
        status="nothing_found", reason="check_failed") == 1
    assert w.metrics.get(
        "nora_media_comprehension_total", platform=shape.platform, label="image",
        status="nothing_found", reason="withdrawn") == 0


async def test_a_withdrawn_check_that_fails_once_is_asked_again_and_the_read_goes_on(
    engine, monkeypatch,
):
    shape = SHAPES[0]
    w = World(engine, media=True)
    real = w.audit.find_edited
    calls = {"n": 0}

    async def flaky(**k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one hiccup")
        return await real(**k)

    monkeypatch.setattr(w.audit, "find_edited", flaky)
    await w.run(_photo(shape, M_TS, ""))

    assert len(w.interpreter.calls) == 1
    assert any("unsent kind" in t for t in w.memory.fed)


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


# =============================================================================
# the start-up sweep applies the SAME authorship rule as ingest
# =============================================================================


async def _record_for(w: World, shape: Shape, ts: int, handle: str, *, sender: str,
                      raw: dict[str, Any] | None = None) -> None:
    """A delete recorded the way the code before the authorship rule did, whoever sent it."""
    target = await w.audit.find_edited(
        conversation_id=shape.room, target=shape.target(ts, handle), sender_id=shape.author,
    )
    assert target is not None
    delete = dataclasses.replace(
        shape.deletion(ts, handle, sender=sender, at=T0 + 8000), raw=raw or {},
    )
    await w.audit.append_delete(message_id=target.id, message=delete, person_id=None)


async def test_the_sweep_does_not_apply_an_old_delete_record_that_is_not_the_authors(engine):
    """Before the rule existed WhatsApp deletes were recorded whoever sent them, so the
    first start after a deploy must not hide what an admin or a forger revoked then."""
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    await _record_for(w, shape, M_TS, "H-M", sender="w2-stranger")

    dry = await reconcile_deletes(engine, apply=False, now_ms=T0 + 50_000)
    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert (dry.visible, dry.hidden, dry.refused) == (0, 0, 1)
    assert (done.visible, done.hidden, done.refused) == (0, 0, 1)
    assert await w.hidden_at(sc.m) is None
    assert await _note_unsupported(engine, sc.note_m) is None
    assert "not their author" in "\n".join(render_reconcile(done))


async def test_unforget_is_not_blocked_by_a_delete_record_that_is_not_the_authors(engine):
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    await _record_for(w, shape, M_TS, "H-M", sender="w2-stranger")
    await forget_person(engine, documents=SqlDocumentStore(engine), person_id="ada",
                        apply=True, now_ms=T0 + 10_000)
    assert await w.visible(shape.room) == []

    done = await unforget_person(engine, person_id="ada", apply=True)

    assert done.kept_deleted == 0 and done.restored == 3
    assert sc.m in await w.visible(shape.room)


async def test_the_sweep_applies_an_authors_record_and_one_under_another_of_their_id_forms(
    engine,
):
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    # The message is stored under the author's id "w1"; the recorded revoke was picked as
    # the phone form but its raw event also carried the LID form the message is under.
    await _record_for(
        w, shape, M_TS, "H-M", sender="+85200000001",
        raw={"sender_phone": "85200000001", "sender_lid": "w1", "sender_jid": "w1@lid"},
    )

    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert (done.visible, done.hidden, done.refused) == (1, 1, 0)
    assert await w.hidden_at(sc.m) == T0 + 50_000


async def test_the_sweep_rescans_what_cites_a_message_a_delete_already_hid(engine):
    """A note or event written at the moment of the delete can be missed by the
    cascade; the sweep re-scans the edges of messages already hidden by a delete, and
    the dry run says so without writing."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    async with engine.begin() as conn:               # the lost race: edge written, never marked
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == sc.note_m)
                           .values(unsupported_at=None))

    dry = await reconcile_deletes(engine, apply=False, now_ms=T0 + 60_000)
    assert dry.healed == {"note": 1} and await _note_unsupported(engine, sc.note_m) is None

    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 60_000)
    assert done.healed == {"note": 1}
    assert await _note_unsupported(engine, sc.note_m) == T0 + 60_000
    again = await reconcile_deletes(engine, apply=True, now_ms=T0 + 70_000)
    assert again.healed == {}, "idempotent"


# =============================================================================
# a note or event written at the same moment as the delete
# =============================================================================


async def _interleave(engine: Any, *, write: Any, mid: int) -> int:
    """Writer W holds an open transaction that has written its citation edge and run the
    write-time check; THEN the delete starts. Returns the time the delete was stamped."""
    withdrawer = SqlDeleteWithdrawer(engine)
    stamp = T0 + 123_456
    async with engine.connect() as wconn:
        trans = await wconn.begin()
        await write(wconn)
        task = asyncio.create_task(withdrawer.withdraw(message_id=mid, now_ms=stamp))
        await asyncio.sleep(0.5)
        assert not task.done(), (
            "the hide must WAIT for the writer that read the message FOR SHARE"
        )
        await trans.commit()
    assert (await asyncio.wait_for(task, 10)).outcome == "hidden"
    return stamp


async def test_a_note_written_while_the_delete_runs_is_marked_by_one_of_the_two(engine):
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    late = await _note(engine, cites=sc.s, title="late-note", room=shape.room,
                       platform=shape.platform)

    async def write(conn: Any) -> None:
        await record_citations(
            conn, citing_store="note", citing_key=late, body=f"x [{sc.m}]", now_ms=T0 + 1,
        )

    stamp = await _interleave(engine, write=write, mid=sc.m)

    assert await _note_unsupported(engine, late) == stamp


async def test_an_event_written_while_the_delete_runs_is_marked_by_one_of_the_two(engine):
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    event_id = await _cited_event(engine, shape.room, "late event", sc.s)

    async def write(conn: Any) -> None:
        await record_event_citations(
            conn, event_id=str(event_id), parts=("what",),
            citations=(EventCitation(part="what", store="message", ref=str(sc.m)),),
        )
        await mark_if_cites_hidden(
            conn, citing_store="event", citing_key=str(event_id), message_ids={sc.m},
            now_ms=T0 + 1,
        )

    stamp = await _interleave(engine, write=write, mid=sc.m)

    event = await SqlGatheringStore(engine).get(event_id)
    assert event is not None and event.unsupported_at == stamp


# =============================================================================
# a delete whose target is not on record: the durable orphan
# =============================================================================


async def _orphans(engine: Any) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(sa.select(ORPHAN_DELETE))).all())


async def test_a_delete_that_arrives_before_its_message_is_kept_and_applied_when_it_lands(
    engine,
):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9000))
    held = await _orphans(engine)
    assert [(o.platform, o.conversation_id, o.handle) for o in held] == [
        (shape.platform, shape.room, "H-EARLY"),
    ]

    result = await w.run(shape.said(M_TS, "unsent before it was stored", "H-EARLY"))

    assert result.message is None and result.decision is None
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None
    assert w.memory.fed == []
    assert await _orphans(engine) == [], "applied, then discarded"
    assert w.count("applied_from_orphan", shape.platform) == 1
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one() == 1


async def test_a_message_that_arrives_long_after_its_delete_is_still_withdrawn(engine):
    """No hold to run out: the orphan is a row, not a timer."""
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9000))
    async with engine.begin() as conn:
        await conn.execute(sa.update(ORPHAN_DELETE).values(recorded_at=1))   # an old row
    await w.run(shape.said(M_TS, "arrives six minutes later", "H-EARLY"))
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None


async def test_a_restart_between_the_delete_and_the_message_loses_nothing(engine):
    shape = SHAPES[0]
    before = World(engine)
    await before.run(shape.deletion(M_TS, "", at=T0 + 9000))
    after = World(engine)                       # a fresh pipeline object, the same database

    await after.run(shape.said(M_TS, "the author's words", ""))

    assert await after.hidden_at(await after.row(shape.room, M_TS)) is not None


async def test_a_delete_whose_lookup_failed_is_not_lost(engine, monkeypatch):
    shape = SHAPES[1]
    w = World(engine)
    await w.run(shape.said(M_TS, "stored already", "H-M"))
    mid = await w.row(shape.room, M_TS)
    real = w.audit.find_edited

    async def boom(**_k: Any) -> Any:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(w.audit, "find_edited", boom)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    monkeypatch.setattr(w.audit, "find_edited", real)
    assert await w.hidden_at(mid) is None
    assert len(await _orphans(engine)) == 1, "kept: «could not look» is not «not there»"

    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert done.orphans_applied == 1
    assert await w.hidden_at(mid) == T0 + 50_000
    assert await _orphans(engine) == []


async def test_a_held_delete_that_is_not_the_authors_is_never_applied(engine):
    for shape in SHAPES[:2]:
        w = World(engine)
        await w.run(shape.deletion(M_TS, "H-M" if not shape.by_ts else "", sender="stranger-9",
                                   at=T0 + 9000))
        # Signal names the target by timestamp and WhatsApp by handle; either way held.
        # A held delete from a stranger is refused when the message lands, and never hides it.
        assert len(await _orphans(engine)) == 1
        result = await w.run(shape.said(M_TS, "the author's own words", "H-M"))
        assert result.message is not None, shape.platform
        assert await w.hidden_at(await w.row(shape.room, M_TS)) is None
        assert len(await _orphans(engine)) == 1, (
            "a stranger's held delete is not matched, so it is not consumed either")
        swept = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)
        assert swept.orphans_applied == 0
        assert await w.hidden_at(await w.row(shape.room, M_TS)) is None
        async with engine.begin() as conn:
            await conn.execute(sa.delete(ORPHAN_DELETE))
            await conn.execute(sa.delete(MESSAGES))


async def test_a_held_delete_never_matches_another_room_or_handle(engine):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-X", at=T0 + 9000))
    await w.run(shape.said(M_TS, "other handle", "H-Y"))
    await w.run(shape.said(M_TS + 1, "same handle, other room", "H-X", room="elsewhere"))
    await w.run(SHAPES[1].said(M_TS + 2, "same handle, other platform", "H-X",
                               room=shape.room))
    for room, ts in ((shape.room, M_TS), ("elsewhere", M_TS + 1), (shape.room, M_TS + 2)):
        assert await w.hidden_at(await w.row(room, ts)) is None
    assert len(await _orphans(engine)) == 1


async def test_the_sweep_purges_a_held_delete_nothing_ever_matched_and_keeps_a_recent_one(engine):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-OLD", at=T0 + 9000))
    await w.run(shape.deletion(M_TS + 1, "H-NEW", at=T0 + 9000))
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(ORPHAN_DELETE).where(ORPHAN_DELETE.c.handle == "H-OLD")
            .values(recorded_at=T0)
        )
        await conn.execute(
            sa.update(ORPHAN_DELETE).where(ORPHAN_DELETE.c.handle == "H-NEW")
            .values(recorded_at=T0 + 29 * 24 * 3600 * 1000)
        )
    now = T0 + 31 * 24 * 3600 * 1000

    dry = await reconcile_deletes(engine, apply=False, now_ms=now)
    assert dry.orphans_expired == 1 and len(await _orphans(engine)) == 2
    done = await reconcile_deletes(engine, apply=True, now_ms=now)

    assert done.orphans_expired == 1
    assert [o.handle for o in await _orphans(engine)] == ["H-NEW"]


async def test_the_gate_sees_a_held_delete_so_a_photo_is_not_read_in_the_gap(engine):
    """The hold used to be removed before the hide committed, leaving a moment in which
    the message was neither held nor hidden. The orphan stays until the delete has
    been applied."""
    shape = SHAPES[0]
    w = World(engine, media=True)
    await w.run(shape.deletion(M_TS, "", at=T0 + 9000))
    await w.run(_photo(shape, M_TS, ""))
    assert w.interpreter.calls == [] and w.memory.fed == []


# =============================================================================
# WhatsApp: an unmapped author's id form flips between the message and the revoke
# =============================================================================

_WA_ROOM = "176569000000@g.us"


def _wa_message(msg_id: str, *, phone: str | None, lid: str | None, ts: int) -> NormalizedMessage:
    msg = parse_event({
        "type": "message", "id": msg_id, "chat_jid": _WA_ROOM, "is_group": True,
        "sender_jid": f"{lid}@lid" if lid else f"{phone}@s.whatsapp.net",
        "sender_phone": phone, "sender_lid": lid, "push_name": "X", "text": "private words",
        "timestamp_ms": ts, "from_me": False, "media": [], "mentioned": [],
    })
    assert msg is not None
    return msg


def _wa_revoke(target: str, *, phone: str | None, lid: str | None, ts: int) -> NormalizedMessage:
    msg = parse_event({
        "type": "delete", "id": "REV-" + target, "chat_jid": _WA_ROOM, "is_group": True,
        "sender_jid": f"{lid}@lid" if lid else f"{phone}@s.whatsapp.net",
        "sender_phone": phone, "sender_lid": lid, "target_id": target,
        "timestamp_ms": ts, "from_me": False,
    })
    assert msg is not None
    return msg


@pytest.mark.parametrize(
    ("stored", "revoked_by", "hidden"),
    [
        ({"phone": None, "lid": "999"}, {"phone": "85211112222", "lid": "999"}, True),
        ({"phone": "85211112222", "lid": None}, {"phone": "85211112222", "lid": "999"}, True),
        ({"phone": "85211112222", "lid": "999"}, {"phone": "85299990000", "lid": "555"}, False),
    ],
    ids=["stored-under-lid", "stored-under-phone", "a-different-person"],
)
async def test_a_whatsapp_revoke_is_the_authors_under_any_id_form_the_event_carries(
    engine, stored, revoked_by, hidden,
):
    w = World(engine)
    await w.run(_wa_message("MSG-1", ts=T0 + 1, **stored))
    mid = await w.row(_WA_ROOM, T0 + 1)

    await w.run(_wa_revoke("MSG-1", ts=T0 + 5, **revoked_by))

    assert (await w.hidden_at(mid) is not None) is hidden
    assert w.count("refused_not_author", "whatsapp") == (0 if hidden else 1)


async def test_an_event_updated_after_the_delete_to_cite_it_is_born_unsupported(engine):
    """The second place an event's edges are written: a revision that newly cites the
    deleted message."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))
    store = SqlGatheringStore(engine)
    event = await store.get(sc.event_s)
    assert event is not None and event.unsupported_at is None

    await store.update(event, evidence=EventEvidence(
        parts=frozenset({"what"}),
        citations=(EventCitation(part="what", store="message", ref=str(sc.m)),),
    ))

    after = await store.get(sc.event_s)
    assert after is not None and after.unsupported_at is not None


async def test_a_delete_removes_pending_and_refused_proposals_and_keeps_created_ones(engine):
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    refused = await _candidate(engine, "refused-one", sc.m, status="refused")
    created = await _candidate(engine, "created-one", sc.m, status="created")

    await w.run(shape.deletion(M_TS, "H-M", at=T0 + 9000))

    assert await _candidates(engine) == {sc.cand_s, created}, "pending and refused went"
    assert sc.cand_m not in await _candidates(engine) and refused not in await _candidates(engine)


# =============================================================================
# the one central guard: every race ends at a hidden row
# =============================================================================


async def test_a_delete_racing_its_message_stops_the_message_at_the_guard(engine, monkeypatch):
    """The exact interleaving, with a forced await order: the delete's first lookup
    misses and writes its orphan; the message is audited; the delete's SECOND lookup
    finds it, hides it and discards the orphan; only THEN does the message's own check of
    held deletes run, and finds nothing. Without the guard the message went on to the
    memory feed."""
    shape = SHAPES[2]
    w = World(engine)
    real_find = w.audit.find_edited
    at_second, second = asyncio.Event(), asyncio.Event()
    looks = {"n": 0}

    async def find(**k: Any) -> Any:
        if k["target"].message_id == "H-RACE":
            looks["n"] += 1
            if looks["n"] == 2:
                at_second.set()
                await second.wait()
        return await real_find(**k)

    real_match = w.audit.match_orphan_deletes
    at_match, go_match = asyncio.Event(), asyncio.Event()

    async def match(**k: Any) -> Any:
        at_match.set()
        await go_match.wait()
        return await real_match(**k)

    monkeypatch.setattr(w.audit, "find_edited", find)
    monkeypatch.setattr(w.audit, "match_orphan_deletes", match)

    delete = asyncio.create_task(w.pipeline.run(message=shape.deletion(M_TS, "H-RACE", at=T0 + 9)))
    await asyncio.wait_for(at_second.wait(), 10)
    late = asyncio.create_task(w.pipeline.run(message=shape.said(M_TS, "raced words", "H-RACE")))
    await asyncio.wait_for(at_match.wait(), 10)  # audited, and about to ask for held deletes
    second.set()
    await asyncio.wait_for(delete, 10)          # found, hidden, orphan discarded
    assert await _orphans(engine) == []
    go_match.set()
    result = await asyncio.wait_for(late, 10)
    await w.settle()

    assert result.message is None and result.decision is None
    assert w.memory.fed == [], "the message must never reach the memory feed"
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="withdrawn") == 1


async def test_a_hidden_state_read_that_fails_once_is_asked_again_and_the_message_goes_on(
    engine, monkeypatch,
):
    shape = SHAPES[2]
    w = World(engine)
    real = w.audit.is_hidden
    calls = {"n": 0}

    async def flaky(**k: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one hiccup")
        return await real(**k)

    monkeypatch.setattr(w.audit, "is_hidden", flaky)
    result = await w.run(shape.said(M_TS, "one transient failure costs nothing", "H-1"))

    assert result.message is not None and calls["n"] == 2
    assert w.memory.fed, "fed to memory as an ordinary message"


async def test_a_hidden_state_read_that_fails_twice_stops_the_message_with_its_own_outcome(
    engine, monkeypatch,
):
    shape = SHAPES[2]
    w = World(engine)

    async def boom(**_k: Any) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(w.audit, "is_hidden", boom)
    result = await w.run(shape.said(M_TS, "fail closed, deliberately", "H-1"))

    assert result.message is None and w.memory.fed == []
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="guard_read_failed") == 1
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="withdrawal_pending") == 0, "no delete is known: the label must not claim one"


async def test_a_matched_delete_that_cannot_be_applied_feeds_and_raises_nothing(
    engine, monkeypatch,
):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9))

    async def boom(**_k: Any) -> int:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(w.audit, "append_delete", boom)
    result = await w.run(shape.said(M_TS, "arrives while the apply fails", "H-EARLY"))

    assert result.message is None and w.memory.fed == []
    assert w.count("apply_failed", shape.platform) == 1
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="withdrawal_pending") == 1, "stopped as pending, not as withdrawn"
    assert len(await _orphans(engine)) == 1, "left for the start-up sweep"
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is None


async def test_a_lookup_that_fails_while_a_matched_delete_is_applied_does_not_fail_open(
    engine, monkeypatch,
):
    """The held delete is applied DIRECTLY against the row the message was just given: no
    second lookup. A lookup that raised there left the message flowing on to the memory
    feed and wrote a second orphan row for a delete already matched."""
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9))

    async def boom(**_k: Any) -> Any:
        raise RuntimeError("lookup down")

    monkeypatch.setattr(w.audit, "find_edited", boom)
    result = await w.run(shape.said(M_TS, "late words", "H-EARLY"))

    assert result.message is None and w.memory.fed == []
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None
    assert await _orphans(engine) == [], "applied and discarded, never a second row"
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE))).scalar_one() == 1


# =============================================================================
# unforget and the sweep read the deleter's id forms back from the record
# =============================================================================


async def _set_person(engine: Any, room: str, person: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.conversation_id == room)
                           .values(person_id=person))


async def test_unforget_keeps_a_delete_hidden_that_was_applied_from_a_held_delete(engine):
    """An unmapped WhatsApp author stored under the LID form revokes under the phone form
    and the revoke overtakes the message. The record the application writes carries the
    revoker's id forms, so it still reads as the author's later; it used to be written
    with an empty event and then read as «not the author's», so `unforget` restored the
    message."""
    w = World(engine)
    await w.run(_wa_revoke("MSG-L", phone="85211112222", lid="999", ts=T0 + 5))
    await w.run(_wa_message("MSG-L", phone=None, lid="999", ts=T0 + 1))
    mid = await w.row(_WA_ROOM, T0 + 1)
    assert await w.hidden_at(mid) is not None
    await _set_person(engine, _WA_ROOM, "zed")

    done = await unforget_person(engine, person_id="zed", apply=True)

    assert (done.restored, done.kept_deleted) == (0, 1)
    assert await w.hidden_at(mid) is not None


async def test_unforget_keeps_a_delete_hidden_that_the_sweep_applied_from_a_held_delete(engine):
    w = World(engine)
    revoke = _wa_revoke("MSG-S", phone="85211112222", lid="999", ts=T0 + 5)
    assert revoke.touch is not None
    await w.audit.record_orphan_delete(
        message=revoke, touch=revoke.touch, person_id=None, now_ms=T0 + 6)
    mid = await w.audit.append(message=_wa_message("MSG-S", phone=None, lid="999", ts=T0 + 1))

    swept = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50)
    assert swept.orphans_applied == 1
    await _set_person(engine, _WA_ROOM, "zed")
    done = await unforget_person(engine, person_id="zed", apply=True)

    assert (done.restored, done.kept_deleted) == (0, 1)
    assert await w.hidden_at(mid) is not None


async def test_an_orphan_applied_delete_whose_hide_keeps_failing_is_not_refused_by_the_sweep(
    engine, monkeypatch,
):
    w = World(engine, retry_delays=())
    await w.run(_wa_revoke("MSG-F", phone="85211112222", lid="999", ts=T0 + 5))

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("cascade down")

    with monkeypatch.context() as patch:
        patch.setattr(delete_withdrawal, "withdraw_message", boom)
        await w.run(_wa_message("MSG-F", phone=None, lid="999", ts=T0 + 1))
    mid = await w.row(_WA_ROOM, T0 + 1)
    assert await w.hidden_at(mid) is None

    swept = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50)

    assert (swept.refused, swept.hidden) == (0, 1)
    assert await w.hidden_at(mid) == T0 + 50


# =============================================================================
# one lock order: message rows first, then the row that cites them
# =============================================================================


async def test_a_note_revised_while_the_delete_runs_neither_deadlocks_nor_stays_supported(
    engine, monkeypatch,
):
    """The writer used to lock its note row (the UPDATE) and THEN take FOR SHARE on the
    message while the delete held the message and its cascade waited for the note:
    a deadlock, the delete ended `cascade_failed` and its hide rolled back. With message
    rows locked first by both, one waits for the other and both finish."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    notes = SqlNoteStore(engine)
    head = (await notes.note_by_id(unit="u", note_id=sc.note_m)).head_revision_id  # type: ignore[union-attr]
    real = delete_withdrawal.withdraw_message
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*a, **k)

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", held)
    withdrawer = SqlDeleteWithdrawer(engine)
    delete = asyncio.create_task(withdrawer.withdraw(message_id=sc.m, now_ms=T0 + 77))
    await asyncio.wait_for(entered.wait(), 10)   # the message row is hidden and locked
    revise = asyncio.create_task(notes.append_revision(
        unit="u", note_id=sc.note_m, parent_revision_id=head,
        body=f"revised, still resting on [{sc.m}]", established_by="test", now_ms=T0 + 5,
    ))
    await asyncio.sleep(0.5)
    release.set()
    outcome, _ = await asyncio.wait_for(asyncio.gather(delete, revise), 15)

    assert outcome.outcome == "hidden", "the delete must not end cascade_failed"
    assert await _note_unsupported(engine, sc.note_m) is not None


# =============================================================================
# the held-delete table is bounded
# =============================================================================


async def _keep(store: Any, *, platform: str = "discord", room: str = "g1",
                handle: str = "H1", deleter: str = "", now: int = T0, ts: int | None = None,
                ) -> Any:
    msg = NormalizedMessage(platform, room, "group", deleter, None, 5, "", {})
    touch = Touch(kind="delete", target=EditTarget(message_id=handle, sent_ts=ts))
    return await store.record_orphan_delete(
        message=msg, touch=touch, person_id=None, now_ms=now)


async def test_the_same_delivery_again_adds_no_row(engine):
    store = SqlRecordStore(engine)
    ids = {(await _keep(store)).id for _ in range(5)}
    assert len(ids) == 1
    assert len(await _orphans(engine)) == 1
    # A different delivery (another handle, another deleter) is another row.
    assert (await _keep(store, handle="H2")).id not in ids
    assert (await _keep(store, deleter="d2")).id not in ids
    assert len(await _orphans(engine)) == 3


async def test_a_signal_delivery_is_keyed_by_its_timestamp(engine):
    store = SqlRecordStore(engine)
    a = await _keep(store, platform="signal", handle="", ts=100, deleter="+1")
    b = await _keep(store, platform="signal", handle="", ts=100, deleter="+1")
    c = await _keep(store, platform="signal", handle="", ts=101, deleter="+1")
    assert a.id == b.id and c.id != a.id and len(await _orphans(engine)) == 2


async def test_a_flood_of_unknown_discord_deletes_does_not_cost_a_real_race_in_a_fresh_room(
    engine,
):
    """Discord and Instagram deletes name nobody, so every held row has an empty deleter.
    Three hundred deletes of messages Nora never stored (embed-only messages the parser
    drops, messages from before she joined) over thirty rooms are ordinary traffic and
    must not become one shared bucket that refuses the next real race."""
    shape = SHAPES[2]
    w = World(engine)
    for n in range(300):
        await w.pipeline.run(message=shape.deletion(
            M_TS + n, f"U{n}", room=f"room-{n % 30}", at=T0 + n))
    assert len(await _orphans(engine)) == 300
    assert w.count("orphan_evicted", shape.platform) == 0

    await w.run(shape.deletion(M_TS, "H-REAL", room="fresh", at=T0 + 9000))
    result = await w.run(shape.said(M_TS, "a real race, in a fresh room", "H-REAL",
                                    room="fresh"))

    assert result.message is None and w.memory.fed == []
    assert await w.hidden_at(await w.row("fresh", M_TS)) is not None


async def test_a_hostile_whatsapp_member_cannot_push_out_the_authors_genuine_early_unsend(
    engine,
):
    """A modified client revokes ids that do not exist, over and over. Its own rows are
    bounded well below the room, so it evicts ITS oldest, never the author's."""
    shape = SHAPES[1]
    w = World(engine)
    for n in range(300):
        await w.pipeline.run(message=shape.deletion(
            M_TS + n, f"NOPE-{n}", sender="w-hostile", at=T0 + n))
    mine = await _orphans(engine)
    assert len(mine) <= ORPHAN_CAP_PER_DELETER, "the hostile member is bounded by itself"
    assert w.count("orphan_evicted", shape.platform) == 300 - ORPHAN_CAP_PER_DELETER

    await w.run(shape.deletion(M_TS, "H-REAL", at=T0 + 9000))               # the author's
    result = await w.run(shape.said(M_TS, "the author's early unsent words", "H-REAL"))

    assert result.message is None and w.memory.fed == []
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None


async def test_eviction_takes_the_oldest_of_the_same_deleter_and_is_counted(engine):
    shape = SHAPES[1]
    w = World(engine)
    cap = ORPHAN_CAP_PER_DELETER
    for n in range(cap + 3):
        await w.pipeline.run(message=shape.deletion(
            M_TS + n, f"E{n}", sender="w-flood", at=T0 + n))
    handles = sorted(o.handle for o in await _orphans(engine))
    assert handles == sorted(f"E{n}" for n in range(3, cap + 3)), "the three oldest went"
    assert w.count("orphan_evicted", shape.platform) == 3


async def test_the_room_cap_evicts_the_rooms_oldest_across_deleters_and_spares_other_rooms(
    engine, monkeypatch,
):
    import nora.infra.sql.audit as audit_module

    monkeypatch.setattr(audit_module, "ORPHAN_CAP_PER_ROOM", 3)
    store = SqlRecordStore(engine)
    for n in range(3):
        await _keep(store, platform="whatsapp", room="g1", handle=f"R{n}", deleter=f"d{n}",
                    now=T0 + n)
    await _keep(store, platform="whatsapp", room="g2", handle="OTHER", deleter="dx", now=T0 + 5)
    rec = await _keep(store, platform="whatsapp", room="g1", handle="R3", deleter="d3",
                      now=T0 + 10)

    assert rec.evicted == 1
    assert sorted(o.handle for o in await _orphans(engine)) == ["OTHER", "R1", "R2", "R3"]


async def test_the_overall_cap_evicts_the_oldest_overall(engine, monkeypatch):
    import nora.infra.sql.audit as audit_module

    monkeypatch.setattr(audit_module, "ORPHAN_CAP_GLOBAL", 3)
    store = SqlRecordStore(engine)
    for n in range(3):
        await _keep(store, platform="whatsapp", room=f"g{n}", handle=f"G{n}", deleter=f"d{n}",
                    now=T0 + n)
    rec = await _keep(store, platform="whatsapp", room="g9", handle="NEW", deleter="d9",
                      now=T0 + 10)

    assert rec.evicted == 1
    assert sorted(o.handle for o in await _orphans(engine)) == ["G1", "G2", "NEW"]


async def test_a_new_delivery_costs_a_bounded_number_of_history_rows_and_a_repeat_none(engine):
    """No insert-then-delete pair for a refusal: eviction happens BEFORE the insert, and a
    re-delivered event writes nothing at all."""
    store = SqlRecordStore(engine)
    cap = ORPHAN_CAP_PER_DELETER

    async def versions() -> int:
        async with engine.connect() as conn:
            return int((await conn.execute(sa.text(
                "SELECT count(*) FROM row_history WHERE table_name = 'orphan_delete'"
            ))).scalar_one())

    for n in range(cap):
        await _keep(store, handle=f"H{n}", deleter="flood", now=T0 + n)
    full = await versions()
    await _keep(store, handle="H0", deleter="flood", now=T0 + 999)           # a repeat
    assert await versions() == full
    await _keep(store, handle="NEW", deleter="flood", now=T0 + 1000)         # at the cap
    assert await versions() == full + 2, "one insert and ONE eviction, nothing more"


async def test_an_expired_held_delete_is_purged_when_a_new_one_is_written(engine):
    from nora.domain.delete_authorship import ORPHAN_TTL_MS

    store = SqlRecordStore(engine)
    await _keep(store, handle="OLD", now=1)
    await _keep(store, handle="FRESH", now=ORPHAN_TTL_MS + 10)
    assert [o.handle for o in await _orphans(engine)] == ["FRESH"]


# --- the set-based sweep, against the per-message rule -----------------------


async def _seed(engine: Any, rng: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    platforms = ("signal", "whatsapp", "discord")
    rooms = ("r0", "r1", "r2")
    persons = (None, "ada", "ben", "cy")
    messages: list[dict[str, Any]] = []
    for n in range(2400):
        platform = rng.choice(platforms)
        messages.append({
            "platform": platform, "conversation_id": rng.choice(rooms),
            "conversation_type": "group", "sender_id": f"s{rng.randrange(40)}",
            "person_id": rng.choice(persons), "ts": 10_000 + rng.randrange(900),
            "body": f"m{n}", "is_nora": 0, "delivery_status": "sent", "raw_json": "{}",
            "platform_message_id": None if platform == "signal" else f"h{n}",
        })
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES), messages)
        signal_ids = [int(i) for i in (await conn.execute(
            sa.select(MESSAGES.c.id).where(MESSAGES.c.platform == "signal")
            .order_by(MESSAGES.c.id).limit(150))).scalars()]
        # Signal names a message by its ORIGINAL timestamp or by one of its edits'.
        await conn.execute(sa.insert(MESSAGE_EDIT), [
            {"message_id": i, "ts": 50_000 + i, "body": "edited", "raw_json": "{}"}
            for i in signal_ids
        ])
        rows = (await conn.execute(
            sa.select(MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id,
                      MESSAGES.c.sender_id, MESSAGES.c.person_id, MESSAGES.c.ts,
                      MESSAGES.c.platform_message_id).order_by(MESSAGES.c.id))).all()
    stored = [dict(r._mapping) | {"edit_ts": 50_000 + r.id if r.id in signal_ids else None}
              for r in rows]
    orphans: list[dict[str, Any]] = []
    for n in range(2400):
        target = rng.choice(stored)
        names = rng.random() < 0.6
        platform = target["platform"] if names or rng.random() < 0.5 else rng.choice(platforms)
        room = target["conversation_id"] if names or rng.random() < 0.5 else rng.choice(rooms)
        if platform == "signal":
            handle, sent_ts = "", target["ts"] + (0 if names else rng.choice((0, 7, 1000)))
            if names and target["edit_ts"] is not None and rng.random() < 0.5:
                sent_ts = target["edit_ts"]          # named by one of its edits' timestamps
        else:
            handle = (target["platform_message_id"] or "") if names else f"nope{n}"
            handle = handle if rng.random() < 0.9 else ""
            sent_ts = None if handle else 99_999
        author_like = rng.random() < 0.7
        deleter = target["sender_id"] if author_like else f"s{rng.randrange(40)}"
        person = target["person_id"] if rng.random() < 0.6 else rng.choice(persons)
        orphans.append({
            "platform": platform, "conversation_id": room, "handle": handle,
            "sent_ts": sent_ts, "deleter_id": deleter,
            "deleter_ids": [deleter] + ([target["sender_id"]] if rng.random() < 0.1 else []),
            "deleter_person_id": person, "event_ts": 5, "recorded_at": T0 + n,
        })
    # The unique delivery key: drop the repeats the generator may have made.
    seen: set[tuple[Any, ...]] = set()
    unique = []
    for o in orphans:
        key = (o["platform"], o["conversation_id"], o["handle"], o["sent_ts"], o["deleter_id"])
        if key not in seen:
            seen.add(key)
            unique.append(o)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(ORPHAN_DELETE), unique)
    return stored, unique


async def test_the_set_based_sweep_agrees_with_the_per_message_rule_at_scale(engine):
    """A few thousand held deletes and messages, random overlap, mixed platforms, authors
    and strangers. What the sweep hides is exactly what the one rule says each held delete
    names (the rule the pipeline applies message by message), the pipeline's own match
    returns the same orphans for a sample of messages, and the whole sweep is bounded."""
    import random
    import time

    from nora.domain.delete_authorship import ORPHAN_TTL_MS, orphan_names_message
    from nora.infra.sql.audit import orphan_delete_of

    stored, _ = await _seed(engine, random.Random(20261003))
    async with engine.connect() as conn:
        orphans = [orphan_delete_of(r) for r in (await conn.execute(
            sa.select(ORPHAN_DELETE).order_by(ORPHAN_DELETE.c.id))).all()]
    by_room: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for m in stored:
        by_room.setdefault((m["platform"], m["conversation_id"]), []).append(m)

    def names(o: Any, m: dict[str, Any], *, arriving: bool = False) -> bool:
        edits = [] if arriving or m["edit_ts"] is None else [m["edit_ts"]]
        return orphan_names_message(
            o, platform=m["platform"], conversation_id=m["conversation_id"],
            handle=m["platform_message_id"] or "", timestamps=(m["ts"], *edits),
            sender_id=m["sender_id"], person_id=m["person_id"])

    expected: set[int] = set()
    for o in orphans:
        hit = next((m for m in by_room.get((o.platform, o.conversation_id), []) if names(o, m)),
                   None)
        if hit is not None:
            expected.add(hit["id"])
    assert 300 < len(expected) < 2000, "the generator exercises both outcomes"

    store = SqlRecordStore(engine)
    for m in stored[:120]:                       # the pipeline's own check, a sample
        msg = NormalizedMessage(
            m["platform"], m["conversation_id"], "group", m["sender_id"], None, m["ts"], "x",
            {}, message_id=m["platform_message_id"] or "")
        matched = await store.match_orphan_deletes(message=msg, person_id=m["person_id"])
        got = {o.id for o in matched}
        # A message just recorded has no edits yet: only its own timestamp can name it.
        want = {o.id for o in orphans if names(o, m, arriving=True)}
        assert got == want

    started = time.monotonic()
    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + ORPHAN_TTL_MS // 2)
    assert time.monotonic() - started < 60

    async with engine.connect() as conn:
        hidden = {int(i) for i in (await conn.execute(
            sa.select(MESSAGES.c.id).where(MESSAGES.c.hidden_at.is_not(None)))).scalars()}
        left = (await conn.execute(sa.select(sa.func.count()).select_from(ORPHAN_DELETE))
                ).scalar_one()
    assert hidden == expected
    assert done.failed == 0 and done.orphans_applied >= len(expected)
    # What is left is exactly the held deletes that name nothing: an applied one is consumed.
    unmatched = {
        o.id for o in orphans
        if not any(names(o, m) for m in by_room.get((o.platform, o.conversation_id), []))
    }
    async with engine.connect() as conn:
        remaining = {int(i) for i in (await conn.execute(
            sa.select(ORPHAN_DELETE.c.id))).scalars()}
    assert remaining == unmatched and left == len(unmatched)


async def test_the_match_reads_the_handle_and_timestamp_indexes_not_the_whole_table(engine):
    """At 200k rows the match was a parallel sequential scan (~12 ms per message). It now
    reads `idx_orphan_delete_handle` / `idx_orphan_delete_sent_ts` through the room."""
    from nora.infra.sql.audit import orphan_candidates_stmt

    rows = [
        {"platform": "whatsapp" if i % 2 else "signal", "conversation_id": f"r{i % 50}",
         "handle": f"h{i}" if i % 2 else "", "sent_ts": None if i % 2 else 1000 + i,
         "deleter_id": f"d{i % 700}", "deleter_ids": [], "event_ts": 5, "recorded_at": T0 + i}
        for i in range(30_000)
    ]
    async with engine.begin() as conn:
        for start in range(0, len(rows), 5000):
            await conn.execute(sa.insert(ORPHAN_DELETE), rows[start:start + 5000])
        await conn.execute(sa.text("ANALYZE orphan_delete"))
    msg = NormalizedMessage("whatsapp", "r1", "group", "d1", None, 1001, "x", {}, message_id="h1")
    stmt = orphan_candidates_stmt(msg)
    assert stmt is not None
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    async with engine.connect() as conn:
        plan = "\n".join(
            r[0] for r in (await conn.execute(sa.text("EXPLAIN " + sql))).all())
    assert "Seq Scan on orphan_delete" not in plan, plan
    assert "idx_orphan_delete_handle" in plan or "idx_orphan_delete_sent_ts" in plan, plan


async def test_held_deletes_that_cannot_be_read_once_are_asked_again(engine, monkeypatch):
    shape = SHAPES[2]
    w = World(engine)
    real = w.audit.match_orphan_deletes
    calls = {"n": 0}

    async def flaky(**k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one hiccup")
        return await real(**k)

    monkeypatch.setattr(w.audit, "match_orphan_deletes", flaky)
    result = await w.run(shape.said(M_TS, "no delete names this", "H-1"))
    assert result.message is not None and calls["n"] == 2 and w.memory.fed


async def test_held_deletes_that_cannot_be_read_twice_stop_the_message_with_its_own_outcome(
    engine, monkeypatch,
):
    shape = SHAPES[2]
    w = World(engine)

    async def boom(**_k: Any) -> Any:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(w.audit, "match_orphan_deletes", boom)
    result = await w.run(shape.said(M_TS, "unknown whether a delete names it", "H-1"))

    assert result.message is None and w.memory.fed == []
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="guard_read_failed") == 1


# =============================================================================
# the curator's batch takes its message locks once, sorted, before the first write
# =============================================================================


async def _two_notes(w: World, shape: Shape) -> tuple[Scene, str]:
    """A scene (note A cites m, the message to be deleted) and a second note B citing the
    control room's message `o`."""
    sc = await _scene(w, shape)
    other = await _note(w.engine, cites=sc.o, title="about-o-too", room=shape.room,
                        platform=shape.platform)
    return sc, other


async def _head(engine: Any, note_id: str) -> str:
    note = await SqlNoteStore(engine).note_by_id(unit="u", note_id=note_id)
    assert note is not None and note.head_revision_id is not None
    return note.head_revision_id


def _batch(*pairs: tuple[str, str, str]) -> list[Any]:
    from nora.domain.notes import NoteRevisionProposal
    from nora.domain.ports import Proposal

    return [
        Proposal(
            payload=NoteRevisionProposal(note_id=nid, parent_revision_id=head, body=body),
            instruction_sha256="0" * 64, model="m", established_by="curator",
        )
        for nid, head, body in pairs
    ]


async def test_a_batch_revising_two_notes_does_not_deadlock_with_a_delete(engine, monkeypatch):
    """The reviewer's PL1: note A cites m3 and is revised to cite m9; note B is revised to
    cite m3; a delete hides m3 and its cascade updates A. A lock taken per WRITE meant the
    batch held A's row and then waited for m3, which the delete held while its cascade
    waited for A. The lock is now taken ONCE over the whole batch, sorted, before the first
    write."""
    shape = SHAPES[1]
    w = World(engine)
    sc, note_b = await _two_notes(w, shape)
    notes = SqlNoteStore(engine)
    batch = _batch(
        (sc.note_m, await _head(engine, sc.note_m), f"now resting on [{sc.s}]"),
        (note_b, await _head(engine, note_b), f"now resting on the deleted line [{sc.m}]"),
    )
    real = delete_withdrawal.withdraw_message
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*a, **k)

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", held)
    delete = asyncio.create_task(
        SqlDeleteWithdrawer(engine).withdraw(message_id=sc.m, now_ms=T0 + 77))
    await asyncio.wait_for(entered.wait(), 10)
    proposed = asyncio.create_task(notes.propose(key="u", proposals=batch, now_ms=T0 + 5))
    await asyncio.sleep(0.5)
    release.set()
    outcome, ids = await asyncio.wait_for(asyncio.gather(delete, proposed), 15)

    assert outcome.outcome == "hidden", "the delete must not end cascade_failed"
    assert list(ids) == [sc.note_m, note_b]
    assert await _note_unsupported(engine, note_b) is not None, "B cites the deleted line"


async def test_two_batches_citing_overlapping_messages_in_opposite_orders_both_finish(engine):
    shape = SHAPES[1]
    w = World(engine)
    sc, note_b = await _two_notes(w, shape)
    notes = SqlNoteStore(engine)
    first = _batch(
        (sc.note_m, await _head(engine, sc.note_m),
         f"cites [{sc.s}] then [{sc.m}] then [{sc.o}]"),
    )
    second = _batch(
        (note_b, await _head(engine, note_b), f"cites [{sc.o}] then [{sc.m}] then [{sc.s}]"),
    )
    await asyncio.wait_for(asyncio.gather(
        notes.propose(key="u", proposals=first, now_ms=T0 + 1),
        notes.propose(key="u", proposals=second, now_ms=T0 + 1),
    ), 15)


async def test_an_event_revised_while_the_delete_runs_neither_deadlocks_nor_stays_supported(
    engine, monkeypatch,
):
    """The event UPDATE path: the cited message is locked BEFORE the event row. Without
    that the writer locks the event row FOR UPDATE and then waits for the message the
    delete holds, while the delete's cascade waits for the event row."""
    shape = SHAPES[1]
    w = World(engine)
    sc = await _scene(w, shape)
    store = SqlGatheringStore(engine)
    event = await store.get(sc.event_m)
    assert event is not None
    real = delete_withdrawal.withdraw_message
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*a, **k)

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", held)
    delete = asyncio.create_task(
        SqlDeleteWithdrawer(engine).withdraw(message_id=sc.m, now_ms=T0 + 77))
    await asyncio.wait_for(entered.wait(), 10)
    revise = asyncio.create_task(store.update(event, evidence=EventEvidence(
        parts=frozenset({"what"}),
        citations=(EventCitation(part="what", store="message", ref=str(sc.m)),),
    )))
    await asyncio.sleep(0.5)
    release.set()
    outcome, _ = await asyncio.wait_for(asyncio.gather(delete, revise), 15)

    assert outcome.outcome == "hidden"
    after = await store.get(sc.event_m)
    assert after is not None and after.unsupported_at is not None


# =============================================================================
# the edit path applies a held delete too, and guards the original
# =============================================================================


async def test_a_delete_naming_an_edit_before_the_edit_lands_withdraws_the_message(engine):
    """Signal names an edit by its OWN timestamp. The delete arrives first (it finds nothing
    and is held); when the edit lands the held delete names it, and the edit is neither fed
    to memory nor answered."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.said(M_TS, "the original words", ""))
    mid = await w.row(shape.room, M_TS)
    fed = list(w.memory.fed)
    await w.run(shape.deletion(M_TS + 700, "", at=T0 + 9000))     # names the edit's timestamp
    assert len(await _orphans(engine)) == 1

    result = await w.run(shape.edit(M_TS, "an edit nobody should read", "", at=M_TS + 700))

    assert result.message is None and result.decision is None
    assert await w.hidden_at(mid) is not None
    assert w.memory.fed == fed, "the edit was not fed to memory"
    assert await _orphans(engine) == [], "applied, then discarded"
    assert w.count("applied_from_orphan", shape.platform) == 1


async def test_the_sweep_resolves_a_held_delete_that_names_an_edit(engine):
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.said(M_TS, "original", ""))
    mid = await w.row(shape.room, M_TS)
    delete = shape.deletion(M_TS + 700, "", at=T0 + 9000)
    assert delete.touch is not None
    await w.audit.record_orphan_delete(
        message=delete, touch=delete.touch, person_id=None, now_ms=T0 + 9001)
    await w.audit.append_edit(message_id=mid, message=shape.edit(M_TS, "edited", "",
                                                                  at=M_TS + 700))

    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert done.orphans_applied == 1 and await w.hidden_at(mid) == T0 + 50_000


async def test_an_edit_whose_original_a_delete_hid_in_the_meantime_goes_no_further(
    engine, monkeypatch,
):
    """A delete hides the original between the moment the edit path read it and the guard."""
    shape = SHAPES[1]
    w = World(engine)
    await w.run(shape.said(M_TS, "original", "H-M"))
    mid = await w.row(shape.room, M_TS)
    fed = list(w.memory.fed)
    real = w.audit.append_edit

    async def hide_then_record(**k: Any) -> Any:
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(MESSAGES).where(MESSAGES.c.id == mid).values(hidden_at=1))
        return await real(**k)

    monkeypatch.setattr(w.audit, "append_edit", hide_then_record)
    result = await w.run(shape.edit(M_TS, "an edit of a hidden message", "H-M", at=M_TS + 5))

    assert result.message is None and w.memory.fed == fed
    assert w.metrics.get(
        "nora_ingest_outcomes_total", platform=shape.platform, conversation_type="group",
        outcome="withdrawn") == 1


# =============================================================================
# a matched delete whose cascade fails is retried; ids forms are stored for every platform
# =============================================================================


async def test_a_held_delete_applied_with_a_failing_cascade_is_retried(engine, monkeypatch):
    shape = SHAPES[2]
    w = World(engine, retry_delays=(0.0,))
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9))
    real = delete_withdrawal.withdraw_message
    calls = {"n": 0}

    async def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cascade down once")
        return await real(*a, **k)

    monkeypatch.setattr(delete_withdrawal, "withdraw_message", flaky)
    result = await w.run(shape.said(M_TS, "arrives while the cascade fails", "H-EARLY"))

    assert result.message is None and w.memory.fed == []
    assert await w.hidden_at(await w.row(shape.room, M_TS)) is not None
    assert w.count("cascade_failed", shape.platform) == 1
    assert w.count("reconciled", shape.platform) == 1


async def test_the_deleters_id_forms_are_stored_by_the_touch_stage_for_a_signal_event_too(engine):
    """Signal's parser gives one id today, but the record must hold whatever forms an event
    carries, on every platform: otherwise `unforget` un-hides a deleted message the day a
    parser fills them. A Signal-shaped event whose revoker has two ids, the author stored
    under the other, unmapped."""
    shape = SHAPES[0]
    w = World(engine)
    await w.run(shape.said(M_TS, "words", "", sender="aci-1"))
    mid = await w.row(shape.room, M_TS)
    revoke = dataclasses.replace(
        shape.deletion(M_TS, "", sender="+1", at=T0 + 9000),
        touch=Touch(kind="delete", target=EditTarget(sent_ts=M_TS),
                    actor_ids=("+1", "aci-1")),
    )

    await w.run(revoke)
    assert await w.hidden_at(mid) is not None
    await _set_person(engine, shape.room, "zed")
    done = await unforget_person(engine, person_id="zed", apply=True)

    assert (done.restored, done.kept_deleted) == (0, 1)


async def test_a_historic_event_that_happens_to_contain_actor_ids_is_not_trusted(engine):
    """Only the key Nora writes (`DELETER_IDS_KEY`) is read as the deleter's id forms: an
    old event with an `actor_ids` of its own, naming the author, does not make a stranger's
    record genuine."""
    shape = SHAPES[1]
    w = World(engine, withdrawer=None)
    sc = await _scene(w, shape)
    await _record_for(w, shape, M_TS, "H-M", sender="w2-stranger",
                      raw={"actor_ids": [shape.author]})

    done = await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    assert (done.visible, done.hidden, done.refused) == (0, 0, 1)
    assert await w.hidden_at(sc.m) is None


async def test_two_sweeps_at_once_write_one_record_and_count_one_application(engine):
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9))
    mid = await w.audit.append(message=shape.said(M_TS, "stored without the pipeline",
                                                  "H-EARLY"))

    first, second = await asyncio.gather(
        reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000),
        reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000),
    )

    assert first.orphans_applied + second.orphans_applied == 1
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE)
            .where(MESSAGE_DELETE.c.message_id == mid))).scalar_one() == 1
    assert await w.hidden_at(mid) is not None


async def test_the_same_delivery_arriving_concurrently_leaves_one_row_and_no_error(engine):
    """Both writers pass the «is it held?» read before either inserts: the unique key (with
    the NULL timestamp folded, as Discord and Instagram deliveries have none) makes the
    loser find the winner's row instead of adding a second or raising."""
    store = SqlRecordStore(engine)

    records = await asyncio.gather(*[_keep(store, handle="SAME") for _ in range(8)])

    assert len({r.id for r in records}) == 1
    assert len(await _orphans(engine)) == 1


async def test_a_held_delete_whose_record_the_live_path_already_wrote_is_not_recorded_twice(
    engine,
):
    """The live path wrote the record and the hide, then died before discarding the held
    row: the sweep applies the held delete again and must not add a second record."""
    shape = SHAPES[2]
    w = World(engine)
    await w.run(shape.deletion(M_TS, "H-EARLY", at=T0 + 9))
    [held] = await _orphans(engine)
    mid = await w.audit.append(message=shape.said(M_TS, "stored", "H-EARLY"))
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGE_DELETE).values(
            message_id=mid, sender_id=held.deleter_id, person_id=None, ts=held.event_ts,
            raw_json="{}"))

    await reconcile_deletes(engine, apply=True, now_ms=T0 + 50_000)

    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGE_DELETE)
            .where(MESSAGE_DELETE.c.message_id == mid))).scalar_one() == 1
    assert await w.hidden_at(mid) is not None
