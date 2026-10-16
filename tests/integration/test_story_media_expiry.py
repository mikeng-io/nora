"""A story frame's bytes deleted at their bound (`nora.app.social_expiry.
StoryMediaExpiry`), driven tick by tick: the real Record and tombstone in
Postgres, a `FakeBlobStore` holding the bytes, and a clock each test moves by
hand.

Every test ticks the pass on a moving clock rather than seeding a state and
asking once: «not before the bound», «once», «after a restart» are all state
carried from one tick to the next. The memory index embeds no social item yet,
so at the tick no frame is embedded; when the bytes go once BOTH exist is
`bytes_due`'s, `tests/unit/test_story_bytes_due.py`.

Database scoped to the MODULE and truncated between tests (AGENTS.md).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.social_expiry import StoryMediaExpiry
from nora.domain.social_stream import SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    BLOB_TOMBSTONE,
    COMPUTE_JOBS,
    COMPUTER_EVENTS,
    COMPUTER_EXPORTS,
    DOCUMENT_REVISION,
    MEDIA_OBJECTS,
    MESSAGES,
    REMINDERS,
    SOCIAL_ITEM,
    STICKER_INDEX,
)
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.obs.metrics import Metrics
from nora.testing import FakeBlobStore
from tests.integration.test_snapshot_holds_what_was_held import APPEND, ROWS

ADA = "10000001"
KEEP, MAX = 7, 30


@pytest.fixture(scope="module")
def _db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    dbname = "t_story_media_expiry_" + uuid4().hex
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


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def days(self, n: float) -> None:
        self.now += timedelta(days=n)


@dataclass
class World:
    store: SqlSocialStreamStore
    engine: Any
    clock: Clock = field(default_factory=Clock)
    metrics: Metrics = field(default_factory=Metrics)
    #: Run inside each `delete`, before its check: a writer racing the pass.
    racing: Callable[[str], Awaitable[None]] | None = None
    blobs: FakeBlobStore = field(init=False)

    def __post_init__(self) -> None:
        self.blobs = FakeBlobStore(
            clock=lambda: self.clock.now.timestamp(), before_delete=self._race,
        )

    async def _race(self, key: str) -> None:
        if self.racing is not None:
            await self.racing(key)

    def expiry(self) -> StoryMediaExpiry:
        """A new pass over the same rows and bytes: what a restart builds."""
        return StoryMediaExpiry(
            store=self.store, blobs=self.blobs, keep_days=KEEP, max_days=MAX,
            tick_seconds=600, metrics=self.metrics, clock=self.clock,
        )

    async def seed(
        self, item_id: str, *, kind: str = "story_frame", data: bytes | None = None,
        perceived: bool = False, expires: bool | None = None,
    ) -> str:
        """One item recorded now with its bytes in custody, the way the poller
        leaves it; a story frame expires a day after it was posted, and so does
        any item given `expires`. Answers its content address."""
        sha = await self.blobs.put(
            data=data if data is not None else f"bytes of {item_id}".encode(),
        )
        taken = self.clock.now
        await self.store.insert_item(
            SocialItem(
                network="instagram", item_id=item_id, account=ADA, kind=kind,  # type: ignore[arg-type]
                taken_at=taken,
                expires_at=taken + timedelta(days=1)
                if (kind == "story_frame" if expires is None else expires) else None,
                caption=None, alt_text=None, media=(),
            ),
            person="ada", origin="poll", seen_at=self.clock.now, media_ref=sha,
        )
        if perceived:
            await self.store.record_perception(
                network="instagram", item_id=item_id, person="ada", units=("friends",),
                origin="poll", outcome="ok", calls=1, at=self.clock.now,
            )
        return sha

    async def tombstones(self) -> list[tuple[str, str, str]]:
        t = BLOB_TOMBSTONE
        async with self.engine.connect() as conn:
            return [
                (r.sha256, r.kind, r.reason)
                for r in await conn.execute(sa.select(t).order_by(t.c.recorded_at))
            ]

    def held(self, sha: str) -> bool:
        return sha in self.blobs.blobs

    def deleted(self, reason: str) -> int:
        return int(self.metrics.get("nora_social_story_bytes_deleted_total", reason=reason))

    def unembedded(self) -> int:
        return int(self.metrics.get("nora_social_story_unembedded_total"))


@pytest.fixture
async def world(_db: str) -> AsyncIterator[World]:
    engine = create_sql_engine(_db)
    try:
        yield World(store=SqlSocialStreamStore(engine), engine=engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate social_item, social_perception, blob_tombstone, media_objects, "
                "messages, sticker_index, computer_events, computer_exports, "
                "document_revision, compute_jobs, reminders cascade",
            ))
        await engine.dispose()


# ---- the bounds -----------------------------------------------------------------


async def test_nothing_is_deleted_before_the_first_bound(world) -> None:
    sha = await world.seed("s1", perceived=True)
    expiry = world.expiry()

    for _ in range(KEEP + 1):  # it expires a day in, and its bound is KEEP days past that
        await expiry.tick()
        world.clock.days(1)

    assert world.held(sha)
    assert await world.tombstones() == []


async def test_at_the_first_bound_a_frame_without_an_embedding_keeps_its_bytes(world) -> None:
    perceived = await world.seed("s1", perceived=True)
    unperceived = await world.seed("s2")
    expiry = world.expiry()

    world.clock.days(1 + KEEP)
    for _ in range(MAX - KEEP - 1):
        await expiry.tick()
        world.clock.days(1)

    assert world.held(perceived), "perceived but not embedded: kept to the hard limit"
    assert world.held(unperceived)
    assert await world.tombstones() == []
    assert world.blobs.deletes == 0


async def test_at_the_hard_limit_the_bytes_go_tombstoned_and_counted_unembedded(world) -> None:
    sha = await world.seed("s1", perceived=True)
    other = await world.seed("s2")
    expiry = world.expiry()

    world.clock.days(1 + MAX - 0.01)
    await expiry.tick()
    assert world.held(sha), "a moment before the hard limit"

    world.clock.days(0.01)
    await expiry.tick()
    await expiry.tick()

    assert not world.held(sha) and not world.held(other)
    assert sorted(await world.tombstones()) == sorted([
        (sha, "story_frame", "hard_limit"), (other, "story_frame", "hard_limit"),
    ])
    assert world.blobs.deletes == 2, "deleted once each, however many ticks"
    assert world.deleted("hard_limit") == 2
    assert world.deleted("embedded") == 0
    assert world.unembedded() == 2


async def test_the_rows_and_the_description_stay_when_the_bytes_go(world) -> None:
    sha = await world.seed("s1", perceived=True)
    async with world.engine.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type="image/jpeg", size=1, source="social",
            description="friends at dinner", created_at_ms=0,
        ))
    world.clock.days(1 + MAX)
    await world.expiry().tick()

    assert not world.held(sha)
    async with world.engine.connect() as conn:
        assert (await conn.execute(
            sa.select(MEDIA_OBJECTS.c.description).where(MEDIA_OBJECTS.c.sha256 == sha),
        )).scalar_one() == "friends at dinner"
        assert (await conn.execute(sa.text("select count(*) from social_item"))).scalar_one() == 1


async def test_posts_and_reels_are_never_deleted(world) -> None:
    """Even one carrying an expiry: the kind is what keeps a post's bytes."""
    post = await world.seed("p1", kind="post", expires=True)
    reel = await world.seed("r1", kind="reel")
    expiry = world.expiry()

    for _ in range(4):
        world.clock.days(MAX)
        await expiry.tick()

    assert world.held(post) and world.held(reel)
    assert await world.tombstones() == []


