"""One suite, both implementations of `ComputerExportStore` (design/
sandbox-runtime §6, §7). The shape of the event store's suite: the real
Postgres `pg_url` fixture, migrated before the SQL store is built,
parametrized alongside the in-memory fake.

What is pinned: a row round-trips every field; `for_artifact` answers the
FIRST export of an artifact (its owner), not the newest revision; an unknown
artifact is None; and the table refuses a change to a row (the ownership it
holds is authorisation state).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from nora.domain.computer_exports import (
    ComputerExport,
    ComputerExportStore,
    normalise_export,
)
from nora.infra.sql.computer_exports import SqlComputerExportStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeComputerExportStore

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _export(**overrides):
    defaults = {
        "id": uuid4(), "created_at": T0, "platform": "signal",
        "conversation_id": "conv-1", "scope": "ab" * 16,
        "path": "projects/trip/out/chart.png", "destination": "link",
        "sha256": "c" * 64, "size_bytes": 1234, "media_type": "image/png",
        "title": "Trip report", "tag_id": "tag-1", "operation_id": uuid4(),
        "artifact_id": "art_000001", "url": "https://nora.example.net/drop/abc",
        "revised": False, "blob_key": None, "session_id": uuid4(),
    }
    defaults.update(overrides)
    return ComputerExport(**defaults)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeComputerExportStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlComputerExportStore(engine)
    await engine.dispose()


def test_both_satisfy_the_port():
    assert isinstance(FakeComputerExportStore(), ComputerExportStore)
    assert isinstance(SqlComputerExportStore(engine=None), ComputerExportStore)  # type: ignore[arg-type]


async def test_a_recorded_link_export_round_trips_every_field(store):
    row = _export()
    await store.record(row)
    assert await store.for_artifact(artifact_id="art_000001") == normalise_export(row)


async def test_a_kept_export_round_trips_with_its_blob_key(store):
    kept = _export(destination="keep", artifact_id=None, url=None, blob_key="k" * 64,
                   session_id=None)
    await store.record(kept)
    # Not an artifact: nothing to own.
    assert await store.for_artifact(artifact_id="art_000001") is None


async def test_for_artifact_is_the_first_export_not_the_newest_revision(store):
    first = _export(created_at=T0)
    revision = _export(created_at=T0 + timedelta(hours=1), revised=True,
                       path="projects/trip/out/chart-v2.png")
    await store.record(revision)
    await store.record(first)
    got = await store.for_artifact(artifact_id="art_000001")
    assert got is not None
    assert (got.id, got.revised) == (first.id, False)


async def test_an_unknown_artifact_is_none(store):
    await store.record(_export())
    assert await store.for_artifact(artifact_id="art_999999") is None


def test_a_link_row_without_its_url_is_refused_before_any_store():
    with pytest.raises(ValueError, match="artifact_id and url"):
        _export(url=None)
    with pytest.raises(ValueError, match="not exportable"):
        _export(media_type="")
    with pytest.raises(ValueError, match="not exportable"):
        _export(media_type="text/html")
    with pytest.raises(ValueError, match="never published as a link"):
        _export(media_type="image/svg+xml")


async def test_the_table_refuses_a_change_to_a_row(pg_url):
    """Ownership is authorisation state: a mistaken UPDATE that moved a row
    to another room would hand that room the artifact."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        await SqlComputerExportStore(engine).record(_export())
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(sa.text(
                    "UPDATE computer_exports SET conversation_id = 'conv-2'"
                ))
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(sa.text("DELETE FROM computer_exports"))
    finally:
        await engine.dispose()
