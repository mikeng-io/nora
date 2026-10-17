"""The social stream's perception pass (`nora.app.social_perception.
SocialPerception`) driven tick by tick: the real Record and stimulus table in
Postgres, ingest's real comprehension job over a `FakeMediaInterpreter`, and a
clock each test moves by hand.

Every test runs successive ticks, and one runs the poller first, never a
batch: «perceived once», «tried again tomorrow», «announced once» and «once
across a restart» are all state carried from one tick to the next.

Database scoped to the MODULE and truncated between tests (AGENTS.md).
"""

from __future__ import annotations

import functools
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.social_perception import SocialPerception
from nora.app.social_poller import SocialPoller
from nora.config import SocialPollConfig
from nora.domain.media import SOCIAL_MEDIA_SOURCE, MediaObject
from nora.domain.ports import MediaDescription
from nora.domain.social_stream import Page, Probe, SocialItem, StoryFrame, StorySnapshot
from nora.domain.stimuli import NewStimulus
from nora.infra.social.subjects import instagram_roster
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import SOCIAL_PERCEPTION, STIMULUS
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.ingest.comprehension import MediaComprehension
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeBlobStore,
    FakeMediaObjectStore,
    FakeMediaSource,
    FakeSocialSource,
    FakeSocialSubjectIndex,
)
from nora.testing.fakes import FakeMediaInterpreter, FakeMemoryStore
from nora.testing.stimuli import InMemorySourceHealthLog

HK = ZoneInfo("Asia/Hong_Kong")
ADA = "10000001"
_CDN = "https://scontent.cdninstagram.com/v/t51"
#: Ada is listed in two units' rooms, Bea in one; Cy opted out.
UNITS = {"ada": ("friends", "family"), "bea": ("friends",), "cy": ("friends",)}


@pytest.fixture(scope="module")
def _db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    dbname = "t_social_perception_" + uuid4().hex
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
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def next_day(self) -> None:
        self.now += timedelta(days=1)


def _spawn(coro: Any) -> None:
    raise AssertionError("the perception pass awaits its reads; nothing is spawned")


@dataclass
class World:
    store: SqlSocialStreamStore
    engine: Any
    clock: Clock = field(default_factory=Clock)
    metrics: Metrics = field(default_factory=Metrics)
    blobs: FakeBlobStore = field(default_factory=FakeBlobStore)
    media_objects: FakeMediaObjectStore = field(default_factory=FakeMediaObjectStore)
    interpreter: FakeMediaInterpreter = field(
        default_factory=lambda: FakeMediaInterpreter("friends at dinner"),
    )

    def comprehension(self) -> MediaComprehension:
        return MediaComprehension(
            blobs=self.blobs, store=self.media_objects, interpreter=self.interpreter,
            memory=FakeMemoryStore(), metrics=self.metrics, spawn=_spawn,
        )

    def perception(self, *, cap: int = 200, batch: int = 50,
                   comprehension: bool = True, rooms: Any = None) -> SocialPerception:
        return SocialPerception(
            store=self.store, media_objects=self.media_objects,
            comprehension=self.comprehension() if comprehension else None,
            stimuli=SqlStimulusStore(self.engine), units=UNITS, daily_cap=cap, tz=HK,
            chunk_seconds=90, chunk_max=10, tick_seconds=600, metrics=self.metrics,
            batch=batch, rooms=rooms, clock=self.clock,
        )

    async def seed(
        self, item_id: str, *, person: str = "ada", origin: str = "poll",
        read_id: str | None = None, kind: str = "post", caption: str | None = None,
        media: int = 1, collab: bool = False, ago: timedelta = timedelta(hours=1),
        media_type: str = "image/jpeg", duration: float | None = None,
        indexed: bool = True,
    ) -> str:
        """One recorded item, its media held in custody and indexed, the way
        the poller leaves it, and (for a new read) the read's own row, the way
        the poller completes a read. Answers its read id."""
        new_read = read_id is None
        read_id = read_id or str(uuid.uuid4())
        shas = []
        for n in range(media):
            data = f"bytes of {item_id}/{n}".encode()
            sha = await self.blobs.put(data=data)
            if indexed:
                await self.media_objects.save(obj=MediaObject(
                    sha256=sha, media_type=media_type, size=len(data),
                    source=SOCIAL_MEDIA_SOURCE, description="",
                    provenance={} if duration is None else {"duration_seconds": duration},
                    created_at_ms=0,
                ))
            shas.append(sha)
        taken = self.clock.now - ago
        await self.store.insert_item(
            SocialItem(
                network="instagram", item_id=item_id, account=ADA, kind=kind,  # type: ignore[arg-type]
                taken_at=taken,
                expires_at=taken + timedelta(days=1) if kind == "story_frame" else None,
                caption=caption, alt_text=None, media=(), collab=collab,
            ),
            person=person, origin=origin, seen_at=self.clock.now,  # type: ignore[arg-type]
            media_ref=",".join(shas), read_id=read_id,
        )
        if new_read:
            await self.store.record_poll(
                network="instagram", person=person, stage="items", outcome="read_ok",
                id=read_id,
            )
        return read_id

    async def attempts(self) -> list[tuple[str, str, int]]:
        p = SOCIAL_PERCEPTION
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(p.c.item_id, p.c.outcome, p.c.calls).order_by(p.c.recorded_at),
            )).all()
        return [(r.item_id, r.outcome, r.calls) for r in rows]

    async def reasons(self) -> list[str | None]:
        p = SOCIAL_PERCEPTION
        async with self.engine.connect() as conn:
            return list((await conn.execute(
                sa.select(p.c.reason).order_by(p.c.recorded_at),
            )).scalars())

    async def stimuli(self) -> list[Any]:
        async with self.engine.connect() as conn:
            return list((await conn.execute(
                sa.select(STIMULUS).where(STIMULUS.c.kind == "social").order_by(STIMULUS.c.id),
            )).all())


