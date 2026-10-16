"""`nora.infra.sql.snapshot` — one room, one instant, four tiers.

Every test here seeds through the REAL store classes production writes with —
`SqlRecordStore`, `SqlStandingStore`, `SqlCultureStore`, `SqlNoteStore` — and
never by hand-crafted INSERT. A reconstruction tested against rows a test wrote
its own way measures the test's idea of the schema, not production's.

**The shape of every test is the same and it is deliberate**: seed at least two
states of the same tier at two different instants, then read at a moment
BETWEEN them and assert the middle one comes back. A snapshot that ignored
`before_ms` entirely passes any single-state test; only the middle read can
tell «reconstructed» from «returned whatever is there now».

The database is MODULE-scoped per `test_culture_store.py`'s pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.domain.notes import NoteSupersedeReason
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.snapshot import snapshot
from nora.infra.sql.standing import SqlStandingStore

T0 = 1_757_000_000_000
MINUTE = 60_000

PLATFORM = "signal"
CONV = "group-one"
OTHER_CONV = "group-two"
#: The culture tier keys the conversation's `primary_id` and the note tier keys
#: the social unit — neither is `(platform, conversation_id)`, and the snapshot
#: reader refuses to guess either. These are what a `policy.toml` entry for this
#: room would resolve to.
CULTURE_KEY = CONV
UNIT = "friends"


@pytest.fixture(scope="module")
def _snapshot_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_snapshot_" + uuid4().hex
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
async def engine(_snapshot_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_snapshot_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate messages, standing, standing_revision, culture, "
                "culture_revision, note, note_revision cascade"
            ))
        await eng.dispose()


# --- seeding, through the stores production uses -----------------------------


async def _say(engine: Any, body: str, *, at: int,
               conversation_id: str = CONV, platform: str = PLATFORM,
               is_nora: bool = False) -> int:
    return await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            platform=platform, conversation_id=conversation_id,
            conversation_type="group", sender_id="+852111",
            sender_name="Miles", timestamp=at, body=body, raw={},
        ),
        is_nora=is_nora,
    )


async def _fold(engine: Any, body: str, *, at: int, fold_count: int) -> None:
    await SqlStandingStore(engine).write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body=body,
        fold_count=fold_count, updated_at=at,
    ))


async def _culture(engine: Any, body: str, *, at: int) -> None:
    await SqlCultureStore(engine).append(
        key=CULTURE_KEY, body=body, written_at=at, established_by="culture",
    )


async def _note(engine: Any, title: str, body: str, *, at: int) -> Any:
    return await SqlNoteStore(engine).create(
        unit=UNIT, title=title, description=f"about {title}",
        uncertainty="open", closing_condition="the room settles it",
        established_in=CONV, body=body, established_by="debrief",
        now_ms=at, anchor_date=20260901,
    )


async def _revise(engine: Any, note: Any, body: str, *, at: int) -> Any:
    return await SqlNoteStore(engine).append_revision(
        unit=UNIT, note_id=note.id, body=body,
        parent_revision_id=note.head_revision_id,
        established_by="debrief", now_ms=at,
    )


async def _at(engine: Any, when: int, **kwargs: Any) -> Any:
    return await snapshot(
        engine, platform=PLATFORM, conversation_id=CONV,
        culture_key=CULTURE_KEY, unit=UNIT, before_ms=when, **kwargs,
    )


# --- the empty room ----------------------------------------------------------


async def test_a_room_with_nothing_in_it_snapshots_to_five_empties(
    engine: Any
) -> None:
    """Every tier's «nothing here» is its own value, and none of them is an
    error. `None` for the two heads, `()` for the three collections — a reader
    building a case can tell «she held no position» apart from «this room does
    not exist», because neither is expressible as the other."""
    seen = await _at(engine, T0)
    assert seen.transcript == ()
    assert seen.standing is None
    assert seen.culture is None
    assert seen.notes == ()
    assert seen.reminders == ()


async def test_the_snapshot_says_what_it_is_a_snapshot_of(engine: Any) -> None:
    """The three keys come back on the result. Two snapshots of the same room
    minutes apart are otherwise indistinguishable once they leave the call that
    produced them, and a case built from one could not name its own moment."""
    seen = await _at(engine, T0 + 5 * MINUTE)
    assert (seen.platform, seen.conversation_id) == (PLATFORM, CONV)
    assert seen.before_ms == T0 + 5 * MINUTE


# --- the transcript ----------------------------------------------------------


async def test_the_transcript_stops_at_the_instant(engine: Any) -> None:
    """Rows after `before_ms` are not «not yet rendered», they are things
    nobody had said. A reconstruction that included them would be measuring her
    against a room that already knew how the conversation ended."""
    await _say(engine, "before one", at=T0)
    await _say(engine, "before two", at=T0 + MINUTE)
    await _say(engine, "AFTER", at=T0 + 3 * MINUTE)

    seen = await _at(engine, T0 + 2 * MINUTE)
    assert [r.body for r in seen.transcript] == ["before one", "before two"]


async def test_the_transcript_is_oldest_first(engine: Any) -> None:
    """`RecordStore.recent`'s order, because that is what every renderer already
    expects; a reversed transcript reads to a model as a room talking
    backwards."""
    await _say(engine, "first", at=T0)
    await _say(engine, "second", at=T0 + MINUTE)
    await _say(engine, "third", at=T0 + 2 * MINUTE)

    seen = await _at(engine, T0 + 10 * MINUTE)
    assert [r.body for r in seen.transcript] == ["first", "second", "third"]


async def test_the_transcript_includes_her_own_speech(engine: Any) -> None:
    """The Dreamer's `since` excludes `is_nora` rows because it derives
    EVIDENCE and must not read her words back as the room's. This is not
    evidence, it is the transcript, and she was in it — a reconstruction that
    dropped her half would show a room talking to nobody."""
    await _say(engine, "member says", at=T0)
    await _say(engine, "she answers", at=T0 + MINUTE, is_nora=True)

    seen = await _at(engine, T0 + 10 * MINUTE)
    assert [(r.body, r.is_nora) for r in seen.transcript] == [
        ("member says", False), ("she answers", True),
    ]


async def test_another_room_is_not_in_this_room_s_transcript(
    engine: Any
) -> None:
    await _say(engine, "ours", at=T0)
    await _say(engine, "theirs", at=T0, conversation_id=OTHER_CONV)

    seen = await _at(engine, T0 + 10 * MINUTE)
    assert [r.body for r in seen.transcript] == ["ours"]


async def test_the_same_id_on_another_platform_is_a_different_room(
    engine: Any
) -> None:
    """`SqlRecordStore.recent` filters on `conversation_id` ALONE — correctly,
    because it is reached from a turn that already resolved one room. This is
    reached from a caller naming a room by the key `standing` and every lock in
    this repo use, so it filters on both: answering with a Discord room's
    messages because a Signal room happens to share an id would reconstruct
    somewhere else entirely, and every other field of the snapshot would still
    be this room's.

    Falsified by dropping the platform predicate from `_transcript`: both rows
    come back.
    """
    await _say(engine, "signal side", at=T0)
    await _say(engine, "discord side", at=T0, platform="discord")

    seen = await _at(engine, T0 + 10 * MINUTE)
    assert [r.body for r in seen.transcript] == ["signal side"]


async def test_a_limit_keeps_the_newest_rows_still_oldest_first(
    engine: Any
) -> None:
    """`recent`'s semantics: the rows to keep are the NEWEST `limit`, because
    those are the ones still live at `before_ms`. A cut applied after an
    ascending read would keep the oldest and silently drop exactly the part of
    the room the instant is about."""
    for n in range(5):
        await _say(engine, f"m{n}", at=T0 + n * MINUTE)

    seen = await _at(engine, T0 + 10 * MINUTE, transcript_limit=2)
    assert [r.body for r in seen.transcript] == ["m3", "m4"]


# --- standing ----------------------------------------------------------------


async def test_standing_is_the_position_that_stood_at_the_instant(
    engine: Any
) -> None:
    """Two folds, and a moment between them gets the FIRST — which `standing`
    alone cannot answer, because the second fold overwrote it in place."""
    await _fold(engine, "they are picking a restaurant", at=T0, fold_count=1)
    await _fold(engine, "they picked one and left", at=T0 + 5 * MINUTE,
                fold_count=2)

    seen = await _at(engine, T0 + 2 * MINUTE)
    assert seen.standing is not None
    assert seen.standing.body == "they are picking a restaurant"
    assert seen.standing.fold_count == 1


async def test_standing_before_the_first_fold_is_none(engine: Any) -> None:
    """The cold start — the same meaning `last_folded_row_id is null` carries.
    Not an empty position, because «she held nothing here» and «she held an
    empty thing» are different inputs to a case."""
    await _fold(engine, "a position", at=T0 + 5 * MINUTE, fold_count=1)
    seen = await _at(engine, T0)
    assert seen.standing is None


# --- culture -----------------------------------------------------------------


async def test_culture_is_the_account_current_at_the_instant(
    engine: Any
) -> None:
    """Two accounts, and the moment between them gets the first. Read by
    `written_at` rather than by walking back from `culture.head_revision_id`:
    the head is TODAY's, and walking from it lands on the right row only when
    the chain happens to be ordered by time, which `SqlCultureStore.chain`'s own
    docstring says must not be assumed."""
    await _culture(engine, "they talk in fragments", at=T0)
    await _culture(engine, "they talk in fragments, and in Cantonese",
                   at=T0 + 5 * MINUTE)

    seen = await _at(engine, T0 + 2 * MINUTE)
    assert seen.culture is not None
    assert seen.culture.body == "they talk in fragments"
    assert seen.culture.written_at == T0


async def test_culture_before_the_first_account_is_none(engine: Any) -> None:
    await _culture(engine, "an account", at=T0 + 5 * MINUTE)
    seen = await _at(engine, T0)
    assert seen.culture is None


async def test_culture_is_keyed_on_the_key_it_was_given(engine: Any) -> None:
    """The key is `policy.toml`'s, not the room's id, and the reader will not
    guess it. Asserted from the failing side because a wrong key does not
    raise — it matches zero rows and reads exactly like a room that had no
    culture, which is the silent failure `0055_culture` spends a paragraph
    on."""
    await _culture(engine, "an account", at=T0)
    elsewhere = await snapshot(
        engine, platform=PLATFORM, conversation_id=CONV,
        culture_key="some-other-scope", unit=UNIT, before_ms=T0 + MINUTE,
    )
    assert elsewhere.culture is None


# --- notes -------------------------------------------------------------------


async def test_a_note_shows_the_body_it_carried_then(engine: Any) -> None:
    """**The single easiest way to build a wrong snapshot** is to resolve
    `note.head_revision_id`, which points at today's head. Two revisions, and
    the moment between them must show the first."""
    note = await _note(engine, "dinner", "they are thinking about dinner", at=T0)
    await _revise(engine, note, "dinner is booked for Friday",
                  at=T0 + 5 * MINUTE)

    seen = await _at(engine, T0 + 2 * MINUTE)
    assert len(seen.notes) == 1
    note_row, revision = seen.notes[0]
    assert note_row.title == "dinner"
    assert revision.body == "they are thinking about dinner"


async def test_a_note_created_after_the_instant_is_not_in_the_notebook(
    engine: Any
) -> None:
    """However standing it is now. The notebook is what she was LOOKING at.

    **Two predicates enforce this and neither is individually pinned.**
    `_notes` filters on `created_at <= before_ms` AND takes the newest revision
    at or before the instant, and `SqlNoteStore.create` writes a note and its
    first revision in one transaction with one `now_ms`, so each alone excludes
    this note: removing either leaves this test green, and only removing BOTH
    turns it red. `nora.infra.sql.snapshot._notes` carries why they are both kept.
    """
    await _note(engine, "later", "written afterwards", at=T0 + 5 * MINUTE)
    seen = await _at(engine, T0 + MINUTE)
    assert seen.notes == ()


async def test_a_note_retired_since_is_still_in_the_notebook(
    engine: Any
) -> None:
    """The commonest way a reconstruction quietly under-reports.
    `SqlNoteStore.standing` asks `retired_at is null`, which is TODAY's answer;
    a note retired after the instant was in the notebook at the instant, and
    dropping it shows her a smaller notebook than she had.

    Falsified by replacing the as-of visibility predicate with
    `retired_at is null`: this goes empty.
    """
    note = await _note(engine, "dinner", "they are thinking about dinner", at=T0)
    await SqlNoteStore(engine).supersede(
        key=UNIT, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None,
        now_ms=T0 + 5 * MINUTE,
    )

    seen = await _at(engine, T0 + 2 * MINUTE)
    assert [n.title for n, _ in seen.notes] == ["dinner"]

    # And after the retirement it is gone — the positive control, without which
    # the assertion above is also satisfied by a reader that ignores retirement
    # entirely.
    later = await _at(engine, T0 + 10 * MINUTE)
    assert later.notes == ()


async def test_a_note_retired_since_reports_itself_as_not_yet_retired(
    engine: Any
) -> None:
    """**Keeping the note is half the job; the other half is what it SAYS.**

    `note` has no history table, so the row `_notes` builds from is today's.
    The visibility predicate correctly keeps a note retired after the instant,
    but returning it carrying `retired_at`, `retire_reason` and `replaced_by`
    would report a retirement that had not happened yet: a note the snapshot
    itself says was OPEN, reporting the moment and the reason it was closed.
    `Note.is_standing` reads `False` on it, so a caller asking the domain type
    the obvious question gets the future's answer.

    Three assertions, and the third is the one that makes the other two mean
    something:

      1. the reconstructed note reports no retirement;
      2. `is_standing` — the property every consumer actually reads — is True;
      3. the DATABASE row does carry the retirement, read directly. Without
         that control, a store that simply never wrote the columns would pass
         1 and 2, and this test would be pinning nothing.

    Falsified by returning `_row_to_note(note_row)` un-gated: 1 and 2 go red
    while 3 stays green, which is the defect exactly.
    """
    note = await _note(engine, "dinner", "they are thinking about dinner", at=T0)
    successor = await _note(engine, "supper", "supper instead", at=T0)
    await SqlNoteStore(engine).supersede(
        key=UNIT, target=note.id, replaced_by=successor.id,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None,
        now_ms=T0 + 5 * MINUTE,
    )

    seen = await _at(engine, T0 + 2 * MINUTE)
    reconstructed = {n.title: n for n, _ in seen.notes}
    assert set(reconstructed) == {"dinner", "supper"}

    dinner = reconstructed["dinner"]
    assert dinner.retired_at is None
    assert dinner.replaced_by is None
    assert dinner.retire_reason is None
    assert dinner.is_standing is True

    # The control: the retirement IS in the database, with all three columns
    # set — so the three `None`s above are this module gating them, not the
    # store having failed to write them.
    async with engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "select retired_at, replaced_by, retire_reason "
            "from note where id = :i"
        ), {"i": note.id})).one()
    assert row.retired_at == T0 + 5 * MINUTE
    # `str(...)` because a raw `sa.text` read hands back asyncpg's own `UUID`
    # object rather than the string the typed column mapping produces.
    assert str(row.replaced_by) == successor.id
    assert row.retire_reason == NoteSupersedeReason.RETIRED_CLOSED


async def test_another_unit_s_notes_are_not_in_this_notebook(
    engine: Any
) -> None:
    """R-49's boundary, on the reconstruction as well as on the turn. The unit
    is `policy.toml`'s and is a required argument for the same reason the
    culture key is."""
    await _note(engine, "ours", "our note", at=T0)
    elsewhere = await snapshot(
        engine, platform=PLATFORM, conversation_id=CONV,
        culture_key=CULTURE_KEY, unit="strangers", before_ms=T0 + MINUTE,
    )
    assert elsewhere.notes == ()


async def test_the_notebook_is_oldest_first(engine: Any) -> None:
    """`SqlNoteStore.standing`'s order. The block renders on every tag, and
    re-ordering it reads to the model as the notebook having changed when
    nothing did."""
    await _note(engine, "first", "one", at=T0)
    await _note(engine, "second", "two", at=T0 + MINUTE)
    await _note(engine, "third", "three", at=T0 + 2 * MINUTE)

    seen = await _at(engine, T0 + 10 * MINUTE)
    assert [n.title for n, _ in seen.notes] == ["first", "second", "third"]


# --- all four at once --------------------------------------------------------


async def test_four_tiers_moving_at_different_times_reconstruct_together(
    engine: Any
) -> None:
    """**The whole capability in one case.** Each tier is written twice, at
    different instants, and the read is taken at a moment that falls between
    the two writes of EVERY tier — so each field must come back as its middle
    state. A reader that got `before_ms` right for three tiers and wrong for
    one would pass every single-tier test above and fail here.
    """
    await _say(engine, "early", at=T0)
    await _fold(engine, "early position", at=T0 + MINUTE, fold_count=1)
    await _culture(engine, "early account", at=T0 + 2 * MINUTE)
    note = await _note(engine, "dinner", "early body", at=T0 + 3 * MINUTE)

    cut = T0 + 4 * MINUTE

    await _say(engine, "late", at=T0 + 5 * MINUTE)
    await _fold(engine, "late position", at=T0 + 6 * MINUTE, fold_count=2)
    await _culture(engine, "late account", at=T0 + 7 * MINUTE)
    await _revise(engine, note, "late body", at=T0 + 8 * MINUTE)

    seen = await _at(engine, cut)

    assert [r.body for r in seen.transcript] == ["early"]
    assert seen.standing is not None and seen.standing.body == "early position"
    assert seen.culture is not None and seen.culture.body == "early account"
    assert [rev.body for _, rev in seen.notes] == ["early body"]

    # The positive control the assertions above need: read at the END and every
    # tier must show its LATE state. Without it, a reader that returned the
    # earliest row of everything would pass all four assertions above.
    now = await _at(engine, T0 + 10 * MINUTE)
    assert [r.body for r in now.transcript] == ["early", "late"]
    assert now.standing is not None and now.standing.body == "late position"
    assert now.culture is not None and now.culture.body == "late account"
    assert [rev.body for _, rev in now.notes] == ["late body"]


async def test_the_snapshot_writes_nothing(engine: Any) -> None:
    """READ-ONLY, measured rather than asserted in a docstring. Every table's
    row count is taken before and after, and a reconstruction that wrote —
    a watermark, a miss, an audit row — would move one of them.

    The counts are taken over the tables the reader touches PLUS the ones a
    careless implementation would most plausibly write into, so the check is
    about the database rather than about the four `SELECT`s this version
    happens to issue.
    """
    await _say(engine, "something", at=T0)
    await _fold(engine, "a position", at=T0, fold_count=1)
    await _culture(engine, "an account", at=T0)
    await _note(engine, "dinner", "a body", at=T0)

    tables = (
        "messages", "standing", "standing_revision", "culture",
        "culture_revision", "note", "note_revision", "note_candidate",
        "decision_log", "tag_trace",
    )

    async def counts() -> dict[str, int]:
        async with engine.connect() as conn:
            return {
                t: int((await conn.execute(
                    sa.text(f"select count(*) from {t}")  # noqa: S608 — fixed tuple
                )).scalar_one())
                for t in tables
            }

    before = await counts()
    await _at(engine, T0 + MINUTE)
    assert await counts() == before


# --- the reminder tier -------------------------------------------------------
#
# Read DIRECTLY off `snapshot()`, and that is the point rather than a
# convenience. The sandbox seeds every reminder through `ReminderSeed`, which
# has no `state` field because everything it seeds was outstanding — so the
# as-of restoration below is INVISIBLE through that path and a test written
# there passes with `_as_of_reminder` deleted.


async def _texts(engine: Any, when: int) -> set[str]:
    """The outstanding reminders at `when`, by text.

    Text rather than the whole tuple because `engine` is MODULE-scoped: these
    rows accumulate across the tests below exactly as the module's messages
    and notes do, so each assertion scopes itself to the row it created.
    """
    return {r.text for r in (await _at(engine, when)).reminders}


async def _remind(
    engine: Any, text: str, *, created: int, due: int, conv: str = CONV,
) -> Any:
    row = Reminder(
        id=uuid4(), requester_person_id="miles_novak", platform=PLATFORM,
        conversation_id=conv, conversation_type="group", text=text,
        created_at=_utc(created), due_at=_utc(due),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    await SqlReminderStore(engine).create(row)
    return row


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def test_a_reminder_written_after_the_instant_is_not_in_the_queue(
    engine: Any,
) -> None:
    await _remind(engine, "later", created=T0 + 9 * MINUTE, due=T0 + 90 * MINUTE)
    assert "later" not in await _texts(engine, T0 + 5 * MINUTE)


async def test_a_reminder_outstanding_at_the_instant_is_returned(engine: Any) -> None:
    await _remind(engine, "ask about friday", created=T0 + MINUTE, due=T0 + 90 * MINUTE)
    assert "ask about friday" in await _texts(engine, T0 + 5 * MINUTE)


async def test_a_reminder_resolved_after_the_instant_is_restored_to_pending(
    engine: Any,
) -> None:
    """THE FALSIFICATION for `_as_of_reminder`. The row is `DONE` today with a
    stamp and an outcome; at the instant none of the three had happened, and a
    reconstruction that hands back today's values describes a reminder as
    outstanding while carrying the verdict of a firing still in its future.

    Delete the `replace(...)` in `_as_of_reminder` and this goes red on all
    three fields.
    """
    row = await _remind(engine, "chase it", created=T0 + MINUTE, due=T0 + 2 * MINUTE)
    await SqlReminderStore(engine).mark(
        row.id, ReminderState.DONE, fired_at=_utc(T0 + 8 * MINUTE), outcome="sent",
    )
    [seen] = [
        r for r in (await _at(engine, T0 + 5 * MINUTE)).reminders
        if r.text == "chase it"
    ]
    assert seen.state is ReminderState.PENDING
    assert seen.fired_at is None
    assert seen.outcome is None


async def test_a_reminder_resolved_before_the_instant_is_not_outstanding(
    engine: Any,
) -> None:
    """It was not something the room was waiting on. The same rule that keeps
    a note retired before the instant out of the notebook."""
    row = await _remind(engine, "already done", created=T0 + MINUTE, due=T0 + 2 * MINUTE)
    await SqlReminderStore(engine).mark(
        row.id, ReminderState.DONE, fired_at=_utc(T0 + 3 * MINUTE), outcome="sent",
    )
    assert "already done" not in await _texts(engine, T0 + 5 * MINUTE)


async def test_a_cancelled_reminder_is_omitted_because_its_instant_is_unknowable(
    engine: Any,
) -> None:
    """`cancel()` writes `state` and no timestamp, and the schema says
    `fired_at` is "NULL forever on one that is cancelled" — so a row cancelled
    a month before the instant and one cancelled a minute after are the same
    row today. It is omitted rather than guessed PENDING, because a pending
    row is what FIRES and inventing one is the dangerous direction."""
    row = await _remind(engine, "called off", created=T0 + MINUTE, due=T0 + 90 * MINUTE)
    await SqlReminderStore(engine).cancel(row.id, conversation_id=CONV)
    assert "called off" not in await _texts(engine, T0 + 5 * MINUTE)


async def test_another_rooms_reminder_does_not_leak_in(engine: Any) -> None:
    """Keyed on `(platform, conversation_id)`, the pair this table actually
    carries — unlike the transcript, whose rows have no platform column."""
    await _remind(
        engine, "someone else's", created=T0 + MINUTE, due=T0 + 90 * MINUTE,
        conv=OTHER_CONV,
    )
    assert "someone else's" not in await _texts(engine, T0 + 5 * MINUTE)


async def test_an_edit_folds_only_once_it_had_happened(engine: Any) -> None:
    """The transcript reads fold a message's
    newest edit onto it; the reconstruction must fold only an edit made AT OR
    BEFORE the instant — a message corrected five minutes later still said
    its first text one minute in, and a snapshot that showed the correction
    there would be today's room wearing a past timestamp."""
    import dataclasses

    from nora.domain.models import EditTarget

    row = await _say(engine, "dinner at 7", at=T0)
    await SqlRecordStore(engine).append_edit(
        message_id=row,
        message=dataclasses.replace(
            NormalizedMessage(
                platform=PLATFORM, conversation_id=CONV, conversation_type="group",
                sender_id="+852111", sender_name="Miles", timestamp=T0 + 5 * MINUTE,
                body="dinner at 8", raw={},
            ),
            edit_of=EditTarget(sent_ts=T0),
        ),
    )

    before = await _at(engine, T0 + MINUTE)
    assert [r.body for r in before.transcript] == ["dinner at 7"]
    assert before.transcript[0].edit is None

    after = await _at(engine, T0 + 10 * MINUTE)
    assert [r.body for r in after.transcript] == ["dinner at 8"]
    assert after.transcript[0].edit is not None
    assert after.transcript[0].edit.was == "dinner at 7"


