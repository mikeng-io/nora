"""The social poller (`nora.app.social_poller.SocialPoller`) driven tick by tick:
a `FakeSocialSource` held to the Instagram adapter's answers
(`tests/contract/test_social_source_contract.py`), the real Record in Postgres,
and a clock each test moves by hand.

Every test runs successive ticks, never one batch: the cadence, the marker,
the budget, the halt and the back-off are all state carried from one tick to
the next, and a single tick cannot show any of them.

The clock starts at the real time because a poller that has just started
seeds its schedule from `social_poll.recorded_at`, the database's own clock.

Database scoped to the MODULE and truncated between tests (AGENTS.md).
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.social_poller import SocialPoller
from nora.config import SocialPollConfig
from nora.domain.social_stream import (
    Failed,
    FailureKind,
    ItemRef,
    MediaItem,
    Page,
    Probe,
    SocialItem,
    StoryFrame,
    StorySnapshot,
)
from nora.infra.social.subjects import instagram_roster
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import SOCIAL_ITEM, SOCIAL_ITEM_STATE, SOCIAL_POLL
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeBlobStore,
    FakeMediaObjectStore,
    FakeMediaSource,
    FakeSocialSource,
    FakeSocialSubjectIndex,
)
from nora.testing.stimuli import InMemorySourceHealthLog

ADA, BEA = "10000001", "10000002"
_CDN = "https://scontent.cdninstagram.com/v/t51"


@pytest.fixture(scope="module")
def _db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    dbname = "t_social_poller_" + uuid4().hex
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
        self.start = self.now

    def __call__(self) -> datetime:
        return self.now

    def at(self, **delta: float) -> None:
        self.now = self.start + timedelta(**delta)


@dataclass
class World:
    store: SqlSocialStreamStore
    engine: Any
    clock: Clock = field(default_factory=Clock)
    source: FakeSocialSource = field(default_factory=FakeSocialSource)
    health: InMemorySourceHealthLog = field(default_factory=InMemorySourceHealthLog)
    metrics: Metrics = field(default_factory=Metrics)
    media: FakeMediaSource = field(default_factory=FakeMediaSource)
    blobs: FakeBlobStore = field(default_factory=FakeBlobStore)
    media_objects: FakeMediaObjectStore = field(default_factory=FakeMediaObjectStore)
    subjects: FakeSocialSubjectIndex = field(
        default_factory=lambda: FakeSocialSubjectIndex({"ada": ADA, "bea": BEA}),
    )

    def poller(self, **settings: Any) -> SocialPoller:
        return SocialPoller(
            source=self.source, store=self.store, health=self.health,
            roster=functools.partial(instagram_roster, self.subjects),
            settings=SocialPollConfig(enabled=True, jitter_seconds=0, **settings),
            metrics=self.metrics, media=self.media, blobs=self.blobs,
            media_objects=self.media_objects, clock=self.clock,
        )

    def probes(self) -> list[str]:
        return self.source.reads("probe")

    async def polls(self, person: str) -> list[tuple[str, str, bool, bool]]:
        p = SOCIAL_POLL
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(p.c.stage, p.c.outcome, p.c.advanced_marker, p.c.advanced_counts)
                .where(p.c.person == person).order_by(p.c.recorded_at, p.c.id),
            )).all()
        return [tuple(r) for r in rows]  # type: ignore[misc]

    async def items(self) -> dict[str, str | None]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(SOCIAL_ITEM.c.item_id, SOCIAL_ITEM.c.media_ref),
            )).all()
        return {r.item_id: r.media_ref for r in rows}

    async def states(self) -> list[tuple[str, str]]:
        s = SOCIAL_ITEM_STATE
        async with self.engine.connect() as conn:
            rows = (await conn.execute(sa.select(s.c.item_id, s.c.state).order_by(s.c.id))).all()
        return [(r.item_id, r.state) for r in rows]

    def transitions(self, scope: str = "") -> list[str]:
        return [
            r.outcome for r in self.health.rows
            if r.source == "social.instagram" and r.scope == scope
        ]


@pytest.fixture
async def world(_db: str) -> AsyncIterator[World]:
    engine = create_sql_engine(_db)
    try:
        yield World(store=SqlSocialStreamStore(engine), engine=engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate social_item, social_poll, social_item_state"))
        await engine.dispose()


def probe(marker: int = 0, posts: int = 1, clips: int = 0, **kw: Any) -> Probe:
    return Probe(
        latest_reel_media=marker, media_count=posts, total_clips_count=clips,
        counts_available=True, **kw,
    )


def frame(item_id: str, clock: Clock, *, expires_in: float = 20) -> StoryFrame:
    return StoryFrame(
        item_id=item_id, taken_at=clock.now - timedelta(hours=1),
        expires_at=clock.now + timedelta(hours=expires_in), kind="image",
        url=f"{_CDN}/{item_id}.jpg",
    )


def story(*frames: StoryFrame) -> StorySnapshot:
    return StorySnapshot(found=True, frames=frames)


def held(world: World, *item_ids: str) -> None:
    """The CDN holds bytes for each item's media."""
    for item_id in item_ids:
        world.media.blobs[f"attachment:{_CDN}/{item_id}.jpg"] = f"bytes of {item_id}".encode()