@pytest.fixture
async def world(_db: str) -> AsyncIterator[World]:
    engine = create_sql_engine(_db)
    try:
        yield World(store=SqlSocialStreamStore(engine, opted_out=frozenset({"cy"})),
                    engine=engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate social_item, social_poll, social_item_state, social_perception, "
                "stimulus, blob_tombstone",
            ))
        await engine.dispose()


# ---- perception ---------------------------------------------------------------


async def test_an_item_is_perceived_once_and_told_what_it_is(world) -> None:
    await world.seed("s1", kind="story_frame", collab=False)
    perception = world.perception()

    await perception.tick()
    await perception.tick()
    world.clock.next_day()
    await perception.tick()

    assert len(world.interpreter.calls) == 1, "perceived once, never again"
    assert await world.attempts() == [("s1", "ok", 1)]
    (obj,) = world.media_objects.objs.values()
    assert obj.description == "friends at dinner"
    (context,) = world.interpreter.contexts
    assert context.startswith("This is one frame of a story on Instagram, posted ")
    posted = (world.clock.now - timedelta(days=1, hours=1)).astimezone(HK)
    assert f"posted {posted:%Y-%m-%d %H:%M}" in context
    assert world.metrics.get("nora_perception_total", source="social", outcome="ok") == 1


async def test_a_frame_whose_bytes_expired_unread_is_failed_as_expired_with_no_call(world) -> None:
    """Its bytes went at the story bound before a read landed (the cap held it
    every day): the pass says so, never reads the missing bytes, never asks
    again."""
    await world.seed("s1", kind="story_frame")
    (sha,) = list(world.blobs.blobs)
    deleted_at = await world.store.now()
    await world.blobs.delete(key=sha, idle_seconds=0)
    await world.store.record_tombstone(sha256=sha, reason="hard_limit", deleted_at=deleted_at)
    perception = world.perception()

    await perception.tick()
    world.clock.next_day()
    await perception.tick()

    assert world.interpreter.calls == []
    assert await world.attempts() == [("s1", "failed", 0)]
    assert await world.reasons() == ["expired"]