# --- a source BEHIND this checkout's migration head --------------------------
#
# `nora.subject.sandbox.refuse_if_source_diverges_from_head` allows a source
# behind head to run at all (refusing on every routine deploy lag would
# refuse nearly every ordinary run from master). `snapshot()` is the
# fail-closed second layer for the rare migration that actually mattered: a
# read that hits a column or table a NEWER migration added, against a source
# that predates it. Simulated here by dropping a column off an otherwise
# fully migrated, throwaway database — the SAME symptom a genuinely-behind
# source produces, on a schema this suite controls precisely rather than one
# built by checking out an old revision.


@pytest.fixture
async def behind_schema_engine(pg_admin: Any) -> AsyncIterator[Any]:
    """A database at THIS checkout's migration head, minus one column
    `transcript_select` names — dropped and re-added around the test so
    nothing else in the suite sees the missing column, and its own database
    (never `_snapshot_db`, module-scoped and shared) so nothing else in this
    module does either."""
    dbname = "t_snapshot_behind_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    eng = create_sql_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


async def test_snapshot_runs_on_a_source_behind_head_with_a_compatible_schema(
    behind_schema_engine: Any,
) -> None:
    """The positive case, paired with the refusal below: a source whose
    schema this checkout's reads are actually compatible with (every real
    "behind head" case, bar the one migration that adds a column/table a
    read names) runs exactly as a source at head does."""
    await _say(behind_schema_engine, "dinner at 7", at=T0)

    shot = await snapshot(
        behind_schema_engine, platform=PLATFORM, conversation_id=CONV,
        culture_key=CULTURE_KEY, unit=UNIT, before_ms=T0 + MINUTE,
    )

    assert [r.body for r in shot.transcript] == ["dinner at 7"]


async def test_snapshot_fails_closed_with_a_clear_message_when_a_column_is_missing(
    behind_schema_engine: Any,
) -> None:
    from nora.infra.sql.migrations_runner import SourceRevisionMismatchError

    async with behind_schema_engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE messages DROP COLUMN sender_name"))

    with pytest.raises(SourceRevisionMismatchError) as excinfo:
        await snapshot(
            behind_schema_engine, platform=PLATFORM, conversation_id=CONV,
            culture_key=CULTURE_KEY, unit=UNIT, before_ms=T0 + MINUTE,
        )
    message = str(excinfo.value)
    assert "source at" in message
    assert "branch at" in message
    assert "missing column or table" in message
    assert "make eval-source" in message
