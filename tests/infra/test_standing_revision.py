"""`standing_revision` — the chain behind the head, and the as-of read over it.

Single-store file, not a contract suite: `read_as_of` is SQL-only on purpose.
`InMemoryStandingStore` implements the `StandingStore` PORT, and history is not
on that port — the fold path reads the head and writes the head, and giving the
fake a revision table would be building a second implementation of something
with one real consumer. `tests/contract/test_standing_store_contract.py` stays
the gate for everything both implementations do share, and this file covers the
half that is the SQL store's alone.

The database is MODULE-scoped per `test_culture_store.py`'s pattern: one
`CREATE DATABASE` plus one migration chain for the whole file, truncated between
tests.

**What these tests are really about is that the append is not decoration.** A
revision table that exists, is written, and is never read back correctly is
worse than none — it reads as history while answering the wrong instant. So
every test here either writes twice and asks which position a moment between
them saw, or asserts the append happens at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.standing import SqlStandingStore

T0 = 1_757_000_000_000
PLATFORM = "signal"
OTHER_PLATFORM = "discord"
CONV = "g1"
OTHER_CONV = "g2"


@pytest.fixture(scope="module")
def _standing_history_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_standing_hist_" + uuid4().hex
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
async def store(_standing_history_db: str) -> AsyncIterator[SqlStandingStore]:
    engine = create_sql_engine(_standing_history_db)
    try:
        yield SqlStandingStore(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(
                sa.text("truncate standing, standing_revision cascade")
            )
        await engine.dispose()


@pytest.fixture
async def engine(_standing_history_db: str) -> AsyncIterator[Any]:
    """A bare engine for the counting reads — the store deliberately exposes no
    "how many revisions" verb, so the tests that assert the APPEND happened read
    the table directly rather than having a method added for their benefit."""
    eng = create_sql_engine(_standing_history_db)
    try:
        yield eng
    finally:
        await eng.dispose()


def _standing(
    body: str = "They are picking a restaurant.",
    *,
    platform: str = PLATFORM,
    conversation_id: str = CONV,
    fold_count: int = 1,
    updated_at: int = T0,
    last_folded_row_id: int | None = None,
) -> Standing:
    return Standing(
        platform=platform, conversation_id=conversation_id, body=body,
        fold_count=fold_count, updated_at=updated_at,
        last_folded_row_id=last_folded_row_id,
    )


async def _revision_count(engine: Any) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(
            sa.text("select count(*) from standing_revision")
        )).scalar_one())


# --- the append --------------------------------------------------------------


async def test_a_write_leaves_a_revision_behind_it(
    store: SqlStandingStore, engine: Any
) -> None:
    """One fold, one row of history. The whole feature rests on this and
    nothing above it can be true if this is not."""
    await store.write(standing=_standing("the first position"))
    assert await _revision_count(engine) == 1


async def test_three_writes_leave_three_revisions_not_one(
    store: SqlStandingStore, engine: Any
) -> None:
    """**The property this file exists for.** The head is an UPSERT — one row
    per room, revised in place — so a history table wired through the same
    `ON CONFLICT` path, or written with one of its own, would hold exactly one
    row after three folds and every reader would be told the room has always
    held its current position.

    Falsified by removing the append from `SqlStandingStore.write`: this drops
    to zero.
    """
    await store.write(standing=_standing("one", fold_count=1, updated_at=T0))
    await store.write(standing=_standing("two", fold_count=2, updated_at=T0 + 1000))
    await store.write(standing=_standing("three", fold_count=3, updated_at=T0 + 2000))
    assert await _revision_count(engine) == 3
    # And the head is still exactly one row, unchanged in meaning.
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "three"
    assert head.fold_count == 3


async def test_a_refused_write_leaves_no_revision_either(
    store: SqlStandingStore, engine: Any
) -> None:
    """A fold the database refuses advances NEITHER the head nor the history.

    **This does NOT prove the two statements share a transaction.** Both
    tables carry the same body ceiling, so a body over it is refused by
    whichever statement runs first and nothing lands either way — moving the
    append into its own transaction leaves this test green. The atomicity claim
    is the test below; this one pins the narrower and still-real property that
    the revision table enforces the head's own ceiling, so history cannot
    record a position `standing` would refuse.

    `object.__setattr__` bypasses the domain type exactly as the contract
    suite's ceiling tests do: it manufactures the state a writer that went
    around the type would produce, which is the only state the CHECK exists for.
    """
    await store.write(standing=_standing("the prior position"))
    assert await _revision_count(engine) == 1

    smuggled = _standing("x" * 10, updated_at=T0 + 1000)
    object.__setattr__(smuggled, "body", "x" * (STANDING_BODY_CEILING_CHARS + 1))
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError from either CHECK
        await store.write(standing=smuggled)

    assert await _revision_count(engine) == 1
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "the prior position"


async def test_the_head_does_not_move_when_the_append_fails(
    _standing_history_db: str
) -> None:
    """**The atomicity pin.** The head's upsert and the revision's insert are
    one transaction, so a failure on the second rolls the first back.

    The failure has to be INJECTED, and that is a property of the design rather
    than a shortcut: the two tables carry identical CHECKs on purpose, so no
    VALUE exists that one accepts and the other refuses, and the only way the
    second statement fails while the first succeeded is the server or the
    connection going away between them. That is what this simulates — a
    `before_cursor_execute` hook that raises on the `standing_revision` insert
    and on nothing else, which is how a real mid-transaction failure presents.

    Without the shared transaction the head is already committed when the
    append is attempted, so the room's position has moved and nothing records
    what it moved from — the exact state `standing_revision` exists to make
    unrepresentable. Falsified by splitting `write` into two `engine.begin()`
    blocks: this goes red, and `test_a_refused_write_leaves_no_revision_either`
    does not.

    Its own engine, not the `store`/`engine` fixtures, because the hook is
    attached to the engine for the length of this test and must not outlive it
    onto another test's connections.
    """
    eng = create_sql_engine(_standing_history_db)

    class _ConnectionLost(Exception):
        pass

    def _fail_on_the_append(conn, cursor, statement, parameters, context, many):
        if "standing_revision" in statement.lower():
            raise _ConnectionLost("the connection went away mid-transaction")

    try:
        await eng.dispose()  # start from a cold pool so the hook sees every write
        sa.event.listen(eng.sync_engine, "before_cursor_execute", _fail_on_the_append)
        try:
            with pytest.raises(Exception):  # noqa: B017 — wrapped by the driver
                await SqlStandingStore(eng).write(
                    standing=_standing("a position nothing should keep")
                )
        finally:
            sa.event.remove(
                eng.sync_engine, "before_cursor_execute", _fail_on_the_append
            )

        # The positive control the assertion needs: prove the hook actually
        # fired on the statement it was aimed at, by showing an unhooked write
        # of the same value DOES land. Without it, an assertion of "no row" is
        # also satisfied by a write that never ran for some other reason.
        assert await SqlStandingStore(eng).read(
            platform=PLATFORM, conversation_id=CONV
        ) is None
        await SqlStandingStore(eng).write(standing=_standing("and now it lands"))
        landed = await SqlStandingStore(eng).read(
            platform=PLATFORM, conversation_id=CONV
        )
        assert landed is not None and landed.body == "and now it lands"
    finally:
        async with eng.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(
                sa.text("truncate standing, standing_revision cascade")
            )
        await eng.dispose()


async def test_a_revision_carries_every_column_the_head_does(
    store: SqlStandingStore
) -> None:
    """A revision is the head's value, not a summary of it. A column dropped
    from the append reads back as `None` — and on the row-id watermark, `None`
    is a MEANINGFUL value (never folded), so a dropped column would
    reconstruct as a real state rather than as a gap."""
    written = _standing(
        "a complete position", fold_count=7, updated_at=T0,
        last_folded_row_id=4242,
    )
    await store.write(standing=written)
    recovered = await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0,
    )
    assert recovered == written


# --- read_as_of --------------------------------------------------------------


async def test_a_room_with_no_fold_yet_reads_as_none(
    store: SqlStandingStore
) -> None:
    """The cold start. `None` is a first-class state on this tier — the same
    meaning `last_folded_row_id is null` carries — and an as-of read before any
    fold must give it rather than an empty position."""
    assert await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0,
    ) is None


async def test_an_instant_before_the_first_fold_reads_as_none(
    store: SqlStandingStore
) -> None:
    """The room HAS a history; the moment asked about is before it starts. This
    is the case a read that ignored `before_ms` and just took the newest row
    would get wrong, and it would get it wrong silently — returning a real
    position for an instant at which there was none."""
    await store.write(standing=_standing("the first position", updated_at=T0))
    assert await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0 - 1,
    ) is None


async def test_an_intermediate_instant_reads_the_position_that_stood_then(
    store: SqlStandingStore
) -> None:
    """**The reconstruction, in one test.** Three folds, and a moment between
    the second and the third gets the SECOND — not the newest (which is what a
    reader of `standing` itself gets, and the reason this table exists) and not
    the first (which is what an off-by-one on the bound gives).

    Falsified by dropping the `updated_at <= before_ms` predicate: this returns
    "three". Falsified by ordering ascending instead of descending: it returns
    "one".
    """
    await store.write(standing=_standing("one", fold_count=1, updated_at=T0))
    await store.write(standing=_standing("two", fold_count=2, updated_at=T0 + 1000))
    await store.write(standing=_standing("three", fold_count=3, updated_at=T0 + 2000))

    seen = await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0 + 1500,
    )
    assert seen is not None
    assert seen.body == "two"
    assert seen.fold_count == 2
    assert seen.updated_at == T0 + 1000


async def test_the_bound_is_inclusive(store: SqlStandingStore) -> None:
    """A fold landing exactly AT the instant asked about is part of what she
    knew there: `updated_at` is when the position was TAKEN, so an exclusive
    bound would answer with the position this one replaced and call that the
    state at a moment it was no longer the state.

    Stated as its own test because both readings look reasonable and the
    difference only ever shows on the boundary — which is exactly where an eval
    case anchored to a real message's timestamp lands.
    """
    await store.write(standing=_standing("one", updated_at=T0))
    await store.write(standing=_standing("two", updated_at=T0 + 1000))
    at_the_boundary = await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0 + 1000,
    )
    assert at_the_boundary is not None and at_the_boundary.body == "two"


async def test_an_instant_after_the_last_fold_reads_the_last_fold(
    store: SqlStandingStore
) -> None:
    """The positive control for the bound: pushing the instant past the whole
    history must still answer, and answer with the head's value. A read that
    returned `None` here would be filtering the wrong way round, and the
    intermediate test above cannot tell those two apart on its own."""
    await store.write(standing=_standing("one", updated_at=T0))
    await store.write(standing=_standing("two", updated_at=T0 + 1000))
    latest = await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0 + 999_999,
    )
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert latest == head


#: Trials for the tie-break pin below. Ordering by uuid makes each trial an
#: independent coin toss, so twenty-four of them put the chance of a wrong
#: ordering passing this test at one in sixteen million. Low enough that a
#: green run is evidence rather than luck, and cheap enough (two writes and
#: one read each, against a container already up) not to be felt.
_TIE_TRIALS = 24


async def test_two_folds_in_one_millisecond_resolve_to_the_later_fold(
    store: SqlStandingStore
) -> None:
    """**The tie-break is `fold_count`, not `id`.**

    `standing_revision.id` is `gen_random_uuid()`. Ordering
    `updated_at DESC, id DESC` is perfectly deterministic for a fixed set of
    rows — and perfectly uncorrelated with which of two folds sharing a
    millisecond actually happened first, which is the only thing the as-of read
    is asking.

    An eval case anchored to a real message's timestamp lands on exactly this
    boundary, and the wrong answer here is not an error — it is a plausible
    position from the wrong side of the fold, which nothing downstream can
    detect.

    **Repeated rather than run once, because one run cannot tell a fix from a
    coin toss.** Each trial is its own room, so the rows of one do not decide
    another, and every trial must return the higher `fold_count`. Falsified by
    restoring `id DESC` as the tie-break: red within the first few trials, every
    time.
    """
    for trial in range(_TIE_TRIALS):
        room = f"tie-{trial}"
        await store.write(standing=_standing(
            "the position before", conversation_id=room,
            fold_count=1, updated_at=T0,
        ))
        await store.write(standing=_standing(
            "the position after", conversation_id=room,
            fold_count=2, updated_at=T0,
        ))
        seen = await store.read_as_of(
            platform=PLATFORM, conversation_id=room, before_ms=T0,
        )
        assert seen is not None
        assert (seen.body, seen.fold_count) == ("the position after", 2), (
            f"trial {trial}: two folds share updated_at={T0} and the read "
            f"returned fold {seen.fold_count} — the as-of read must break the "
            f"tie on fold_count, not on a random uuid"
        )


async def test_the_as_of_read_is_keyed_on_platform_and_conversation(
    store: SqlStandingStore
) -> None:
    """The room key, on the history as well as the head. The revision table carries
    both columns and no foreign key, so a read that filtered on
    `conversation_id` alone would hand a Discord room's position to a Signal
    room that happens to share an id — the failure the head's own key test
    names, one table over and with nothing to catch it."""
    await store.write(standing=_standing("signal position", updated_at=T0))
    await store.write(standing=_standing(
        "discord position", platform=OTHER_PLATFORM, updated_at=T0,
    ))
    signal = await store.read_as_of(
        platform=PLATFORM, conversation_id=CONV, before_ms=T0,
    )
    discord = await store.read_as_of(
        platform=OTHER_PLATFORM, conversation_id=CONV, before_ms=T0,
    )
    assert signal is not None and signal.body == "signal position"
    assert discord is not None and discord.body == "discord position"


async def test_another_room_on_the_same_platform_has_its_own_history(
    store: SqlStandingStore
) -> None:
    await store.write(standing=_standing("g1 position", updated_at=T0))
    assert await store.read_as_of(
        platform=PLATFORM, conversation_id=OTHER_CONV, before_ms=T0 + 999_999,
    ) is None


# --- source -----------------------------------------------------------------


async def _source(engine: Any, *, conversation_id: str = CONV) -> str:
    async with engine.connect() as conn:
        return str((await conn.execute(sa.text(
            "select source from standing_revision "
            "where conversation_id = :c order by updated_at desc limit 1"
        ), {"c": conversation_id})).scalar_one())


async def test_a_fold_records_source_fold_by_default(
    store: SqlStandingStore, engine: Any
) -> None:
    """The fold writers never pass `source` — this is the value every one of
    their calls produces."""
    await store.write(standing=_standing("the fold's own position"))
    assert await _source(engine) == "fold"


async def test_an_operator_edit_is_recorded_as_operator(
    store: SqlStandingStore, engine: Any
) -> None:
    """The one other writer of this table. `write_if_unchanged` always
    stamps its own append `'operator'`, never taking it as an argument —
    there is no other source an edit through this method could be."""
    await store.write(standing=_standing("the fold's own position", fold_count=1))
    before = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert before is not None

    edited = _standing(
        "the operator's correction", fold_count=before.fold_count,
        updated_at=T0 + 1000, last_folded_row_id=before.last_folded_row_id,
    )
    applied = await store.write_if_unchanged(expected=before, standing=edited)
    assert applied is True
    assert await _source(engine) == "operator"

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "the operator's correction"
    assert head.fold_count == before.fold_count  # unchanged, per the module docstring
    assert head.last_folded_row_id == before.last_folded_row_id  # unchanged too


async def test_write_if_unchanged_refuses_a_standing_that_changes_the_key_or_the_watermark(
    store: SqlStandingStore, engine: Any
) -> None:
    """`write_if_unchanged` is for an operator edit, which changes `body` and
    `updated_at` only. `standing`'s `platform`, `conversation_id`,
    `fold_count` and `last_folded_row_id` are not covered by the WHERE at
    all — they land in `.values()` and in the revision insert, entirely
    uncontrolled by the compare-and-set. Anything else reaching this method
    is a caller bug, and it must be refused loudly (`ValueError`) rather than
    silently accepted."""
    await store.write(standing=_standing("the fold's own position", fold_count=3,
                                         last_folded_row_id=10))
    before = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert before is not None

    mutations = [
        {"platform": OTHER_PLATFORM},
        {"conversation_id": OTHER_CONV},
        {"fold_count": before.fold_count + 1},
        {"last_folded_row_id": (before.last_folded_row_id or 0) + 1},
    ]
    for mutation in mutations:
        bad = _standing(
            "an edit that also tries to move something else",
            platform=mutation.get("platform", before.platform),
            conversation_id=mutation.get("conversation_id", before.conversation_id),
            fold_count=mutation.get("fold_count", before.fold_count),
            updated_at=T0 + 1000,
            last_folded_row_id=mutation.get(
                "last_folded_row_id", before.last_folded_row_id),
        )
        with pytest.raises(ValueError, match="platform, conversation_id, "
                                              "fold_count, last_folded_row_id"):
            await store.write_if_unchanged(expected=before, standing=bad)

    # Nothing landed for any of the four attempts.
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "the fold's own position"
    assert head.fold_count == 3
    assert head.last_folded_row_id == 10
    assert await _revision_count(engine) == 1


async def test_compare_and_set_refuses_a_stale_row(
    store: SqlStandingStore, engine: Any
) -> None:
    """**The property `write_if_unchanged` exists for.** A fold lands
    between the operator's read and their write — simulated here by writing
    again through the ordinary fold path before the operator's write reaches
    the store. The stale write must be refused, must change neither the head
    nor the history, and the fold's own position must survive it untouched.
    """
    await store.write(standing=_standing("position one", fold_count=1, updated_at=T0))
    stale_read = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert stale_read is not None

    # A fold lands in between — the row the operator read no longer exists.
    await store.write(standing=_standing(
        "position two, folded after the operator's read",
        fold_count=2, updated_at=T0 + 1000,
    ))

    stale_edit = _standing(
        "the operator's edit, built from the stale read",
        fold_count=stale_read.fold_count, updated_at=T0 + 2000,
        last_folded_row_id=stale_read.last_folded_row_id,
    )
    applied = await store.write_if_unchanged(expected=stale_read, standing=stale_edit)
    assert applied is False

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "position two, folded after the operator's read"
    assert head.fold_count == 2
    # No third revision was appended for the refused write.
    assert await _revision_count(engine) == 2