async def test_an_item_recorded_after_its_bytes_were_deleted_once_is_read(world) -> None:
    """A member posts again a picture whose earlier story frame's bytes were
    being deleted: the post's row lands after the deletion was decided and
    before its tombstone is written. It stored the bytes again, so it is not
    expired: the tombstone covers rows recorded before its `deleted_at`, not
    before the tombstone row itself."""
    sha = hashlib.sha256(b"bytes of p1/0").hexdigest()
    deleted_at = await world.store.now()
    await world.seed("p1", kind="post")
    await world.store.record_tombstone(sha256=sha, reason="hard_limit", deleted_at=deleted_at)
    await world.perception().tick()

    assert await world.attempts() == [("p1", "ok", 1)]
    assert len(world.interpreter.calls) == 1


async def test_past_the_cap_an_item_waits_unperceived_for_the_next_day(world) -> None:
    read = await world.seed("p1", ago=timedelta(hours=1))
    await world.seed("p2", read_id=read, ago=timedelta(hours=2))
    perception = world.perception(cap=1)

    await perception.tick()
    assert await world.attempts() == [("p1", "ok", 1), ("p2", "capped", 0)]
    await perception.tick()
    assert len(world.interpreter.calls) == 1, "capped today stays capped today"

    world.clock.next_day()
    await perception.tick()
    assert len(world.interpreter.calls) == 2
    assert (await world.attempts())[-1] == ("p2", "ok", 1)


async def test_backfill_spends_at_most_half_of_what_polled_items_leave(world) -> None:
    for n in range(2):
        await world.seed(f"poll{n}")
    for n in range(5):
        await world.seed(f"old{n}", origin="backfill", ago=timedelta(days=30 + n))
    perception = world.perception(cap=6)

    await perception.tick()
    await perception.tick()

    outcomes = {item: outcome for item, outcome, _ in await world.attempts()}
    assert [outcomes[f"poll{n}"] for n in range(2)] == ["ok", "ok"]
    # 6 - 2 polled = 4 left; back-fill may take half of it, newest first.
    assert [outcomes[f"old{n}"] for n in range(5)] == ["ok", "ok", "capped", "capped", "capped"]
    assert len(world.interpreter.calls) == 4


async def test_a_failed_read_is_tried_again_on_a_later_day(world) -> None:
    await world.seed("p1")
    world.interpreter.available = False
    perception = world.perception()

    await perception.tick()
    await perception.tick()
    assert await world.attempts() == [("p1", "unavailable", 1)]

    world.interpreter.available = True
    world.clock.next_day()
    await perception.tick()
    assert (await world.attempts())[-1] == ("p1", "ok", 1)


# ---- the stimulus -------------------------------------------------------------


async def test_a_polled_read_raises_one_stimulus_per_unit_after_its_items_are_perceived(
    world,
) -> None:
    """The real poller records a story read; the pass then perceives one frame
    a tick (`batch=1`), and the stimulus waits for the second."""
    clock = world.clock
    source = FakeSocialSource()
    source.probes[ADA] = Probe(latest_reel_media=1, media_count=1, total_clips_count=0,
                               counts_available=True)
    source.pages[ADA] = Page(items=(), next_cursor=None)
    frames = tuple(
        StoryFrame(item_id=f"f{n}", taken_at=clock.now - timedelta(minutes=30 - n),
                   expires_at=clock.now + timedelta(hours=20), kind="image",
                   url=f"{_CDN}/f{n}.jpg", caption="dinner tomorrow at 8" if n else None)
        for n in range(2)
    )
    source.stories[ADA] = StorySnapshot(found=True, frames=frames)
    media = FakeMediaSource()
    for frame in frames:
        media.blobs[f"attachment:{frame.url}"] = f"bytes of {frame.item_id}".encode()
    poller = SocialPoller(
        source=source, store=world.store, health=InMemorySourceHealthLog(),
        roster=functools.partial(instagram_roster, FakeSocialSubjectIndex({"ada": ADA})),
        settings=SocialPollConfig(enabled=True, jitter_seconds=0), metrics=world.metrics,
        media=media, blobs=world.blobs, media_objects=world.media_objects, clock=clock,
    )
    await poller.tick()
    perception = world.perception(batch=1)

    await perception.tick()
    assert await world.stimuli() == [], "one frame is still to be perceived"

    await perception.tick()
    rows = await world.stimuli()
    assert [r.unit for r in rows] == ["friends", "family"]
    assert {r.item for r in rows} == {"story_frame,story_frame"}
    assert {r.person_id for r in rows} == {"ada"}
    assert all("datetime" in r.cues for r in rows), "the caption's date is a cue"
    assert {r.at_ms for r in rows} == {int(frames[1].taken_at.timestamp() * 1000)}
    assert world.metrics.get("nora_stimulus_total", kind="social", cue="datetime") == 2

    await world.perception().tick()        # a restart: a new pass, the same Record
    assert len(await world.stimuli()) == 2, "the read is announced once"


