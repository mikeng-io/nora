"""`VideoReadingStore` has two implementations, so it gets one suite both pass.

The property the cache exists for is that EVERY input a reading depends on is part of its
identity: a different geometry, model or prompt is a miss, never a stale hit.
"""

from __future__ import annotations

import dataclasses

import pytest

from nora.domain.media import VideoReadingKey
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.video_readings import SqlVideoReadingStore
from nora.testing import FakeVideoReadingStore

KEY = VideoReadingKey(
    sha256="c" * 64, provider="opencode-go", model="mimo-v2.6-flash",
    chunk_seconds=90, chunk_max=10, reading_digest="0123456789ab",
)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeVideoReadingStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlVideoReadingStore(engine)
    await engine.dispose()


async def test_an_unread_clip_is_a_miss(store):
    """Against a NON-EMPTY store: an empty one misses for every key and would pass this for a
    store that ignored the key entirely."""
    await store.put(key=KEY, reading="0:00 — a stage at night")
    assert await store.get(key=dataclasses.replace(KEY, sha256="d" * 64)) is None


async def test_a_put_reading_comes_back_for_the_same_key(store):
    await store.put(key=KEY, reading="0:00 — a stage at night")
    assert await store.get(key=KEY) == "0:00 — a stage at night"


@pytest.mark.parametrize("field,value", [
    ("sha256", "d" * 64), ("provider", "omniroute"), ("model", "k3-256k"),
    ("chunk_max", 20), ("chunk_seconds", 60), ("reading_digest", "ba9876543210"),
])
async def test_any_other_input_is_a_miss_not_a_stale_hit(store, field, value):
    await store.put(key=KEY, reading="0:00 — a stage at night")
    assert await store.get(key=dataclasses.replace(KEY, **{field: value})) is None


async def test_a_second_put_for_the_same_key_replaces_rather_than_raises(store):
    await store.put(key=KEY, reading="first")
    await store.put(key=KEY, reading="second")
    assert await store.get(key=KEY) == "second"


async def test_an_empty_reading_is_never_stored(store):
    await store.put(key=KEY, reading="")
    assert await store.get(key=KEY) is None
