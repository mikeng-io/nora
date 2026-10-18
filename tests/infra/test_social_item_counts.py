"""A post's or reel's counts in the Record (`social_item_count`,
design/cognition-loop/22-social-stream.md §5): `record_counts` appends a row
only where one read's counts differ from the item's latest, never writes an
unknown as 0, writes nothing for a read that carried nothing, and keeps
`likes` NULL while the author hides them; `current_counts` and `items_of`
read the latest row under the readable rule.

Database scoped to the MODULE and truncated between tests (AGENTS.md).
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.social_stream import ItemCounts, MediaItem, SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import SOCIAL_ITEM_COUNT
from nora.infra.sql.social_stream import SqlSocialStreamStore

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
NET = "instagram"


@pytest.fixture(scope="module")
def _db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    dbname = "t_social_counts_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{pg_template}"')
    try:
        yield (
            f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
            f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
        )
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
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
            await conn.execute(sa.text(
                "truncate social_item, social_item_state, social_item_count",
            ))
        await eng.dispose()


def item(item_id: str, counts: ItemCounts | None = None) -> SocialItem:
    return SocialItem(
        network=NET, item_id=item_id, account="10000001", kind="post", taken_at=T0,
        expires_at=None, caption="c", alt_text=None,
        media=(MediaItem("image", "https://x"),), counts=counts or ItemCounts(),
    )


async def recorded(store: SqlSocialStreamStore, item_id: str, person: str = "ada") -> None:
    await store.insert_item(item(item_id), person=person, origin="poll", seen_at=T0)


async def count_rows(engine: Any, item_id: str) -> list[tuple[Any, ...]]:
    c = SOCIAL_ITEM_COUNT
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(c.c.likes, c.c.comments, c.c.reposts, c.c.likes_hidden)
            .where(c.c.item_id == item_id).order_by(c.c.recorded_at, c.c.id),
        )).all()
    return [tuple(r) for r in rows]


async def write(store: SqlSocialStreamStore, *items: SocialItem) -> int:
    return await store.record_counts(network=NET, items=items, read_id=str(uuid4()))


async def test_a_new_count_is_a_row_and_an_unchanged_read_writes_none(engine) -> None:
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    assert await write(store, item("1", ItemCounts(31, 4, 2, False))) == 1
    assert await write(store, item("1", ItemCounts(31, 4, 2, False))) == 0, "unchanged"
    assert await write(store, item("1", ItemCounts(32, 4, 2, False))) == 1, "a like more"
    assert await count_rows(engine, "1") == [(31, 4, 2, False), (32, 4, 2, False)]


async def test_an_unknown_count_is_written_null_never_zero(engine) -> None:
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    assert await write(store, item("1", ItemCounts(likes=7))) == 1
    assert await count_rows(engine, "1") == [(7, None, None, False)]


async def test_a_read_that_carries_nothing_writes_nothing(engine) -> None:
    """An upstream shape change would look like this: no row of NULLs lands
    over the real counts."""
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    await write(store, item("1", ItemCounts(31, 4, None, False)))
    assert await write(store, item("1", ItemCounts())) == 0
    assert await write(store, item("1", ItemCounts(likes_hidden=False))) == 0, (
        "the switch alone, off, carries no count"
    )
    assert await count_rows(engine, "1") == [(31, 4, None, False)]


async def test_a_count_that_falls_back_to_unknown_writes_no_row_on_its_own(engine) -> None:
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    await write(store, item("1", ItemCounts(31, 4, 2, False)))
    assert await write(store, item("1", ItemCounts(31, None, None, False))) == 0
    (latest,) = (await store.current_counts(items=[(NET, "1")])).values()
    assert (latest.likes, latest.comments, latest.reposts) == (31, 4, 2)


async def test_hidden_likes_are_null_and_the_switch_is_a_row(engine) -> None:
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    await write(store, item("1", ItemCounts(31, 4, None, False)))
    # The platform still sends a number; the author hid it.
    assert await write(store, item("1", ItemCounts(31, 4, None, True))) == 1
    assert await count_rows(engine, "1") == [(31, 4, None, False), (None, 4, None, True)]
    async with engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.insert(SOCIAL_ITEM_COUNT).values(
                network=NET, item_id="1", likes=5, likes_hidden=True,
            ))


async def test_an_item_not_recorded_gets_no_row(engine) -> None:
    """A back-fill page lists items past its limits that are never recorded;
    their counts would be rows about nothing."""
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    assert await write(
        store, item("1", ItemCounts(1, 1)), item("past-the-limit", ItemCounts(9, 9)),
    ) == 1
    assert await count_rows(engine, "past-the-limit") == []


async def test_current_counts_reads_the_latest_row_of_readable_items_only(engine) -> None:
    """Gone or hidden, or of a person who opted out (under a retired id too),
    an item has no counts; one never counted has none either."""
    plain = SqlSocialStreamStore(engine)
    for item_id, person in (("1", "ada"), ("gone", "ada"), ("hidden", "ada"),
                            ("cy", "cy"), ("cy-old", "cy_old"), ("never", "ada")):
        await recorded(plain, item_id, person)
    await write(plain, *(
        item(i, ItemCounts(3, 1)) for i in ("1", "gone", "hidden", "cy", "cy-old")
    ))
    await write(plain, item("1", ItemCounts(5, 1)))
    await plain.record_state(network=NET, item_id="gone", state="gone")
    await plain.record_state(network=NET, item_id="hidden", state="hidden")
    readable = SqlSocialStreamStore(engine, opted_out=frozenset({"cy", "cy_old"}))

    got = await readable.current_counts(
        items=[(NET, i) for i in ("1", "gone", "hidden", "cy", "cy-old", "never")],
    )
    assert set(got) == {(NET, "1")}
    assert (got[(NET, "1")].likes, got[(NET, "1")].comments) == (5, 1), "the latest row"
    # Positive control: the same rows, read without the opt-out, are there.
    assert set(await plain.current_counts(items=[(NET, "cy"), (NET, "cy-old")])) == {
        (NET, "cy"), (NET, "cy-old"),
    }


async def test_items_of_carries_each_items_latest_counts(engine) -> None:
    store = SqlSocialStreamStore(engine)
    await recorded(store, "1")
    await store.insert_item(
        dataclasses.replace(item("2"), taken_at=T0 - timedelta(hours=1)),
        person="ada", origin="poll", seen_at=T0,
    )
    await write(store, item("1", ItemCounts(31, 4)))
    got = await store.items_of(
        persons=["ada"], start=T0 - timedelta(days=1), end=T0 + timedelta(days=1), limit=5,
    )
    first, never = got
    assert first.counts is not None and (first.counts.likes, first.counts.comments) == (31, 4)
    assert never.counts is None, "never counted"


async def test_the_last_list_read_is_the_latest_page_read_at_any_stage(engine) -> None:
    """What the recount waits on: a list read, a back-fill, a re-walk or a
    recount page read, never a probe, a story read or a failed page."""
    store = SqlSocialStreamStore(engine)
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
        await conn.execute(sa.text("truncate social_poll"))

    async def at(person: str) -> datetime | None:
        return (await store.last_list_reads(network=NET)).get(person)

    for stage, outcome in (("probe", "unchanged"), ("story", "read_ok"), ("items", "error")):
        await store.record_poll(network=NET, person="ada", stage=stage, outcome=outcome)
    assert await at("ada") is None
    seen = []
    for stage in ("items", "backfill", "relist", "recount"):
        await store.record_poll(network=NET, person="ada", stage=stage, outcome="read_ok")
        seen.append(await at("ada"))
        await store.record_poll(network=NET, person="ada", stage=stage, outcome="backed_off")
        assert await at("ada") == seen[-1], f"a failed {stage} page reads nothing"
    assert None not in seen and seen == sorted(set(seen)), "each stage's read moves it"
    assert await at("bea") is None
