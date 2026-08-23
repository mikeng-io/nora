"""Every MusicQueueStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.music import MusicQueueStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.testing.music import FakeMusicQueueStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: MusicQueueStore = SqlMusicQueueStore(create_sql_engine(pg_url))
    else:
        s = FakeMusicQueueStore()
    yield s
    await s.aclose()


async def _enqueue(store, **kw):
    base = {
        "guild_id": "guild-1", "source": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up", "added_by_person_id": "miles",
        "added_at": T0,
    }
    base.update(kw)
    return await store.enqueue(**base)


async def test_an_empty_queue_has_nothing_to_pop(store):
    assert await store.pop_next("guild-1") is None
    assert await store.queue_for("guild-1") == []


async def test_enqueue_assigns_increasing_positions(store):
    first = await _enqueue(store, title="First")
    second = await _enqueue(store, title="Second")

    assert first.position == 0
    assert second.position == 1


async def test_pop_next_returns_lowest_position_first_and_removes_it(store):
    first = await _enqueue(store, title="First")
    await _enqueue(store, title="Second")

    popped = await store.pop_next("guild-1")

    assert popped is not None
    assert popped.id == first.id
    assert [t.title for t in await store.queue_for("guild-1")] == ["Second"]


async def test_pop_next_is_scoped_to_its_guild(store):
    await _enqueue(store, guild_id="guild-1", title="Guild 1's track")
    guild_2_track = await _enqueue(store, guild_id="guild-2", title="Guild 2's track")

    popped = await store.pop_next("guild-2")

    assert popped is not None
    assert popped.id == guild_2_track.id
    assert [t.title for t in await store.queue_for("guild-1")] == ["Guild 1's track"]


async def test_queue_for_is_ordered_by_position(store):
    await _enqueue(store, title="First")
    await _enqueue(store, title="Second")
    await _enqueue(store, title="Third")

    titles = [t.title for t in await store.queue_for("guild-1")]

    assert titles == ["First", "Second", "Third"]


async def test_clear_removes_every_row_for_its_guild_and_reports_the_count(store):
    await _enqueue(store, guild_id="guild-1")
    await _enqueue(store, guild_id="guild-1")
    await _enqueue(store, guild_id="guild-2")

    removed = await store.clear("guild-1")

    assert removed == 2
    assert await store.queue_for("guild-1") == []
    assert len(await store.queue_for("guild-2")) == 1


async def test_a_new_track_after_a_clear_starts_the_position_over(store):
    await _enqueue(store, title="First")
    await store.clear("guild-1")

    fresh = await _enqueue(store, title="Fresh start")

    assert fresh.position == 0


async def test_added_at_round_trips_as_utc(store):
    await _enqueue(store, added_at=T0 + timedelta(hours=1))

    [row] = await store.queue_for("guild-1")

    assert row.added_at.tzinfo is not None
    assert row.added_at == T0 + timedelta(hours=1)
