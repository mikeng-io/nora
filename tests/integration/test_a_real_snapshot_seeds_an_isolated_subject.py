"""Read a room's state as of an instant into a separate Postgres sandbox.

The source is seeded through `know()` and the production stores. The
assertions read the copied state and the source back independently.
**Three claims, and none is provable from the others:**

1. the sandbox holds a FAITHFUL copy AT THE MOMENT'S OWN CLOCK — every row at
   its real `ts`, the standing with the `updated_at` it had, the culture
   account and the notebook that were current then, and nothing from after;
2. the SOURCE is untouched by the seeding. The source is the deployment's
   clone and is opened read-only, but "opened read-only" is a claim about an
   engine and this asserts the rows;
3. the sandbox database is a real, migrated, DROPPED-afterwards database
   beside the source — isolation by database is only isolation if the
   database exists and goes away.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.schema import CULTURE, CULTURE_REVISION, MESSAGES
from nora.infra.sql.standing import SqlStandingStore
from nora.subject import (
    Knowledge,
    Message,
    NoteSeed,
    ReminderSeed,
    Subject,
    SubjectSpec,
    know,
    sandbox_as_of,
    sandbox_database,
    sandbox_from_snapshot,
)
from tests.sandbox_fixtures import ROOM, Source, offline_subject, seed_source

BEFORE_MS = 1_752_000_000_000
MINUTE = 60_000
HOUR = 3_600_000

#: Fixed ids, so a test names the row it means instead of matching on text.
OUTSTANDING = UUID("aaaaaaaa-0000-4000-8000-000000000001")
FIRED_BEFORE = UUID("aaaaaaaa-0000-4000-8000-000000000002")
FIRED_AFTER = UUID("aaaaaaaa-0000-4000-8000-000000000003")
CREATED_AFTER = UUID("aaaaaaaa-0000-4000-8000-000000000004")
CANCELLED = UUID("aaaaaaaa-0000-4000-8000-000000000005")


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _reminder(rid: UUID, text: str, *, created: int, due: int) -> Reminder:
    return Reminder(
        id=rid, requester_person_id="miles_novak", platform=ROOM[0],
        conversation_id=ROOM[1], conversation_type="group", text=text,
        created_at=_at(created), due_at=_at(due),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )


@pytest.fixture
async def engine(clean_pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(clean_pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _say(engine: Any, body: str, *, at: int) -> int:
    return await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
            sender_id="+88387175396", sender_name="Miles", timestamp=at, body=body, raw={},
        ),
        person_id="miles_novak",
    )


@pytest.fixture
async def source(
    clean_pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> Source:
    """The room as it stood at `BEFORE_MS` — and as it stood AFTER, so the
    copy can be caught taking today's state instead of the instant's. Every
    tier carries a second, LATER state for `tests/infra/test_snapshot.py`'s
    reason: a reconstruction that ignored `before_ms` passes any single-state
    test."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    src = await seed_source(
        clean_pg_url, tmp_path, Knowledge(
            transcript=(
                Message(body="are we still on for friday", at_ms=BEFORE_MS - 10 * MINUTE),
                # SAME display name, NO person: the only shape that tells
                # `person=""` (default) from `person=None` (unknown) apart.
                Message(body="i can't make it", person=None, at_ms=BEFORE_MS - 4 * MINUTE),
                Message(body="want me to move it?", is_nora=True, at_ms=BEFORE_MS - MINUTE),
            ),
            standing="they are deciding whether friday still works",
            standing_age_hours=3.0,
            culture="they confirm plans late and nobody minds",
            notes=(NoteSeed(
                title="Friday dinner", description="whether friday holds",
                body="nobody has confirmed friday", uncertainty="open",
                closing_condition="somebody says yes or no", anchor_date=20260701,
            ),),
        ),
        template=pg_template, before_ms=BEFORE_MS,
    )
    # AFTER the instant. None of it may reach the sandbox.
    await _say(engine, "moved it to saturday", at=BEFORE_MS + HOUR)
    await SqlStandingStore(engine).write(standing=Standing(
        platform=ROOM[0], conversation_id=ROOM[1],
        body="friday is off; saturday is the plan", fold_count=4,
        updated_at=BEFORE_MS + 2 * HOUR,
    ))
    culture_key = ROOM[1]
    await SqlCultureStore(engine).append(
        key=culture_key, body="they confirm plans late, and now they say so",
        written_at=BEFORE_MS + 3 * HOUR, established_by="run-8",
    )
    notes = SqlNoteStore(engine)
    unit = "nora"
    [note] = await notes.standing(unit=unit)
    await notes.append_revision(
        unit=unit, note_id=note.id, body="jordan is out; friday is dead",
        parent_revision_id=note.head_revision_id, established_by="debrief",
        now_ms=BEFORE_MS + 4 * HOUR,
    )
    await notes.create(
        unit=unit, title="Saturday dinner", description="whether saturday holds",
        uncertainty="open", closing_condition="somebody books it",
        established_in=ROOM[1], body="nobody has booked saturday",
        established_by="debrief", now_ms=BEFORE_MS + 5 * HOUR, anchor_date=20260702,
    )
    # THE REMINDER TIER, one row per shape the as-of rule has to separate.
    # Four of the five must NOT reach the sandbox, and each is excluded for a
    # different reason — a single pending row would pass any of them.
    rows = SqlReminderStore(engine)
    await rows.create(_reminder(
        OUTSTANDING, "ask whether friday holds",
        created=BEFORE_MS - 6 * HOUR, due=BEFORE_MS + 2 * HOUR,
    ))
    resolved_before = _reminder(
        FIRED_BEFORE, "remind them about the deposit",
        created=BEFORE_MS - 8 * HOUR, due=BEFORE_MS - 5 * HOUR,
    )
    await rows.create(resolved_before)
    await rows.mark(
        resolved_before.id, ReminderState.DONE,
        fired_at=_at(BEFORE_MS - 5 * HOUR), outcome="sent",
    )
    fired_after = _reminder(
        FIRED_AFTER, "chase the booking",
        created=BEFORE_MS - 7 * HOUR, due=BEFORE_MS + 30 * MINUTE,
    )
    await rows.create(fired_after)
    await rows.mark(
        fired_after.id, ReminderState.DONE,
        fired_at=_at(BEFORE_MS + 3 * HOUR), outcome="sent",
    )
    await rows.create(_reminder(
        CREATED_AFTER, "book saturday instead",
        created=BEFORE_MS + HOUR, due=BEFORE_MS + 6 * HOUR,
    ))
    cancelled = _reminder(
        CANCELLED, "the one whose cancellation has no clock",
        created=BEFORE_MS - 9 * HOUR, due=BEFORE_MS + 4 * HOUR,
    )
    await rows.create(cancelled)
    await rows.cancel(cancelled.id, conversation_id=ROOM[1])
    return src