def post(world: World, item_id: str, account: str = ADA) -> ItemRef:
    """A listed post, and the item it reads as."""
    ref = ItemRef(
        account=account, item_id=item_id, shortcode=f"C{item_id}", kind="post",
        taken_at=world.clock.now - timedelta(days=1), caption="c", collab=False,
    )
    world.source.items[item_id] = SocialItem(
        network="instagram", item_id=item_id, account=account, kind="post",
        taken_at=ref.taken_at, expires_at=None, caption="c", alt_text=None,
        media=(MediaItem("image", f"{_CDN}/{item_id}.jpg"),),
    )
    held(world, item_id)
    return ref


def quiet(world: World, *accounts: str) -> None:
    """No story up and counts already known, so a probe reads nothing: the
    counts are recorded by the first probe (cold start makes a list read due,
    and an empty first page completes it)."""
    for account in accounts:
        world.source.probes[account] = probe()
        world.source.pages[account] = Page(items=(), next_cursor=None)


# ---- cadence ----------------------------------------------------------------


async def test_the_ceiling_reprobes_and_the_floor_holds_back_a_failed_probe(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.UNAVAILABLE, "503")
    poller = world.poller()

    await poller.tick()
    assert world.probes() == [ADA, BEA]

    world.clock.at(minutes=10)
    await poller.tick()
    assert world.probes() == [ADA, BEA], "inside the floor nobody is probed again"

    world.source.probes[ADA] = probe()
    world.source.pages[ADA] = Page(items=(), next_cursor=None)
    world.clock.at(minutes=30)
    await poller.tick()
    assert world.probes() == [ADA, BEA, ADA], "the floor passed; bea is inside the ceiling"

    world.clock.at(hours=6)
    await poller.tick()
    assert world.probes()[3:] == [BEA], "ada was answered at 30 min, so she is not due yet"

    world.clock.at(hours=6, minutes=30)
    await poller.tick()
    assert world.probes()[4:] == [ADA]


async def test_a_restarted_poller_keeps_the_schedule_it_left(world) -> None:
    quiet(world, ADA, BEA)
    await world.poller().tick()
    assert world.probes() == [ADA, BEA]

    world.clock.at(minutes=40)
    await world.poller().tick()
    assert world.probes() == [ADA, BEA], "a new poller seeds from social_poll, not from nothing"


async def test_the_budget_stops_a_tick_and_frees_up_after_an_hour(world) -> None:
    quiet(world, ADA, BEA)
    poller = world.poller(hourly_budget=3)  # a probe costs 2, the cold list read 1

    await poller.tick()
    assert world.probes() == [ADA]
    assert world.metrics.get(
        "nora_social_poll_skipped_total", network="instagram", reason="budget",
    ) == 1

    world.clock.at(minutes=30)
    await poller.tick()
    assert world.probes() == [ADA], "the hour's calls are spent"

    world.clock.at(minutes=61)
    await poller.tick()
    assert world.probes() == [ADA, BEA]
    assert world.metrics.get(
        "nora_social_upstream_calls_total", network="instagram", stage="probe",
    ) == 4


async def test_the_member_answered_longest_ago_is_probed_first(world) -> None:
    quiet(world, ADA)
    world.source.probes[BEA] = Failed(FailureKind.UNAVAILABLE, "503")
    poller = world.poller()
    await poller.tick()

    quiet(world, BEA)
    world.clock.at(hours=6, minutes=5)
    await poller.tick()
    assert world.probes() == [ADA, BEA, BEA, ADA], "bea was never answered"


# ---- probe, then read -------------------------------------------------------


async def test_a_story_is_read_once_per_marker_value(world) -> None:
    quiet(world, ADA, BEA)
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = story(frame("f1", world.clock))
    held(world, "f1")
    poller = world.poller()

    await poller.tick()
    assert world.source.reads("story") == [ADA]
    assert ("story", "read_ok", True, False) in await world.polls("ada")

    world.clock.at(hours=6)
    await poller.tick()
    assert world.source.reads("story") == [ADA], "the same marker never reads again"

    world.source.probes[ADA] = probe(marker=200)
    world.source.stories[ADA] = StorySnapshot(found=False)
    world.clock.at(hours=12)
    await poller.tick()
    world.clock.at(hours=18)
    await poller.tick()
    assert world.source.reads("story") == [ADA, ADA], (
        "a read that found nothing is complete: it advanced the marker"
    )
    assert await world.store.read_marker(network="instagram", person="ada") == 200