# ---- shared bytes ----------------------------------------------------------------


async def _message_naming(world: World, sha: str) -> None:
    async with world.engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES).values(
            platform="signal", conversation_id="room", conversation_type="group",
            sender_id="u1", ts=0, body="[image]",
            media_json=json.dumps([{"sha256": sha, "media_type": "image/jpeg", "size": 1}]),
        ))


async def _generated_first(world: World, sha: str) -> None:
    async with world.engine.begin() as conn:
        await conn.execute(sa.insert(MEDIA_OBJECTS).values(
            sha256=sha, media_type="image/png", size=1, source="generated", created_at_ms=0,
        ))


async def _a_post_repeats_it(world: World, sha: str) -> None:
    await world.seed("p1", kind="post", data=b"shared bytes")


@pytest.mark.parametrize("holder", [_message_naming, _generated_first, _a_post_repeats_it])
async def test_bytes_another_row_names_are_never_deleted(world, holder) -> None:
    sha = await world.seed("s1", data=b"shared bytes")
    await holder(world, sha)
    alone = await world.seed("s2")

    world.clock.days(1 + MAX)
    await world.expiry().tick()

    assert world.held(sha), "another row still names these bytes"
    assert not world.held(alone), "the control: bytes only the frame names go"
    assert [t[0] for t in await world.tombstones()] == [alone]