@pytest.fixture
async def sandbox(source: Source, pg_template: str, tmp_path: Any) -> AsyncIterator[Subject]:
    """A subject composed for the SAME real room, in a database of its own,
    with its clock frozen at the moment."""
    async with (
        sandbox_database(source.url, template=pg_template) as url,
        offline_subject(SubjectSpec(
            database_url=url, workdir=tmp_path / "sandbox", room=source.room,
            policy=source.policy, identity=source.identity,
            now=datetime.fromtimestamp(BEFORE_MS / 1000, tz=UTC),
        )) as subject,
    ):
        yield subject


async def _seed(subject: Subject, source: Source) -> Any:
    return await sandbox_from_snapshot(subject, source_url=source.url, before_ms=BEFORE_MS)


async def _rows(subject: Subject) -> list[Any]:
    return await subject.deps.audit.recent(conversation_id=subject.room[1], limit=50)


# --- the copy, at the moment's own clock -------------------------------------


async def test_the_sandbox_holds_the_transcript_as_it_stood_then(sandbox, source) -> None:
    """The three rows at or before the instant, in order, and not the fourth."""
    await _seed(sandbox, source)
    assert [r.body for r in await _rows(sandbox)] == [
        "are we still on for friday", "i can't make it", "want me to move it?",
    ]


