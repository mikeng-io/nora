"""Every MusicSessionStore implementation passes this. CLAUDE.md: a port
never gains a second implementation — including a test fake — without a
shared contract suite both pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.music import MusicSession, MusicSessionStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.testing.music import FakeMusicSessionStore

T0 = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: MusicSessionStore = SqlMusicSessionStore(create_sql_engine(pg_url))
    else:
        s = FakeMusicSessionStore()
    yield s
    await s.aclose()


def _session(**kw) -> MusicSession:
    base = {
        "guild_id": "guild-1", "voice_channel_id": "voice-1", "notify_channel_id": "notify-1",
        "now_playing_source": "youtube",
        "now_playing_locator": "https://www.youtube.com/watch?v=x",
        "now_playing_title": "Track A", "paused": False, "volume": 1.0, "updated_at": T0,
    }
    base.update(kw)
    return MusicSession(**base)


async def test_a_missing_session_returns_none(store):
    assert await store.get("guild-1") is None


async def test_upsert_creates_a_row_readable_by_get(store):
    await store.upsert(_session())

    row = await store.get("guild-1")

    assert row is not None
    assert row.guild_id == "guild-1"
    assert row.voice_channel_id == "voice-1"
    assert row.notify_channel_id == "notify-1"
    assert row.now_playing_title == "Track A"
    assert row.paused is False
    assert row.volume == 1.0


async def test_upsert_replaces_the_existing_row_for_that_guild(store):
    await store.upsert(_session(now_playing_title="Track A", paused=False))

    await store.upsert(_session(now_playing_title="Track B", paused=True, volume=0.5))

    row = await store.get("guild-1")
    assert row is not None
    assert row.now_playing_title == "Track B"
    assert row.paused is True
    assert row.volume == 0.5


async def test_upsert_can_persist_nothing_currently_playing(store):
    """A session can exist (the voice connection is live) with nothing
    playing right now -- queue empty, waiting on the idle timer."""
    await store.upsert(_session(
        now_playing_source=None, now_playing_locator=None, now_playing_title=None,
    ))

    row = await store.get("guild-1")
    assert row is not None
    assert row.now_playing_source is None
    assert row.now_playing_locator is None
    assert row.now_playing_title is None


async def test_all_returns_every_persisted_session(store):
    await store.upsert(_session(guild_id="guild-1"))
    await store.upsert(_session(guild_id="guild-2"))

    guild_ids = {s.guild_id for s in await store.all()}

    assert guild_ids == {"guild-1", "guild-2"}


async def test_delete_removes_the_row(store):
    await store.upsert(_session())

    await store.delete("guild-1")

    assert await store.get("guild-1") is None


async def test_delete_is_a_no_op_for_a_guild_with_no_row(store):
    await store.delete("guild-nonexistent")  # must not raise


async def test_updated_at_round_trips_as_utc(store):
    await store.upsert(_session(updated_at=T0 + timedelta(hours=1)))

    row = await store.get("guild-1")

    assert row is not None
    assert row.updated_at.tzinfo is not None
    assert row.updated_at == T0 + timedelta(hours=1)
