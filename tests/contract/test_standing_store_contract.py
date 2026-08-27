"""Shared contract for the Standing store. Every implementation runs every test.

Two implementations: `nora.infra.sql.standing.SqlStandingStore` over real
Postgres in a testcontainer, and `nora.testing.InMemoryStandingStore` built from
`nora.domain.standing` alone. CLAUDE.md's rule is why this file exists at all —
a port never gains a second implementation, *including a test fake*, without a
suite both pass.

**The property this suite is really about is `write` being an UPSERT.** The row
is REVISED IN PLACE (`26-standing` §4): one row per `(platform,
conversation_id)`, no history, no second row. A fake backed by a list, or a SQL
store using a plain `INSERT`, would pass a naive "write then read" assertion and
fail the second write — which is every turn after the first. So the suite writes
twice, always.

**And the second property is that the two implementations REFUSE identically.**
`InMemoryStandingStore` holds `Standing` objects rather than dicts precisely so
the ceiling refusal is structural: the domain constructor raises before the
value can be stored, exactly as `standing_body_fits_the_ceiling_check` raises
before the row can land. A fake that stored whatever it was handed would turn a
missing guarantee into a passing gate.

The database and its migrations are built ONCE for this module and the table is
truncated between tests, per CLAUDE.md's contract-suite rule: the isolation this
suite needs is "no rows from the last test", not "no schema from the last test",
and the per-test rebuild's cost lands on an unrelated wall-clock test in another
directory rather than here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing
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
    """ONE database and ONE migration run for this whole file — see the module
    docstring, and `tests/contract/test_note_store_contract.py::_notes_db` for
    the measured defect that made this the house rule."""
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
            await conn.execute(sa.text("truncate standing cascade"))
        await engine.dispose()


def _standing(body: str = "They are picking a restaurant.", *,
              platform: str = PLATFORM, conversation_id: str = CONV,
              fold_count: int = 1, updated_at: int = NOW) -> Standing:
    return Standing(platform=platform, conversation_id=conversation_id,
                    body=body, fold_count=fold_count, updated_at=updated_at)


async def test_a_conversation_with_no_position_reads_as_none(store):
    """`None` is a first-class state, not an error: a conversation Nora has not
    folded has no position, and the fold is told so explicitly rather than
    handed an empty one (§4.2 — `prior` is "always present, even when empty")."""
    assert await store.read(platform=PLATFORM, conversation_id=CONV) is None


async def test_a_written_position_reads_back_whole(store):
    await store.write(standing=_standing(fold_count=3))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row == _standing(fold_count=3)


async def test_a_second_write_revises_rather_than_adding_a_second_row(store):
    """**The property this suite exists for.** §4: one row per conversation,
    revised in place. A store that appended would pass a write-then-read
    assertion and then hold two contradictory positions — which is the language
    tier's exact live failure (two rows, both wrong, both glossing the same
    thing, neither able to correct the other) reproduced one tier over.
    """
    await store.write(standing=_standing("the first position", fold_count=1))
    await store.write(standing=_standing(
        "the corrected position", fold_count=2, updated_at=NOW + 1000))
    row = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert row.body == "the corrected position"
    assert row.fold_count == 2
    assert row.updated_at == NOW + 1000


async def test_the_key_is_platform_and_conversation(store):
    """S4. Every lock, queue and slot in this repo keys `(platform,
    conversation_id)` and the `messages` table carries the platform column, so
    a `conversation_id`-only key would be a join nobody can make — and, worse,
    would have a Discord room's position overwritten by a Signal room that
    happens to share an id."""
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


async def test_both_implementations_refuse_a_body_over_the_ceiling(store):
    """Identically, and by the SAME mechanism: the domain constructor raises
    before either store is reached, which is why `InMemoryStandingStore` holds
    `Standing` objects rather than dicts. The database's own CHECK is the real
    guarantee for anything that bypasses the type — that is asserted separately,
    in `tests/infra/test_schema_parity.py`, against the migrated schema."""
    with pytest.raises(ValueError, match="ceiling"):
        await store.write(standing=_standing("x" * (STANDING_BODY_CEILING_CHARS + 1)))