async def test_rows_keep_their_real_timestamps_and_the_clock_is_the_moment(sandbox, source) -> None:
    """Nothing is shifted: a row keeps its `ts`, and the subject's clock reads
    `before_ms`, so the age the model sees is the age the row had then."""
    await _seed(sandbox, source)
    assert [r.ts for r in await _rows(sandbox)] == [
        BEFORE_MS - 10 * MINUTE, BEFORE_MS - 4 * MINUTE, BEFORE_MS - MINUTE,
    ]
    assert int(sandbox.deps.clock().timestamp() * 1000) == BEFORE_MS


async def test_her_own_rows_come_back_as_hers(sandbox, source) -> None:
    await _seed(sandbox, source)
    assert [r.is_nora for r in await _rows(sandbox)] == [False, False, True]


async def test_a_sender_the_identity_did_not_know_stays_unknown(sandbox, source) -> None:
    """A null `person_id` is a real fact about a real row and must survive as
    one. Seeding it as the default person would put a stranger's words in a
    member's mouth."""
    await _seed(sandbox, source)
    assert [r.person_id for r in await _rows(sandbox)] == ["miles_novak", None, "miles_novak"]


async def test_the_returned_map_translates_a_source_row_id_into_the_copy(
    sandbox, source, engine,
) -> None:
    """`Sandboxed.rows` is keyed by the SOURCE row id, so a case can cite the
    message that really started the incident and be graded against the row the
    sandbox's store assigned. `Sandboxed.transcript` is the window itself."""
    seeded = await _seed(sandbox, source)
    async with engine.connect() as conn:
        ids = dict((await conn.execute(
            sa.select(MESSAGES.c.body, MESSAGES.c.id).where(MESSAGES.c.conversation_id == ROOM[1])
        )).all())
    by_id = {r.id: r.body for r in await _rows(sandbox)}

    assert by_id[seeded.rows[ids["are we still on for friday"]]] == "are we still on for friday"
    assert ids["moved it to saturday"] not in seeded.rows
    assert seeded.now_ms == BEFORE_MS
    assert [re.sub(r"<[^>]*>", "", line) for line in seeded.transcript] == [
        "are we still on for friday", "i can't make it", "want me to move it?",
    ]


async def test_the_judges_source_names_every_speaker_and_marks_hers(sandbox, source) -> None:
    """The judge grades «X said Y» against `Sandboxed.transcript`, so
    each line carries the speaker SHE was shown — the identity's name for a
    member, the profile name for a sender it does not know, her display name
    on her own row — and no account id, the source's or the sandbox's minted
    one, because the judge is a third-party call."""
    seeded = await _seed(sandbox, source)
    assert seeded.transcript == (
        '<m n="1" age="10m ago" from="Miles" author_id="miles_novak" '
        'at="2025-07-08T18:30:00+00:00">are we still on for friday</m>',
        '<m n="2" age="4m ago" from="Miles" author_id="unknown" '
        'at="2025-07-08T18:36:00+00:00">i can\'t make it</m>',
        '<m n="3" age="just now" from="Nora" author_id="nora" '
        'at="2025-07-08T18:39:00+00:00" delivery="not delivered">want me to move it?</m>',
    )
    joined = "\n".join(seeded.transcript)
    sender_ids = {r.sender_id for r in await _rows(sandbox)}
    assert sender_ids  # the minted ids exist, and none of them is rendered
    for raw in (*sender_ids, "+88387175396", "+852"):
        assert raw not in joined


def _culture_head(key: str) -> sa.Select[tuple[str]]:
    return sa.select(CULTURE_REVISION.c.body).join(
        CULTURE, CULTURE.c.head_revision_id == CULTURE_REVISION.c.id,
    ).where(CULTURE.c.key == key)


# --- the source is untouched -------------------------------------------------


async def _pending(subject: Subject) -> list[Any]:
    from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION

    intentions, edges, claims = INTENTION.c, EDGE.c, CLAIM.c
    room = f"{subject.room[0]}:{subject.room[1]}"
    engine = create_sql_engine(subject.database_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(sa.select(
                claims.content["text"].astext.label("text"), intentions.state,
                intentions.trigger["at"].astext.label("due_at"), intentions.owed_to,
            ).select_from(INTENTION.join(
                EDGE, (edges.unit == intentions.unit) & (edges.src_id == intentions.id),
            ).join(
                CLAIM, (claims.unit == edges.unit) & (claims.id == edges.dst_id),
            )).where(
                intentions.unit == subject.unit, intentions.origin_room == room,
                intentions.state == "open", edges.type == "rests_on",
            ).order_by(intentions.trigger["at"].astext, intentions.id))).all())
    finally:
        await engine.dispose()