async def test_bytes_shared_with_a_frame_not_yet_due_wait_for_it(world) -> None:
    sha = await world.seed("s1", data=b"one picture, twice")
    expiry = world.expiry()
    world.clock.days(10)
    await world.seed("s2", data=b"one picture, twice")

    world.clock.days(MAX - 9)
    await expiry.tick()
    assert world.held(sha), "s1 is past its hard limit, s2 is not"

    world.clock.days(10)
    await expiry.tick()
    assert not world.held(sha), "both past: nothing else names them"
    assert await world.tombstones() == [(sha, "story_frame", "hard_limit")]
    assert world.deleted("hard_limit") == 1


# ---- a restart -------------------------------------------------------------------


async def test_a_restart_neither_deletes_twice_nor_misses_a_frame(world) -> None:
    first = await world.seed("s1")
    world.clock.days(1 + MAX)
    await world.expiry().tick()
    assert world.blobs.deletes == 1

    # Down for a while: another frame's bound passes, and the new pass is a
    # new object over the same rows.
    world.clock.days(-MAX)
    second = await world.seed("s2")
    world.clock.days(2 * MAX)
    restarted = world.expiry()
    await restarted.tick()
    await restarted.tick()

    assert not world.held(first) and not world.held(second)
    assert world.blobs.deletes == 2, "the first frame's bytes are not deleted again"
    assert [t[0] for t in await world.tombstones()] == [first, second]


async def test_bytes_deleted_before_their_tombstone_was_written_are_tombstoned_next(world) -> None:
    """A crash between the delete and the tombstone: the next tick finds the
    frame again, its delete answers False, and it writes the tombstone."""
    sha = await world.seed("s1")
    await world.blobs.delete(key=sha, idle_seconds=0)
    world.clock.days(1 + MAX)

    await world.expiry().tick()

    assert await world.tombstones() == [(sha, "story_frame", "hard_limit")]
    assert world.deleted("hard_limit") == 1


# ---- every holder the reference check reads --------------------------------------


def _row(table: str, n: int = 1) -> dict[str, Any]:
    return {**(ROWS.get(table) or APPEND[table])(n)}


async def _insert(world: World, table: sa.Table, row: dict[str, Any]) -> None:
    async with world.engine.begin() as conn:
        await conn.execute(sa.insert(table).values(row))


def _export_by_key(sha: str) -> dict[str, Any]:
    return {**_row("computer_exports"), "destination": "keep", "artifact_id": None,
            "url": None, "sha256": "0" * 64, "blob_key": sha}


#: One row per column `held_elsewhere` reads, each naming the key `sha`.
HOLDERS: dict[str, Callable[[str], tuple[sa.Table, dict[str, Any]]]] = {
    "messages.media_json": lambda sha: (MESSAGES, {
        **_row("messages"), "media_json": json.dumps([{"sha256": sha}]),
    }),
    "sticker_index.sha256": lambda sha: (STICKER_INDEX, {**_row("sticker_index"), "sha256": sha}),
    "computer_events.request": lambda sha: (COMPUTER_EVENTS, {
        **_row("computer_events"), "request": {"key": sha},
    }),
    "computer_events.result": lambda sha: (COMPUTER_EVENTS, {
        **_row("computer_events"), "result": {"sha256": sha},
    }),
    "computer_events.output_ref": lambda sha: (COMPUTER_EVENTS, {
        **_row("computer_events"), "output_ref": {"stdout": {"key": sha}},
    }),
    "computer_exports.sha256": lambda sha: (COMPUTER_EXPORTS, {
        **_row("computer_exports"), "sha256": sha,
    }),
    "computer_exports.blob_key": lambda sha: (COMPUTER_EXPORTS, _export_by_key(sha)),
    "document_revision.media": lambda sha: (DOCUMENT_REVISION, {
        **_row("document_revision"), "media": [sha],
    }),
    "media_objects.sha256 (another source first)": lambda sha: (MEDIA_OBJECTS, {
        **_row("media_objects"), "sha256": sha, "source": "generated",
    }),
    "social_item.media_ref (a post)": lambda sha: (SOCIAL_ITEM, {
        **_row("social_item"), "media_ref": sha,
    }),
    "compute_jobs.payload": lambda sha: (COMPUTE_JOBS, {
        **_row("compute_jobs"), "payload": {"image": sha},
    }),
    "reminders.payload": lambda sha: (REMINDERS, {
        **_row("reminders"), "payload": {"media": sha},
    }),
}