async def test_a_read_past_the_cap_is_announced_unperceived(world) -> None:
    await world.seed("p1", person="bea", caption="see you at 7pm")
    await world.perception(cap=0).tick()
    assert await world.attempts() == [("p1", "capped", 0)]
    (row,) = await world.stimuli()
    assert (row.unit, row.item, tuple(row.cues)) == ("friends", "post", ("datetime",))


async def test_back_filled_items_raise_no_stimulus(world) -> None:
    await world.seed("old", person="bea", origin="backfill", ago=timedelta(days=40))
    perception = world.perception()
    await perception.tick()
    await perception.tick()
    assert await world.attempts() == [("old", "ok", 1)]
    assert await world.stimuli() == []


async def test_without_comprehension_a_read_is_announced_as_recorded(world) -> None:
    await world.seed("p1", person="bea", kind="reel", collab=True)
    await world.perception(comprehension=False).tick()
    assert await world.attempts() == []
    (row,) = await world.stimuli()
    assert row.item == "collab:reel"


# ---- what may be read -----------------------------------------------------------


async def test_an_opted_out_or_gone_item_is_neither_perceived_nor_announced(world) -> None:
    await world.seed("cy1", person="cy")
    await world.seed("gone", person="bea")
    await world.store.record_state(network="instagram", item_id="gone", state="gone")
    perception = world.perception()
    await perception.tick()
    world.clock.next_day()
    await perception.tick()
    assert world.interpreter.calls == []
    assert await world.attempts() == []
    assert await world.stimuli() == []


# ---- a read is announced whole, in every unit -----------------------------------


class _PassMidRead(FakeMediaSource):
    """Custody that runs the perception pass while it fetches each frame: a
    pass that lands between two of a read's inserts."""

    def __init__(self, during: list[Any]) -> None:
        super().__init__()
        self.during = during

    async def fetch(self, *, ref: Any) -> Any:
        for perception in self.during:
            await perception.tick()
        return await super().fetch(ref=ref)


async def test_a_pass_that_runs_mid_read_waits_for_the_whole_read(world) -> None:
    clock = world.clock
    source = FakeSocialSource()
    source.probes[ADA] = Probe(latest_reel_media=1, media_count=1, total_clips_count=0,
                               counts_available=True)
    source.pages[ADA] = Page(items=(), next_cursor=None)
    frames = tuple(
        StoryFrame(item_id=f"f{n}", taken_at=clock.now - timedelta(minutes=30 - n),
                   expires_at=clock.now + timedelta(hours=20), kind="image",
                   url=f"{_CDN}/f{n}.jpg")
        for n in range(3)
    )
    source.stories[ADA] = StorySnapshot(found=True, frames=frames)
    during: list[Any] = []
    media = _PassMidRead(during)
    for frame in frames:
        media.blobs[f"attachment:{frame.url}"] = f"bytes of {frame.item_id}".encode()
    poller = SocialPoller(
        source=source, store=world.store, health=InMemorySourceHealthLog(),
        roster=functools.partial(instagram_roster, FakeSocialSubjectIndex({"ada": ADA})),
        settings=SocialPollConfig(enabled=True, jitter_seconds=0), metrics=world.metrics,
        media=media, blobs=world.blobs, media_objects=world.media_objects, clock=clock,
    )
    perception = world.perception()
    during.append(perception)

    await poller.tick()
    assert await world.stimuli() == [], "the read was not complete while the pass ran"

    await perception.tick()
    rows = await world.stimuli()
    assert [(r.unit, r.item) for r in rows] == [
        ("friends", "story_frame,story_frame,story_frame"),
        ("family", "story_frame,story_frame,story_frame"),
    ]