async def test_the_sandbox_holds_the_reminders_the_room_was_waiting_on(
    sandbox, source,
) -> None:
    await _seed(sandbox, source)
    assert [r.text for r in await _pending(sandbox)] == [
        "chase the booking", "ask whether friday holds",
    ]
    engine = create_sql_engine(sandbox.database_url)
    try:
        assert not await SqlReminderStore(engine).all_pending(limit=50)
    finally:
        await engine.dispose()


async def test_a_reminder_resolved_after_the_instant_comes_back_pending(
    sandbox, source,
) -> None:
    """THE GATING TEST, and the one a reconstruction that reads today's row
    fails. `chase the booking` is `DONE` today with a stamp and an outcome;
    at the instant it had not fired, so all three must be as-of."""
    await _seed(sandbox, source)
    [chased] = [r for r in await _pending(sandbox) if r.text == "chase the booking"]
    assert chased.state == "open"


async def test_a_reminder_already_fired_by_then_is_not_in_the_queue(sandbox, source) -> None:
    """It was not something the room was waiting on, so it is not seeded —
    the same rule that keeps a note retired before the instant out."""
    await _seed(sandbox, source)
    assert "remind them about the deposit" not in {r.text for r in await _pending(sandbox)}


async def test_a_reminder_written_after_the_instant_is_not_there(sandbox, source) -> None:
    await _seed(sandbox, source)
    assert "book saturday instead" not in {r.text for r in await _pending(sandbox)}


async def test_a_cancelled_reminder_is_omitted_rather_than_guessed(sandbox, source) -> None:
    """`cancel()` records no instant, so «cancelled» carries no when — see
    `snapshot.py`'s module docstring. It is left OUT rather than seeded
    pending, because a pending row is what fires and reconstructing one the
    room may not have had is the dangerous direction."""
    await _seed(sandbox, source)
    assert "the one whose cancellation has no clock" not in {
        r.text for r in await _pending(sandbox)
    }


async def test_the_reminder_keeps_the_due_time_it_really_had(sandbox, source) -> None:
    """The clock is frozen at the instant and nothing is shifted, so the copy's
    `due_at` is the real one to the second."""
    await _seed(sandbox, source)
    [friday] = [r for r in await _pending(sandbox) if r.text == "ask whether friday holds"]
    assert datetime.fromisoformat(friday.due_at) == _at(BEFORE_MS + 2 * HOUR)
    assert friday.owed_to == "miles_novak"


async def test_the_source_is_not_written_to(sandbox, source, engine) -> None:
    """Every tier of the source must be exactly what the fixture left: the
    copy went into the sandbox, and only there."""
    async def tiers() -> tuple:
        async with engine.connect() as conn:
            bodies = tuple((await conn.execute(
                sa.select(MESSAGES.c.body).where(MESSAGES.c.conversation_id == ROOM[1])
                .order_by(MESSAGES.c.ts.asc(), MESSAGES.c.id.asc())
            )).scalars().all())
            head = (await conn.execute(_culture_head(ROOM[1]))).one()
        standing = await SqlStandingStore(engine).read(platform=ROOM[0], conversation_id=ROOM[1])
        notes = await SqlNoteStore(engine).standing(unit="nora")
        # Reminders too: `_reminders` is the newest read and the only one
        # whose rows the reconstruction also WRITES elsewhere, so a stray
        # `mark`/`cancel` leaking back here is exactly what this catches.
        reminders = await SqlReminderStore(engine).all_pending(limit=50)
        return (
            bodies, standing.body if standing else None, head.body,
            tuple(sorted((n.title, n.head_revision_id) for n in notes)),
            tuple(sorted((r.text, r.state.value) for r in reminders)),
        )

    before = await tiers()
    await _seed(sandbox, source)
    assert await tiers() == before
    assert before[0] == (
        "are we still on for friday", "i can't make it", "want me to move it?",
        "moved it to saturday",
    )


# --- refusals ----------------------------------------------------------------


