"""Shared contract for the Standing store. Every implementation runs every test.

Two implementations: `nora.infra.sql.standing.SqlStandingStore` over real Postgres in a
testcontainer, and `nora.testing.InMemoryStandingStore` built from `nora.domain.standing`
alone. CLAUDE.md's rule is why this file exists at all — a port never gains a second
implementation, *including a test fake*, without a suite both pass.

**The property this suite is really about is `write` being an UPSERT.** The row is REVISED
IN PLACE: one row per `(platform, conversation_id)`, no history, no second row. A fake
backed by a list, or a SQL store using a plain `INSERT`, would pass a naive "write then
read" assertion and fail the second write — which is every turn after the first. So the
suite writes twice, always.

**And the second property is that the two implementations REFUSE identically.**
`InMemoryStandingStore` holds `Standing` objects rather than dicts precisely so the
ceiling refusal is structural: the domain constructor raises before the value can be
stored, exactly as `standing_body_fits_the_ceiling_check` raises before the row can land.
A fake that stored whatever it was handed would turn a missing guarantee into a passing
gate.

The database and its migrations are built ONCE for this module and the table is truncated
between tests, per CLAUDE.md's contract-suite rule: the isolation this suite needs is "no
rows from the last test", not "no schema from the last test", and a per-test rebuild's
cost lands on an unrelated wall-clock test in another directory rather than here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing, WorkingSetItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.standing import SqlStandingStore
from nora.testing import InMemoryStandingStore

NOW = 1_756_000_000_000
PLATFORM = "signal"
OTHER_PLATFORM = "discord"
CONV = "g1"
OTHER_CONV = "g2"


class _StandingStore(Protocol):
    """The surface both implementations offer, named here so the suite
    type-checks against the SHAPE rather than against whichever class the
    fixture happened to yield — a `mypy` pass that only ever saw
    `SqlStandingStore` would not notice the fake drifting off it."""

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None: ...
    async def write(self, *, standing: Standing) -> None: ...


@pytest.fixture(scope="module")
def _standing_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file — see the module docstring, and
    `tests/contract/test_note_store_contract.py::_notes_db` for the house rule."""
    dbname = "t_standing_" + uuid4().hex
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


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[_StandingStore]:
    if request.param == "memory":
        yield InMemoryStandingStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_standing_db"))
    try:
        yield SqlStandingStore(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate standing cascade"))
        await engine.dispose()


def _standing(body: str = "They are picking a restaurant.", *,
              platform: str = PLATFORM, conversation_id: str = CONV,
              fold_count: int = 1, updated_at: int = NOW,
              last_folded_row_id: int | None = None,
              working_set: tuple[WorkingSetItem, ...] = ()) -> Standing:
    return Standing(platform=platform, conversation_id=conversation_id,
                    body=body, fold_count=fold_count, updated_at=updated_at,
                    last_folded_row_id=last_folded_row_id,
                    working_set=working_set)


async def test_a_conversation_with_no_position_reads_as_none(store):
    """`None` is a first-class state, not an error: a conversation Nora has not
    folded has no position, and the fold is told so explicitly rather than
    handed an empty one (`prior` is "always present, even when empty")."""
    assert await store.read(platform=PLATFORM, conversation_id=CONV) is None


async def test_a_written_position_reads_back_whole(store):
    await store.write(standing=_standing(fold_count=3))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row == _standing(fold_count=3)


async def test_a_second_write_revises_rather_than_adding_a_second_row(store):
    """**The property this suite exists for.** One row per conversation, revised in place. A
    store that appended would pass a write-then-read assertion and then hold two
    contradictory positions, neither able to correct the other.
    """
    await store.write(standing=_standing("the first position", fold_count=1))
    await store.write(standing=_standing(
        "the corrected position", fold_count=2, updated_at=NOW + 1000))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row.body == "the corrected position"
    assert row.fold_count == 2
    assert row.updated_at == NOW + 1000


async def test_the_row_id_watermark_round_trips_including_its_null(store):
    """The fold's row-id watermark, both states. `None` is the ONLY "never folded" — the
    cold-start case, which reads from the beginning — and must survive the round trip as
    itself: a null that read back as `0` would compare unequal to `None` and equal to
    another `0`, which is a watermark satisfiable by accident.

    A store that dropped the column would pass the write/read tests above — it round-trips
    everything else — and the event trigger would then re-read the whole conversation from
    the beginning on every message. That is the latch, restored below the type.
    """
    await store.write(standing=_standing(last_folded_row_id=None))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.last_folded_row_id is None

    await store.write(standing=_standing(last_folded_row_id=12345))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.last_folded_row_id == 12345

    # And BACK to null. The upsert names the column in its `set_`, so a revise
    # that carried no watermark clears it rather than silently keeping the
    # previous row's value.
    await store.write(standing=_standing(last_folded_row_id=None))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.last_folded_row_id is None


async def test_the_working_set_round_trips_empty_populated_and_back(store):
    """`Standing.working_set`, the sibling of the row-id watermark test above: a store that
    dropped the column would pass the plain write/read tests — it round-trips `body` and
    everything else — so this is its own case, and it goes empty -> populated -> empty to
    prove the upsert names the column in its `set_` rather than keeping the previous row's
    value by omission.
    """
    await store.write(standing=_standing(working_set=()))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.working_set == ()

    items = (
        WorkingSetItem(
            what="陳儀興玫瑰餐廳 — chicken hotpot, near the venue",
            source="web_search", found_at=NOW, bears_on="dinner plan",
        ),
        WorkingSetItem(
            what="a second option the search returned",
            source="web_search", found_at=NOW + 1,
        ),
    )
    await store.write(standing=_standing(
        updated_at=NOW + 1000, working_set=items))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.working_set == items

    await store.write(standing=_standing(updated_at=NOW + 2000, working_set=()))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None and row.working_set == ()


async def test_the_row_id_watermark_advances_with_the_position_in_one_write(store):
    """Watermark-read atomicity: the fold's write sets `body` and `last_folded_row_id` in ONE
    statement, so a crash between the two can never leave a position that claims to have
    read further than it did. On the store contract that means: after one `write`, BOTH
    fields carry the new values — a store that wrote the body and dropped the watermark
    would fail this the way the trigger would fail in production."""
    await store.write(standing=_standing("position one", last_folded_row_id=10))
    await store.write(standing=_standing("position two", last_folded_row_id=25))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None
    assert row.body == "position two"
    assert row.last_folded_row_id == 25


async def test_a_refused_write_advances_neither_body_nor_watermark(store):
    """A fold that fails advances NEITHER the position NOR the watermark: the
    write is refused before either can land (the body exceeds the ceiling, so
    `standing_body_fits_the_ceiling_check` refuses the whole statement). A
    store that advanced the watermark on a refused write would skip the stretch
    it never folded — the exact defect the watermark exists to prevent.

    `object.__setattr__` bypasses the domain type exactly as the sibling
    ceiling test does: this manufactures the state a writer that bypassed the
    type would produce, which is the only state the DB CHECK exists for.
    """
    await store.write(standing=_standing("the prior position", last_folded_row_id=10))
    smuggled = _standing("x" * 10, last_folded_row_id=99)
    object.__setattr__(smuggled, "body", "x" * (STANDING_BODY_CEILING_CHARS + 1))
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError (sql) vs ValueError (memory)
        await store.write(standing=smuggled)
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row is not None
    assert row.body == "the prior position"
    assert row.last_folded_row_id == 10


async def test_the_key_is_platform_and_conversation(store):
    """Every lock, queue and slot in this repo keys `(platform, conversation_id)` and the
    `messages` table carries the platform column, so a `conversation_id`-only key would be
    a join nobody can make — and, worse, would have a Discord room's position overwritten
    by a Signal room that happens to share an id."""
    await store.write(standing=_standing("signal position"))
    await store.write(standing=_standing(
        "discord position", platform=OTHER_PLATFORM))
    assert (await store.read(
        platform=PLATFORM, conversation_id=CONV)).body == "signal position"
    assert (await store.read(
        platform=OTHER_PLATFORM, conversation_id=CONV)).body == "discord position"


async def test_another_conversation_on_the_same_platform_is_separate(store):
    await store.write(standing=_standing("g1 position"))
    assert await store.read(
        platform=PLATFORM, conversation_id=OTHER_CONV) is None


async def test_a_fold_count_of_zero_is_writable(store):
    """The re-derivation path RESETS the count rather than only incrementing it
    (which is why the arithmetic is in the sink and not in the UPDATE), so zero
    has to be a value the store accepts — `fold_count >= 0`, not `> 0`."""
    await store.write(standing=_standing(fold_count=0))
    assert (await store.read(
        platform=PLATFORM, conversation_id=CONV)).fold_count == 0


async def test_a_body_at_the_ceiling_is_accepted_by_both(store):
    """The boundary from the accepting side. An off-by-one in either
    implementation would make the type and the CHECK disagree about exactly one
    value, which is the hardest drift to find."""
    body = "x" * STANDING_BODY_CEILING_CHARS
    await store.write(standing=_standing(body))
    assert len((await store.read(
        platform=PLATFORM, conversation_id=CONV)).body) == (
        STANDING_BODY_CEILING_CHARS
    )


async def test_the_domain_type_refuses_an_over_long_body_before_any_store_is_reached(store):
    """The constructor refusal — and it is named for what it actually proves.

    `_standing(...)` is evaluated as an ARGUMENT, so `Standing.__post_init__` raises
    before `store.write` is ever entered: this asserts a pure-domain fact (also covered by
    `tests/unit/test_standing.py`), not a property of the two STORES. It is what makes the
    two implementations refuse *identically* without either of them containing a length
    check. It is not a STORE test; the one below is.
    """
    with pytest.raises(ValueError, match="ceiling"):
        await store.write(standing=_standing("x" * (STANDING_BODY_CEILING_CHARS + 1)))


async def test_the_store_itself_refuses_a_body_over_the_ceiling(store):
    """The STORE's own refusal, reached by going AROUND the domain type.

    The ceiling is enforced in two places on purpose — `Standing.__post_init__` and
    `standing_body_fits_the_ceiling_check` — and the database half is the one that holds
    against *any writer*, config and future code included. The constructor always raises
    first, so only this test exercises it.

    `object.__setattr__` is the only way past a frozen dataclass's own validation, which
    is precisely why it is right here: it manufactures the state a writer that BYPASSED
    the type would produce, which is the only state the DB CHECK exists for. The in-memory
    double must refuse too, or the two implementations are not interchangeable — it holds
    `Standing` objects, so it inherits nothing here and has to be checked, not assumed.

    Falsification: delete the CHECK from `0034_standing.py` and this goes red on the `sql`
    param.
    """
    smuggled = _standing("x" * 10)
    object.__setattr__(smuggled, "body", "x" * (STANDING_BODY_CEILING_CHARS + 1))
    assert len(smuggled.body) > STANDING_BODY_CEILING_CHARS
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError (sql) vs ValueError (memory)
        await store.write(standing=smuggled)
    assert await store.read(
        platform=PLATFORM, conversation_id=CONV) is None