async def test_a_failed_list_read_leaves_the_rise_due_on_the_next_probe(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=3)
    world.source.pages[ADA] = Failed(FailureKind.UNAVAILABLE, "503")
    poller = world.poller()

    await poller.tick()
    assert await world.store.last_counts(network="instagram", person="ada") is None

    world.source.pages[ADA] = Page(items=(post(world, "p1"),), next_cursor=None)
    world.clock.at(hours=6)
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA, ADA], "the rise was still due"
    assert world.source.reads("item") == ["p1"]
    counts = await world.store.last_counts(network="instagram", person="ada")
    assert counts is not None and counts.media_count == 3

    world.clock.at(hours=12)
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA, ADA], "counts unchanged: nothing due"
    assert [r[:2] for r in await world.polls("ada")] == [
        ("probe", "items_due"), ("items", "error"),
        ("probe", "items_due"), ("item", "read_ok"), ("items", "read_ok"),
        ("probe", "unchanged"),
    ]


async def test_a_list_read_cut_short_by_the_budget_does_not_advance_the_counts(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=2)
    world.source.pages[ADA] = Page(
        items=(post(world, "p1"), post(world, "p2")), next_cursor=None,
    )
    poller = world.poller(hourly_budget=4)  # probe 2, list 1, one item

    await poller.tick()
    assert world.source.reads("item") == ["p1"]
    assert await world.store.last_counts(network="instagram", person="ada") is None

    world.clock.at(hours=6)
    await poller.tick()
    assert world.probes() == [ADA, BEA], "bea, never answered, went first and spent the hour"

    world.clock.at(hours=7, minutes=5)
    await poller.tick()
    assert world.source.reads("item") == ["p1", "p2"], "p1 is recorded and not read again"
    assert await world.store.last_counts(network="instagram", person="ada") is not None


# ---- halt, back-off, skip ---------------------------------------------------


async def test_a_409_halts_every_call_until_the_session_is_healthy(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.HALT, "needs_human")
    poller = world.poller()

    await poller.tick()
    assert world.probes() == [ADA], "the halt stops the tick: bea is not probed"
    assert world.transitions() == ["up", "halted"]

    world.source.checkpointed = True
    world.clock.at(hours=6)
    await poller.tick()
    world.clock.at(hours=7)
    await poller.tick()
    assert world.probes() == [ADA], "never retried while the session needs a human"

    world.source.checkpointed = False
    quiet(world, ADA)
    world.clock.at(hours=8)
    await poller.tick()
    assert world.probes() == [ADA, ADA, BEA]
    assert world.transitions() == ["up", "halted", "up"]