async def test_a_source_that_is_the_subjects_own_database_is_refused(sandbox) -> None:
    """Reading the room out of the store it is about to be written into would
    seed the copy on top of itself."""
    with pytest.raises(ValueError, match="names the subject's own database"):
        await sandbox_from_snapshot(sandbox, source_url=sandbox.database_url, before_ms=BEFORE_MS)


async def test_a_subject_whose_clock_is_not_the_moment_is_refused(
    source, pg_template, tmp_path,
) -> None:
    """A window seeded at the moment's clock but read at the wall clock is a
    room that has said nothing for however long ago the moment was."""
    async with (
        sandbox_database(source.url, template=pg_template) as url,
        offline_subject(SubjectSpec(
            database_url=url, workdir=tmp_path / "wall", room=source.room,
            policy=source.policy, identity=source.identity, now=None,
        )) as subject,
    ):
        with pytest.raises(ValueError, match="the subject's clock reads"):
            await sandbox_from_snapshot(subject, source_url=source.url, before_ms=BEFORE_MS)


async def test_a_moment_before_the_first_row_is_refused(source, pg_template, tmp_path) -> None:
    """An empty reconstruction is what a mistyped id reads as too."""
    early = BEFORE_MS - 11 * MINUTE
    async with (
        sandbox_database(source.url, template=pg_template) as url,
        offline_subject(SubjectSpec(
            database_url=url, workdir=tmp_path / "early", room=source.room,
            policy=source.policy, identity=source.identity,
            now=datetime.fromtimestamp(early / 1000, tz=UTC),
        )) as subject,
    ):
        with pytest.raises(ValueError, match="holds no row"):
            await sandbox_from_snapshot(subject, source_url=source.url, before_ms=early)


# --- the sandbox database itself ---------------------------------------------


def _databases(pg_admin: Any) -> set[str]:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        return {row[0] for row in conn.execute("SELECT datname FROM pg_database")}


def _revision(pg_admin: Any, dbname: str) -> str:
    with psycopg.connect(pg_admin.dsn(dbname), autocommit=True) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


async def test_the_sandbox_database_exists_at_head_and_is_dropped_afterwards(
    clean_pg_url, pg_template, pg_admin,
) -> None:
    """Isolation by database is only isolation if the database is real, is the
    schema production runs, and goes away."""
    from sqlalchemy.engine import make_url

    async with sandbox_database(clean_pg_url, template=pg_template) as url:
        name = make_url(url).database
        assert name and name != make_url(clean_pg_url).database
        assert name in _databases(pg_admin)
        assert _revision(pg_admin, name) == _revision(pg_admin, pg_template)
        # Reachable through the URL it handed out — with the password in it.
        eng = create_sql_engine(url)
        try:
            async with eng.connect() as conn:
                assert (await conn.execute(sa.text("SELECT 1"))).scalar() == 1
        finally:
            await eng.dispose()
    assert name not in _databases(pg_admin)


async def test_the_subject_seeds_distinct_open_intentions(sandbox) -> None:
    two_notes = Knowledge(reminders=(
        ReminderSeed(
            text="ask about the dinner plan", due_in_seconds=3600.0,
            requester_person_id="nora", kind="proactive",
            payload={"note_id": "note-x", "note_title": "Dinner plan"},
        ),
        ReminderSeed(
            text="ask about the pickup", due_in_seconds=7200.0,
            requester_person_id="nora", kind="proactive",
            payload={"note_id": "note-y", "note_title": "Pickup before Saturday"},
        ),
    ))
    written = await know(sandbox, two_notes, now_ms=BEFORE_MS)
    assert written.now_ms == BEFORE_MS
    assert {r.text for r in await _pending(sandbox)} == {
        "ask about the dinner plan", "ask about the pickup",
    }


# --- a note's SOURCE id, end to end -----------------------------------------


# --- an OLDER row_history image missing a newer column must restore to SQL
#     NULL, not error or misbehave -----------------------------------------


