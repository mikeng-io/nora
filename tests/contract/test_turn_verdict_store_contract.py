"""Shared contract for the turn-verdict store. Every implementation runs every
test.

`nora.infra.sql.turn_verdicts.SqlTurnVerdictStore` over real Postgres, and
`nora.testing.InMemoryTurnVerdictStore` built from `nora.domain.turn_verdict`
alone. CLAUDE.md's rule is why this exists at all — a port never gains a second
implementation, *including a test fake*, without a suite both pass.

**The property this suite is really about is NEVER REWRITTEN** (26 §3). One
verdict per turn, and a second write must FAIL rather than replace. That is
enforced by `tag_id` being the primary key and by `save` being a plain INSERT
with no `ON CONFLICT` — so the suite writes twice, always.

**And it goes AROUND the domain type where the database is the real guard.**
Writing `InMemoryStandingStore` this afternoon taught this the hard way: that
fake had no length check and only ever refused because `__post_init__` did, so
it was more permissive than Postgres and the contract suite could not see it.
`object.__setattr__` is how a smuggled row is built here for the same reason.

Database and migrations built ONCE per module, table truncated between tests —
CLAUDE.md's contract-suite rule; the isolation needed is "no rows from the last
test", not "no schema".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.turn_verdict import UNSUPPORTED_MAX_ITEMS, TurnVerdict
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.turn_verdicts import SqlTurnVerdictStore
from nora.testing import InMemoryTurnVerdictStore

NOW = 1_756_000_000_000
CONV = "g1"


class _VerdictStore(Protocol):
    async def save(self, verdict: TurnVerdict) -> None: ...
    async def read(self, *, tag_id: str) -> TurnVerdict | None: ...


@pytest.fixture(scope="module")
def _verdict_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_verdict_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[_VerdictStore]:
    if request.param == "memory":
        yield InMemoryTurnVerdictStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_verdict_db"))
    try:
        yield SqlTurnVerdictStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate turn_verdict cascade"))
        await engine.dispose()


def _v(tag_id: str = "tag-1", *, grounded: bool = True, unsupported=()) -> TurnVerdict:
    return TurnVerdict(tag_id=tag_id, conversation_id=CONV, grounded=grounded,
                       unsupported=tuple(unsupported), created_at=NOW)


async def test_a_turn_with_no_verdict_reads_as_none(store):
    assert await store.read(tag_id="tag-1") is None


async def test_a_saved_verdict_reads_back_whole(store):
    await store.save(verdict=_v(grounded=False, unsupported=("claimed a closing time",)))
    assert await store.read(tag_id="tag-1") == _v(
        grounded=False, unsupported=("claimed a closing time",))


async def test_a_second_verdict_for_one_turn_is_refused(store):
    """**The property this suite exists for.** §3: one row per turn, NEVER
    rewritten. A store that replaced would pass a save-then-read assertion and
    then silently lose the first judgement of a turn that was debriefed twice —
    which is itself the thing worth knowing about.

    Falsification: give `SqlTurnVerdictStore.save` an `ON CONFLICT DO UPDATE`,
    or let the double replace, and this goes green while the guarantee is gone.
    """
    await store.save(verdict=_v(grounded=True))
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs DuplicateVerdictError
        await store.save(verdict=_v(grounded=False, unsupported=("a later judgement",)))


async def test_the_first_verdict_survives_the_refused_second(store):
    """The refusal is not enough on its own — the ORIGINAL must still be there.
    A store that deleted-then-failed would also raise."""
    await store.save(verdict=_v(grounded=True))
    with pytest.raises(Exception):  # noqa: B017
        await store.save(verdict=_v(grounded=False, unsupported=("a later judgement",)))
    survivor = await store.read(tag_id="tag-1")
    assert survivor is not None and survivor.grounded is True


async def test_verdicts_for_different_turns_coexist(store):
    await store.save(verdict=_v("tag-1", grounded=True))
    await store.save(verdict=_v("tag-2", grounded=False, unsupported=("x",)))
    assert (await store.read(tag_id="tag-1")).grounded is True
    assert (await store.read(tag_id="tag-2")).grounded is False


async def test_an_empty_unsupported_list_round_trips(store):
    await store.save(verdict=_v(grounded=True))
    assert (await store.read(tag_id="tag-1")).unsupported == ()


async def test_both_stores_refuse_a_grounded_verdict_carrying_claims(store):
    """Reached by going AROUND the domain type, which is the only way the
    DATABASE CHECK is ever exercised — `__post_init__` refuses first for any
    ordinary caller. Delete
    `turn_verdict_grounded_has_no_unsupported_check` and this goes red on
    `sql`; drop the guard in the double and it goes red on `memory`.
    """
    smuggled = _v(grounded=True)
    object.__setattr__(smuggled, "unsupported", ("invented a fact",))
    with pytest.raises(Exception):  # noqa: B017
        await store.save(verdict=smuggled)
    assert await store.read(tag_id="tag-1") is None


async def test_both_stores_refuse_too_many_claims(store):
    smuggled = _v(grounded=False)
    object.__setattr__(
        smuggled, "unsupported",
        tuple(f"c{i}" for i in range(UNSUPPORTED_MAX_ITEMS + 1)))
    with pytest.raises(Exception):  # noqa: B017
        await store.save(verdict=smuggled)


async def test_latest_ungrounded_answers_the_newest_ungrounded_one(store):
    """The read S2's trigger needs, and a DIFFERENT question from `read`: is
    there an ungrounded verdict here, not how did this turn go."""
    await store.save(verdict=_v("tag-1", grounded=True))
    await store.save(verdict=TurnVerdict(
        tag_id="tag-2", conversation_id=CONV, grounded=False,
        unsupported=("the newer one",), created_at=NOW + 1000))
    got = await store.latest_ungrounded(conversation_id=CONV)
    assert got is not None and got.tag_id == "tag-2" and got.grounded is False


async def test_a_newer_grounded_verdict_does_not_bury_an_ungrounded_one(store):
    """**The defect this read replaced a `latest` to close, at the store
    boundary.** A read that answers the newest verdict of EITHER kind returns
    `tag-2` here, the trigger sees `grounded` and declines, and the ungrounded
    `tag-1` is never acted on — permanently, because every later turn buries it
    further. The sequence is ordinary: an ungrounded turn, then a successful one.

    Both implementations must filter, or the double cannot stand in for the
    store on the one input that matters."""
    await store.save(verdict=TurnVerdict(
        tag_id="tag-1", conversation_id=CONV, grounded=False,
        unsupported=("invented a price",), created_at=NOW))
    await store.save(verdict=TurnVerdict(
        tag_id="tag-2", conversation_id=CONV, grounded=True,
        created_at=NOW + 1000))
    got = await store.latest_ungrounded(conversation_id=CONV)
    assert got is not None and got.tag_id == "tag-1", (
        "a later grounded verdict buried the unconsumed ungrounded one"
    )


async def test_latest_ungrounded_is_deterministic_when_two_share_a_millisecond(store):
    """Both implementations must answer the SAME row, or the double is not a
    stand-in. Postgres would otherwise return whatever the scan reached first
    and the double whatever hashed first — a divergence a contract suite cannot
    see unless both sides are ordered. `tag_id` descending breaks it."""
    for tag in ("tag-a", "tag-b", "tag-c"):
        await store.save(verdict=TurnVerdict(
            tag_id=tag, conversation_id=CONV, grounded=False,
            unsupported=("same ms",), created_at=NOW))
    got = await store.latest_ungrounded(conversation_id=CONV)
    assert got is not None and got.tag_id == "tag-c"


async def test_latest_ungrounded_ignores_other_conversations(store):
    await store.save(verdict=TurnVerdict(
        tag_id="other", conversation_id="g2", grounded=False,
        unsupported=("elsewhere",), created_at=NOW + 9999))
    await store.save(verdict=TurnVerdict(
        tag_id="tag-1", conversation_id=CONV, grounded=False,
        unsupported=("here",), created_at=NOW))
    got = await store.latest_ungrounded(conversation_id=CONV)
    assert got is not None and got.tag_id == "tag-1"


async def test_latest_ungrounded_is_none_when_every_verdict_is_grounded(store):
    """Not the same as an empty conversation, and both answer `None` — a
    conversation whose turns all went fine has nothing to re-derive from."""
    await store.save(verdict=_v("tag-1", grounded=True))
    await store.save(verdict=TurnVerdict(
        tag_id="tag-2", conversation_id=CONV, grounded=True,
        created_at=NOW + 1000))
    assert await store.latest_ungrounded(conversation_id=CONV) is None


async def test_latest_is_none_before_the_first_verdict(store):
    """Not a reason to re-derive: the ordinary state before a conversation's
    first fold."""
    assert await store.latest_ungrounded(conversation_id=CONV) is None