async def test_a_unit_whose_stimulus_is_missing_still_gets_it(world) -> None:
    """A crash between two units' appends: the next pass raises the one left."""
    read = await world.seed("p1")
    await SqlStimulusStore(world.engine).append(
        stimulus=NewStimulus(unit="friends", kind="social", item="post",
                             ref=f"social_read:{read}", at_ms=0, person_id="ada"),
        recorded_at_ms=0,
    )
    await world.perception().tick()
    assert sorted(r.unit for r in await world.stimuli()) == ["family", "friends"]


async def test_each_units_stimulus_names_the_room_there_that_lists_the_author(world) -> None:
    """The session reads a social read through the room its stimulus names
    (`composition.social_person_rooms`): a room of that unit listing the
    author. A unit with no room named for them binds its home room."""
    await world.seed("p1")
    await world.perception(rooms={("ada", "friends"): ("signal", "side-room")}).tick()
    rows = {r.unit: (r.platform, r.conversation_id) for r in await world.stimuli()}
    assert rows == {"friends": ("signal", "side-room"), "family": (None, None)}


async def test_a_read_older_than_the_window_wakes_no_unit_added_since(world) -> None:
    """A unit a person joins later is owed their next reads, not a wake for
    each read they had before."""
    read = await world.seed("p1")
    await SqlStimulusStore(world.engine).append(
        stimulus=NewStimulus(unit="friends", kind="social", item="post",
                             ref=f"social_read:{read}", at_ms=0, person_id="ada"),
        recorded_at_ms=0,
    )
    world.clock.now += timedelta(days=3)
    await world.perception().tick()
    assert [r.unit for r in await world.stimuli()] == ["friends"]


# ---- what the cap counts --------------------------------------------------------


class _Windows(FakeMediaInterpreter):
    """A video read that reports the windows it cut, as the real one does."""

    def __init__(self, windows: int) -> None:
        super().__init__("a clip")
        self.windows = windows

    async def describe(self, *, data: bytes, media_type: str, label: str = "",
                       context: str = "") -> MediaDescription:
        got = await super().describe(data=data, media_type=media_type, label=label,
                                     context=context)
        if not got.available or not media_type.startswith("video/"):
            return got
        return replace(got, provenance={"video_windows_cut": self.windows})


async def test_the_cap_counts_a_videos_windows_estimated_before_and_spent_after(world) -> None:
    world.interpreter = _Windows(windows=2)
    # 300 s at 90 s a window: at most 4 windows and a text pass. Read, it cut 2.
    await world.seed("long", media_type="video/mp4", duration=300, ago=timedelta(hours=1))
    # 100 s: at most 2 windows and a text pass, which fits only if the first
    # was charged what it spent (3), not what it might have (5).
    await world.seed("short", media_type="video/mp4", duration=100, ago=timedelta(hours=2))
    # No length from the bridge: all 10 windows and a text pass, over the 1 left.
    await world.seed("unknown", media_type="video/mp4", ago=timedelta(hours=3))
    await world.perception(cap=7).tick()
    assert await world.attempts() == [
        ("long", "ok", 3), ("short", "ok", 3), ("unknown", "capped", 0),
    ]


# ---- a read that cannot succeed -----------------------------------------------


async def test_a_read_that_keeps_failing_stops_after_three_days(world) -> None:
    await world.seed("p1", person="bea")
    world.interpreter.available = False
    perception = world.perception()
    for _ in range(4):
        await perception.tick()
        world.clock.next_day()
    assert await world.attempts() == [
        ("p1", "unavailable", 1), ("p1", "unavailable", 1), ("p1", "failed", 1),
    ]
    assert len(world.interpreter.calls) == 3
    assert len(await world.stimuli()) == 1, "a failed item still settles its read"


async def test_a_permanent_failure_is_final_and_one_with_no_call_costs_nothing(world) -> None:
    await world.seed("big", person="bea")
    await world.seed("lost", person="bea", indexed=False)
    world.interpreter.available = False
    world.interpreter.reason = "too_large"
    perception = world.perception()
    await perception.tick()
    world.clock.next_day()
    await perception.tick()
    assert sorted(await world.attempts()) == [("big", "failed", 0), ("lost", "failed", 0)]
    assert sorted(r or "" for r in await world.reasons()) == ["no_index", "too_large"]
