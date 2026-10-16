"""The social stream's Record (`nora.infra.sql.social_stream`) against real
Postgres: the dedupe is a unique index, the marker is a read of append-only
rows, and nothing is ever updated."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.domain.social_stream import Counts, MediaItem, SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import SOCIAL_ITEM
from nora.infra.sql.social_stream import SqlSocialStreamStore

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
async def stores(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlSocialStreamStore(engine), engine
    finally:
        await engine.dispose()


def item(item_id: str = "2001", *, caption: str = "first", kind: str = "post") -> SocialItem:
    return SocialItem(
        network="instagram", item_id=item_id, account="10000001", kind=kind,  # type: ignore[arg-type]
        taken_at=T0, expires_at=T0 + timedelta(hours=24) if kind == "story_frame" else None,
        caption=caption, alt_text=None, media=(MediaItem("image", "https://x"),),
    )


async def rows(engine, item_id: str):
    async with engine.connect() as conn:
        return (await conn.execute(
            sa.select(SOCIAL_ITEM).where(SOCIAL_ITEM.c.item_id == item_id)
        )).all()


async def test_a_second_insert_of_the_same_item_is_not_new_and_changes_nothing(stores) -> None:
    store, engine = stores
    assert await store.insert_item(item(), person="ada", origin="poll", seen_at=T0) is True
    again = item(caption="edited later")
    assert await store.insert_item(
        again, person="ada", origin="backfill", seen_at=T0 + timedelta(hours=1),
    ) is False
    (row,) = await rows(engine, "2001")
    assert (row.caption, row.origin, row.person) == ("first", "poll", "ada")


async def test_the_same_item_id_on_another_network_is_a_different_item(stores) -> None:
    store, _ = stores
    other = SocialItem(**{**item().__dict__, "network": "threads"})
    assert await store.insert_item(item(), person="ada", origin="poll", seen_at=T0)
    assert await store.insert_item(other, person="ada", origin="poll", seen_at=T0)


async def test_a_story_frame_needs_its_expiry(stores) -> None:
    store, _ = stores
    bad = SocialItem(**{**item(kind="story_frame").__dict__, "expires_at": None})
    with pytest.raises(sa.exc.IntegrityError):
        await store.insert_item(bad, person="ada", origin="poll", seen_at=T0)


async def test_the_marker_is_the_probe_value_of_the_last_advancing_story_read(stores) -> None:
    store, _ = stores
    assert await store.read_marker(network="instagram", person="ada") is None
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="story_due",
        latest_reel_media=100,
    )
    # A probe alone moves nothing; only a completed story read does.
    assert await store.read_marker(network="instagram", person="ada") is None
    await store.record_poll(
        network="instagram", person="ada", stage="story", outcome="read_ok",
        latest_reel_media=100, advanced_marker=True,
    )
    assert await store.read_marker(network="instagram", person="ada") == 100
    await store.record_poll(
        network="instagram", person="ada", stage="story", outcome="error",
        latest_reel_media=200, advanced_marker=False,
    )
    assert await store.read_marker(network="instagram", person="ada") == 100
    await store.record_poll(
        network="instagram", person="ada", stage="story", outcome="read_ok",
        latest_reel_media=200, advanced_marker=True,
    )
    assert await store.read_marker(network="instagram", person="ada") == 200
    assert await store.read_marker(network="instagram", person="bea") is None
    assert await store.read_marker(network="threads", person="ada") is None


async def test_last_counts_are_the_latest_probe_that_had_both(stores) -> None:
    store, _ = stores
    assert await store.last_counts(network="instagram", person="ada") is None
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="items_due",
        media_count=10, total_clips_count=2,
    )
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="partial",
    )
    assert await store.last_counts(network="instagram", person="ada") == Counts(10, 2)
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="count_fell",
        media_count=9, total_clips_count=2,
    )
    assert await store.last_counts(network="instagram", person="ada") == Counts(9, 2)


async def test_an_unknown_outcome_is_refused(stores) -> None:
    store, _ = stores
    with pytest.raises(sa.exc.IntegrityError):
        await store.record_poll(
            network="instagram", person="ada", stage="probe", outcome="great",
        )


async def test_an_items_current_state_is_its_latest_row(stores) -> None:
    store, _ = stores
    assert await store.item_state(network="instagram", item_id="2001") is None
    await store.record_state(network="instagram", item_id="2001", state="gone", reason="deleted")
    assert await store.item_state(network="instagram", item_id="2001") == "gone"
    await store.record_state(network="instagram", item_id="2001", state="hidden")
    assert await store.item_state(network="instagram", item_id="2001") == "hidden"
    assert await store.item_state(network="instagram", item_id="2002") is None
