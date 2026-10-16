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


async def test_last_counts_advance_only_with_a_row_that_advanced_them(stores) -> None:
    store, _ = stores
    assert await store.last_counts(network="instagram", person="ada") is None
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="unchanged",
        media_count=10, total_clips_count=2, advanced_counts=True,
    )
    # The probe that made a list read due, and the list read that then
    # failed: the rise is still due on the next probe.
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="items_due",
        media_count=11, total_clips_count=2,
    )
    await store.record_poll(
        network="instagram", person="ada", stage="items", outcome="backed_off",
    )
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="partial",
    )
    assert await store.last_counts(network="instagram", person="ada") == Counts(10, 2)
    await store.record_poll(
        network="instagram", person="ada", stage="items", outcome="read_ok",
        media_count=11, total_clips_count=2, advanced_counts=True,
    )
    assert await store.last_counts(network="instagram", person="ada") == Counts(11, 2)
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="count_fell",
        media_count=9, total_clips_count=2, advanced_counts=True,
    )
    assert await store.last_counts(network="instagram", person="ada") == Counts(9, 2)
    assert await store.last_counts(network="instagram", person="bea") is None


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


async def test_a_place_row_is_the_current_place_and_hides_nothing(stores) -> None:
    """A tag changed after the item was recorded is a `place` row: the latest
    one is the current place (None: removed), the recorded one stands until
    then, and no reader takes a place row for gone or hidden."""
    store, _ = stores
    assert await store.insert_item(
        item("2001"), person="ada", origin="poll", seen_at=T0, media_ref="a1" * 32,
        location="Placeholder Square",
    )
    assert await store.insert_item(item("2002"), person="ada", origin="poll", seen_at=T0)
    ids = ["2001", "2002", "2003"]
    assert await store.current_places(network="instagram", item_ids=ids) == {
        "2001": "Placeholder Square", "2002": None,
    }
    await store.record_place(network="instagram", item_id="2001", place="Placeholder Pier")
    await store.record_place(network="instagram", item_id="2002", place="Placeholder Hill")
    await store.record_place(network="instagram", item_id="2001", place=None)
    assert await store.current_places(network="instagram", item_ids=ids) == {
        "2001": None, "2002": "Placeholder Hill",
    }
    assert await store.item_state(network="instagram", item_id="2001") is None
    assert [u.item_id for u in await store.unperceived(since=T0, limit=10)] == ["2001"]
    # Through any time after the inserts: `recorded_at` is the database's clock.
    later = int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000)
    posted = await store.posted(persons={"ada"}, through_ms=later)
    assert len(posted.items) == 2, "both items still count on the daybook"

    await store.record_state(network="instagram", item_id="2001", state="hidden")
    await store.record_place(network="instagram", item_id="2001", place="Placeholder Bay")
    assert await store.item_state(network="instagram", item_id="2001") == "hidden", (
        "a later place row does not un-hide it"
    )
    assert await store.unperceived(since=T0, limit=10) == []


async def test_a_place_row_does_not_settle_a_read_whose_item_waits_for_perception(
    stores,
) -> None:
    """A read's stimulus waits for each readable item with media to be
    perceived; a `place` row does not make that item hidden, so it still
    waits."""
    store, _ = stores
    read_id = "00000000-0000-4000-8000-000000000001"
    assert await store.insert_item(
        item("2001"), person="ada", origin="poll", seen_at=T0, media_ref="a1" * 32,
        read_id=read_id,
    )
    await store.record_poll(
        network="instagram", person="ada", stage="items", outcome="read_ok", id=read_id,
    )
    await store.record_place(network="instagram", item_id="2001", place="Placeholder Pier")
    since = datetime.now(UTC) - timedelta(days=1)
    units = {"ada": ("u1",)}
    assert await store.settled_reads(require_perceived=False, units=units, since=since)
    assert await store.settled_reads(require_perceived=True, units=units, since=since) == []