async def test_a_502_backs_off_doubling_and_one_clean_probe_resets_it(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    world.source.probes[BEA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    poller = world.poller()

    await poller.tick()
    assert world.probes() == [ADA]
    world.clock.at(minutes=10)
    await poller.tick()
    assert world.probes() == [ADA], "inside the 15 minute pause"

    world.clock.at(minutes=20)
    await poller.tick()
    assert world.probes() == [ADA, BEA], "ada is inside the floor; bea fails again"
    world.clock.at(minutes=45)
    await poller.tick()
    assert world.probes() == [ADA, BEA], "the pause doubled to 30 minutes"

    quiet(world, ADA, BEA)
    world.clock.at(minutes=51)
    await poller.tick()
    assert world.probes() == [ADA, BEA, ADA, BEA]

    world.source.probes[ADA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    world.clock.at(hours=7)
    await poller.tick()
    details = [r.detail for r in world.health.rows if r.outcome == "backed_off"]
    assert details == ["upstream_rejected 900s", "upstream_rejected 1800s",
                       "upstream_rejected 900s"], "the clean probe reset the step"
    assert world.transitions() == ["up", "backed_off", "backed_off", "up", "backed_off"]


async def test_a_rate_limited_counts_half_backs_the_login_off(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Probe(
        latest_reel_media=100, media_count=None, total_clips_count=None,
        counts_available=False, rate_limited=True,
    )
    await world.poller().tick()
    assert world.probes() == [ADA]
    assert world.source.reads("story") == [], "a backed-off login reads nothing"
    assert world.transitions() == ["up", "backed_off"]


async def test_a_503_skips_that_call_and_backs_nothing_off(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.UNAVAILABLE, "503")
    await world.poller().tick()
    assert world.probes() == [ADA, BEA]
    assert (await world.polls("ada"))[0][:2] == ("probe", "error")
    assert world.transitions() == ["up"]


async def test_a_stale_story_query_halts_story_reads_only(world) -> None:
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = Failed(FailureKind.STALE_QUERY, "stale_query")
    world.source.pages[ADA] = Page(items=(), next_cursor=None)
    world.source.probes[BEA] = probe(marker=300, posts=1)
    world.source.stories[BEA] = story(frame("b1", world.clock))
    world.source.pages[BEA] = Page(items=(post(world, "p9", BEA),), next_cursor=None)
    held(world, "b1")
    poller = world.poller()

    await poller.tick()
    assert world.source.reads("story") == [ADA], "bea's story is not tried while it is stale"
    assert world.source.reads("items") == [ADA, BEA], "the list reads and the login carry on"
    assert world.transitions("story") == ["stale_query"]
    assert world.transitions() == ["up"]

    world.source.stories[ADA] = story(frame("a1", world.clock))
    held(world, "a1")
    world.clock.at(hours=6)
    await poller.tick()
    assert world.source.reads("story") == [ADA, ADA, BEA], "tried again once per ceiling"
    assert world.transitions("story") == ["stale_query", "up"]


# ---- the Record and custody -------------------------------------------------


async def test_a_frame_missing_from_a_later_read_before_its_expiry_is_gone(world) -> None:
    quiet(world, ADA, BEA)
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = story(
        frame("f1", world.clock), frame("f2", world.clock),
        frame("f3", world.clock, expires_in=2),
    )
    held(world, "f1", "f2", "f3")
    poller = world.poller()
    await poller.tick()

    world.source.probes[ADA] = probe(marker=200)
    world.source.stories[ADA] = story(frame("f2", world.clock))
    world.clock.at(hours=6)
    await poller.tick()
    assert await world.states() == [("f1", "gone")], "f3 expired: absence is not deletion"

    world.source.probes[ADA] = probe(marker=300)
    world.clock.at(hours=12)
    await poller.tick()
    assert await world.states() == [("f1", "gone")], "a frame is recorded gone once"


async def test_custody_runs_once_per_new_item_and_never_for_a_dedupe(world) -> None:
    quiet(world, ADA, BEA)
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = story(frame("f1", world.clock))
    held(world, "f1", "f2")
    poller = world.poller()
    await poller.tick()

    world.source.probes[ADA] = probe(marker=200)
    world.source.stories[ADA] = story(frame("f1", world.clock), frame("f2", world.clock))
    world.clock.at(hours=6)
    await poller.tick()

    assert world.media.fetched == [f"attachment:{_CDN}/f1.jpg", f"attachment:{_CDN}/f2.jpg"]
    items = await world.items()
    assert set(items) == {"f1", "f2"}
    assert items["f1"] in world.blobs.blobs and items["f2"] in world.blobs.blobs
    assert {o.source for o in world.media_objects.saved} == {"social"}
    assert world.metrics.get(
        "nora_social_custody_total", network="instagram", outcome="stored",
    ) == 2


async def test_an_item_whose_bytes_did_not_arrive_is_recorded_without_them(world) -> None:
    quiet(world, ADA, BEA)
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = story(frame("f1", world.clock))  # nothing on the CDN
    await world.poller().tick()
    assert await world.items() == {"f1": None}
    assert world.metrics.get(
        "nora_social_custody_total", network="instagram", outcome="not_found",
    ) == 1


# ---- membership ---------------------------------------------------------------


async def test_the_poller_reads_stories_for_members_only(world) -> None:
    """Members are the people listed in a room (`SocialSubjectIndex.members`)
    with exactly one igid: `cal` holds an igid and is in no room, `dee` holds
    two. Neither is ever probed, so neither's story is ever read."""
    world.subjects = FakeSocialSubjectIndex(
        {"ada": ADA, "cal": "10000003", "dee": frozenset({"10000004", "10000005"})},
        out_of_reach=frozenset({"cal"}),
    )
    for account in (ADA, "10000003", "10000004", "10000005"):
        world.source.probes[account] = probe(marker=100)
        world.source.stories[account] = StorySnapshot(found=False)
        world.source.pages[account] = Page(items=(), next_cursor=None)
    await world.poller().tick()
    assert world.probes() == [ADA]
    assert world.source.reads("story") == [ADA]


async def test_a_member_who_set_social_off_is_never_probed(world) -> None:
    """`social = false` in `identity.toml` (`SocialSubjectIndex.members`)
    takes a person listed in a room back out of the stream on every tick."""
    world.subjects = FakeSocialSubjectIndex(
        {"ada": ADA, "bea": BEA}, social_off=frozenset({"bea"}),
    )
    quiet(world, ADA, BEA)
    poller = world.poller()
    await poller.tick()
    world.clock.at(hours=6)
    await poller.tick()
    assert world.probes() == [ADA, ADA]