@pytest.mark.parametrize("holder", sorted(HOLDERS))
async def test_every_column_the_check_reads_holds_the_bytes(world, holder) -> None:
    sha = await world.seed("s1", data=b"held bytes")
    table, row = HOLDERS[holder](sha)
    await _insert(world, table, row)
    alone = await world.seed("s2")

    world.clock.days(1 + MAX)
    await world.expiry().tick()

    assert world.held(sha), f"{holder} names these bytes"
    assert not world.held(alone), "the control: bytes only the frame names go"


def test_the_holder_test_covers_every_column_the_check_reads() -> None:
    from nora.infra.sql.blob_refs import BLOB_KEY_COLUMNS

    named = {tuple(h.split(" ")[0].split(".")) for h in HOLDERS}
    assert named == set(BLOB_KEY_COLUMNS)


# ---- a writer racing the pass ------------------------------------------------------


async def test_bytes_a_writer_stores_again_during_the_pass_are_kept(world) -> None:
    """A room message carrying the frame's bytes: ingest `put`s them, then
    writes the row. A deletion between the two must not take them."""
    sha = await world.seed("s1", data=b"forwarded to a room")
    world.clock.days(1 + MAX)

    async def a_room_stores_them(key: str) -> None:
        if key == sha:
            await world.blobs.put(data=b"forwarded to a room")
    world.racing = a_room_stores_them
    await world.expiry().tick()
    world.racing = None

    assert world.held(sha)
    assert await world.tombstones() == [], "kept bytes leave no tombstone"
    await _insert(world, MESSAGES, HOLDERS["messages.media_json"](sha)[1])
    world.clock.days(1)
    await world.expiry().tick()
    assert world.held(sha), "the room's row now holds them"


async def test_references_are_checked_again_just_before_each_deletion(world) -> None:
    """A row naming the second frame's bytes lands while the first frame's are
    being deleted: a check made once for the batch would miss it."""
    first = await world.seed("s1")
    second = await world.seed("s2")
    world.clock.days(1 + MAX)

    async def a_document_names_the_other(key: str) -> None:
        world.racing = None
        other = second if key == first else first
        await _insert(world, DOCUMENT_REVISION, {**_row("document_revision"), "media": [other]})
    world.racing = a_document_names_the_other
    await world.expiry().tick()

    assert world.held(first) != world.held(second), "the second is now held by the document"
    assert len(await world.tombstones()) == 1


async def test_a_deletion_that_fails_writes_no_tombstone(world) -> None:
    sha = await world.seed("s1")
    world.clock.days(1 + MAX)

    async def the_store_fails(key: str) -> None:
        raise OSError("read-only file system")
    world.racing = the_store_fails
    await world.expiry().tick()

    assert world.held(sha)
    assert await world.tombstones() == []
    assert world.metrics.get("nora_errors_total", stage="social_story_expiry") == 1

    world.racing = None
    await world.expiry().tick()
    assert not world.held(sha), "tried again on the next tick"


# ---- bytes stored again after a deletion -----------------------------------------


async def test_a_later_frame_with_the_same_bytes_is_deleted_at_its_own_bound(world) -> None:
    sha = await world.seed("s1", data=b"one picture, twice")
    world.clock.days(1 + MAX)
    await world.expiry().tick()
    assert not world.held(sha)

    again = await world.seed("s2", data=b"one picture, twice")
    assert again == sha and world.held(sha), "posted again: the bytes are back"
    world.clock.days(1)
    await world.expiry().tick()
    assert world.held(sha), "the new frame's bound has not passed"

    world.clock.days(MAX)
    await world.expiry().tick()
    assert not world.held(sha), "the first frame no longer holds them; the second's bound passed"
    assert [t[0] for t in await world.tombstones()] == [sha, sha]