async def test_a_row_history_image_missing_the_new_hidden_at_key_restores_as_null(
    clean_pg_url: str, pg_template: str, engine: Any,
) -> None:
    """`messages.hidden_at` is nullable with no default, and existing
    `row_history` images were not rewritten to carry the key. So an OLDER
    version, recorded before the column existed, has an `image` JSONB blob with
    no `hidden_at` key at all.

    This proves `nora.infra.sql.snapshot_at.seed` (via `nora.infra.sql.
    history.as_of` and `_insert_sql`'s `jsonb_populate_recordset`) restores
    such a row with `hidden_at` as SQL NULL rather than erroring or
    defaulting it to something else — `hidden_at` is nullable with no
    default, so `_insert_sql` emits the column bare (`r."hidden_at"`, no
    `coalesce`), and `jsonb_populate_recordset` fills a JSON object's
    missing key with NULL for a composite/record type by Postgres's own
    contract.

    The message is seeded through the real write path (`SqlRecordStore`, so
    `row_history` gets a genuine trigger-written image with `hidden_at`
    present), then the `hidden_at` key is stripped from that version's
    `image` directly with the jsonb `-` operator — simulating a
    version recorded before the column existed, never routed back through the
    `messages` table itself.
    """
    message_id = await _say(engine, "a message from before the withdrawal migration", at=BEFORE_MS)

    async with engine.begin() as conn:
        result = await conn.execute(sa.text(
            "UPDATE row_history SET image = image - 'hidden_at' "
            "WHERE table_name = 'messages' AND row_key = :key "
            "AND superseded_at IS NULL RETURNING image"
        ), {"key": f'{{"id": {message_id}}}'})
        [(image,)] = result.all()
        assert "hidden_at" not in image, "the fixture must simulate a pre-0075 image"

    at = datetime.now(UTC)
    async with sandbox_as_of(clean_pg_url, at, template=pg_template) as (sandbox_url, report):
        assert report.copied.get("messages", 0) >= 1
        sandbox_engine = create_sql_engine(sandbox_url)
        try:
            async with sandbox_engine.connect() as conn:
                row = (await conn.execute(sa.text(
                    "SELECT body, hidden_at FROM messages WHERE id = :id"
                ), {"id": message_id})).one()
        finally:
            await sandbox_engine.dispose()

    assert row.body == "a message from before the withdrawal migration"
    assert row.hidden_at is None


# --- a NOT NULL column whose backfill default was DROPPED -------------------


async def test_an_image_from_before_a_dropped_backfill_restores_the_backfill(
    clean_pg_url: str, pg_template: str, engine: Any,
) -> None:
    """`compute_jobs.kind` and `.payload` are NOT NULL, added with a backfill
    default (`legacy`, `{}`) that was then DROPPED. An image recorded before
    that holds neither key, and the target column has no default to read back,
    so the restore must fill the migration's own backfill
    (`snapshot_at.BACKFILLED`) or the insert is refused and every case on a full
    restore ERRORs.

    The row is written through SQL (its trigger records a genuine image), then
    both keys are stripped from that image, simulating an older version.
    """
    job_id = "00000000-0000-0000-0000-000000000094"
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO compute_jobs (id, backend_job_id, task, platform, conversation_id, "
            "conversation_type, requester_person_id, kind, payload) VALUES "
            "(:id, 'job_x', 'a task from before 0094', 'signal', :cid, 'group', 'miles_novak', "
            "'report', '{\"x\": 1}'::jsonb)"
        ), {"id": job_id, "cid": ROOM[1]})
        result = await conn.execute(sa.text(
            "UPDATE row_history SET image = image - 'kind' - 'payload' "
            "WHERE table_name = 'compute_jobs' AND superseded_at IS NULL "
            "AND image->>'id' = :id RETURNING image"
        ), {"id": job_id})
        [(image,)] = result.all()
        assert "kind" not in image and "payload" not in image, (
            "the fixture must simulate a pre-0094 image"
        )

    at = datetime.now(UTC)
    async with sandbox_as_of(clean_pg_url, at, template=pg_template) as (sandbox_url, report):
        assert report.copied.get("compute_jobs", 0) >= 1
        sandbox_engine = create_sql_engine(sandbox_url)
        try:
            async with sandbox_engine.connect() as conn:
                row = (await conn.execute(sa.text(
                    "SELECT task, kind, payload FROM compute_jobs WHERE id = :id"
                ), {"id": job_id})).one()
        finally:
            await sandbox_engine.dispose()

    assert row.task == "a task from before 0094"
    assert row.kind == "legacy"
    assert row.payload == {}
