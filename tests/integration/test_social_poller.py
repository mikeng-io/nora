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

import asyncio
import copy
import dataclasses
import functools
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.social_poller import SocialPoller, relist_max_pages
from nora.config import SocialPollConfig
from nora.domain.social_stream import (
    Failed,
    FailureKind,
    ItemCounts,
    MediaItem,
    Page,
    Probe,
    SocialItem,
    StoryFrame,
    StorySnapshot,
)
from nora.infra.social.instagram_source import InstagramSocialSource, _when
from nora.infra.social.subjects import instagram_roster
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    EVENTS,
    MESSAGES,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
    ROLLUP_OUTING,
    SOCIAL_ITEM,
    SOCIAL_ITEM_COUNT,
    SOCIAL_ITEM_STATE,
    SOCIAL_POLL,
)
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
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instagram_bridge"


def _bridge_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text())


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
            # The re-walk and the recount are off unless a test asks for
            # them: the back-fill's tests count the back-fill's own pages.
            settings=SocialPollConfig(
                enabled=True, jitter_seconds=0,
                **{"relist_pages_per_tick": 0, "recount_interval_hours": 0, **settings},
            ),
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
            await conn.execute(sa.text(
                "truncate social_item, social_poll, social_item_state, social_item_count, "
                "messages, events, rollup_build, rollup_outing, rollup_colocation cascade",
            ))
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


def post(
    world: World, item_id: str, account: str = ADA, *, age: timedelta = timedelta(days=1),
) -> SocialItem:
    """A listed post, media included, and the CDN holding its bytes."""
    held(world, item_id)
    return SocialItem(
        network="instagram", item_id=item_id, account=account, kind="post",
        taken_at=world.clock.now - age, expires_at=None, caption="c",
        alt_text=None, media=(MediaItem("image", f"{_CDN}/{item_id}.jpg"),),
    )


def quiet(world: World, *accounts: str) -> None:
    """No story up and counts already known, so a probe reads nothing: the
    counts are recorded by the first probe (cold start makes a list read due,
    and an empty first page completes it)."""
    for account in accounts:
        world.source.probes[account] = probe(posts=0)
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


async def test_a_member_whose_probe_keeps_failing_does_not_starve_the_rest(world) -> None:
    """Least recently probed goes first, answered or not: ada's probe fails
    every time and is never answered, and bea still gets her turn."""
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.UNAVAILABLE, "503")
    poller = world.poller(hourly_budget=3)  # one probe and a list read an hour

    await poller.tick()
    assert world.probes() == [ADA]
    world.clock.at(minutes=61)
    await poller.tick()
    assert world.probes() == [ADA, BEA]


async def test_a_probe_that_will_not_change_waits_for_the_ceiling(world) -> None:
    """An account the platform does not know is not retried every floor."""
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.NOT_FOUND, "not_found")
    poller = world.poller()
    await poller.tick()
    world.clock.at(minutes=31)
    await poller.tick()
    assert world.probes() == [ADA, BEA]
    world.clock.at(hours=6)
    await poller.tick()
    assert world.probes() == [ADA, BEA, ADA, BEA]


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


async def test_a_tagged_place_is_recorded_for_a_post_a_reel_and_a_story_frame(world) -> None:
    """The source's `location` reaches `social_item.location` on every kind
    the poller records; an untagged item records none."""
    quiet(world, BEA)
    world.source.probes[ADA] = probe(marker=100, posts=3)
    tagged = dataclasses.replace(post(world, "p1"), location="Placeholder Square")
    reel = dataclasses.replace(post(world, "r1"), kind="reel", location="Placeholder Pier")
    world.source.pages[ADA] = Page(items=(tagged, reel, post(world, "p2")), next_cursor=None)
    world.source.stories[ADA] = story(
        dataclasses.replace(frame("f1", world.clock), location="Placeholder Harbour"),
    )
    held(world, "f1")
    await world.poller().tick()
    async with world.engine.connect() as conn:
        rows = dict((await conn.execute(
            sa.select(SOCIAL_ITEM.c.item_id, SOCIAL_ITEM.c.location),
        )).tuples().all())
    assert rows == {
        "p1": "Placeholder Square", "r1": "Placeholder Pier", "p2": None,
        "f1": "Placeholder Harbour",
    }


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
    assert world.source.reads("item") == [], "a listed item is never read on its own"
    assert (await world.items())["p1"], "recorded with its media_ref"
    counts = await world.store.last_counts(network="instagram", person="ada")
    assert counts is not None and counts.media_count == 3

    world.clock.at(hours=12)
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA, ADA], "counts unchanged: nothing due"
    assert [r[:2] for r in await world.polls("ada")] == [
        ("probe", "items_due"), ("backfill", "error"),
        ("probe", "items_due"), ("backfill", "read_ok"),
        ("probe", "unchanged"),
    ], "a first list read is the back-fill's first page"


async def test_a_listed_page_records_every_new_item_with_its_media_and_reads_no_post(world) -> None:
    """Through the real Instagram adapter over the bridge's own `items` body:
    the page's media is what is kept, and `/posts` is never asked."""
    answers = {
        "account": _bridge_fixture("account_ok.json"),
        "story": _bridge_fixture("story_not_found.json"),
        "items": _bridge_fixture("items_last_page.json"),
    }
    served: list[str] = []

    def route(name: str) -> Any:
        async def handler(_: web.Request) -> web.Response:
            served.append(name)
            return web.json_response(answers[name]["body"], status=answers[name]["status"])
        return handler

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"session": "authenticated"})

    async def posts(_: web.Request) -> web.Response:
        served.append("post")
        return web.json_response({"code": "unsupported"}, status=501)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/accounts/{igid}", route("account"))
    app.router.add_get("/accounts/{igid}/story", route("story"))
    app.router.add_get("/accounts/{igid}/items", route("items"))
    app.router.add_get("/posts/{shortcode}", posts)
    listed = answers["items"]["body"]["items"]
    for row in listed:
        for media in row["items"]:
            world.media.blobs[f"attachment:{media['url']}"] = b"bytes"
    async with TestServer(app) as server:
        poller = SocialPoller(
            source=InstagramSocialSource(base_url=str(server.make_url("")), timeout_seconds=5),
            store=world.store, health=world.health,
            roster=functools.partial(instagram_roster, FakeSocialSubjectIndex({"ada": ADA})),
            settings=SocialPollConfig(enabled=True, jitter_seconds=0),
            metrics=world.metrics, media=world.media, blobs=world.blobs,
            media_objects=world.media_objects, clock=world.clock,
        )
        await poller.tick()
    assert "items" in served and "post" not in served
    kept = await world.items()
    assert set(kept) == {row["pk"] for row in listed}
    assert all(kept[row["pk"]] for row in listed if row["items"]), "every item has media_ref"
    assert any(len(ref.split(",")) == 2 for ref in kept.values() if ref), "a carousel keeps both"


async def test_a_page_records_each_new_item_once_and_never_reads_it(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=2)
    p1, p2 = post(world, "p1"), post(world, "p2")
    world.source.pages[ADA] = Page(items=(p1, p2, p1), next_cursor=None)
    poller = world.poller(hourly_budget=3)  # probe 2, list 1: no room for an item read

    await poller.tick()
    assert world.source.reads("item") == []
    assert set(await world.items()) == {"p1", "p2"}
    fetched = list(world.media.fetched)
    assert fetched and len(fetched) == len(set(fetched)), "a repeat on the page is fetched once"
    assert await world.store.last_counts(network="instagram", person="ada") is not None

    world.source.probes[ADA] = probe(posts=3)
    world.source.pages[ADA] = Page(items=(post(world, "p3"), p1, p2), next_cursor=None)
    world.clock.at(hours=7, minutes=5)
    await world.poller().tick()
    assert set(await world.items()) == {"p1", "p2", "p3"}, "recorded ones are not recorded again"
    again = world.media.fetched[len(fetched):]
    assert again and not set(again) & set(fetched), "only the new item's media is fetched"


async def test_an_unsupported_answer_is_not_spent_against_the_budget(world) -> None:
    """The bridge refusing locally sent nothing upstream."""
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Failed(FailureKind.UNSUPPORTED, "unsupported")
    poller = world.poller()
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA]
    assert sum(c for _, c in poller._spent) == 5, "both probes and bea's list read, not ada's"


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
    assert world.probes() == [ADA], "a cleared checkpoint waits out the top back-off step"
    assert world.transitions() == ["up", "halted", "up", "backed_off"]

    world.clock.at(hours=14, minutes=1)
    await poller.tick()
    assert world.probes() == [ADA, BEA, ADA], "least recently probed first"


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



# ---- durability and ordering of a halt or back-off ---------------------------


class _RowsFail(SqlSocialStreamStore):
    """The real store, refusing every `social_poll` row whose outcome is in
    `refuse`: the database failing at the worst moment."""

    refuse: frozenset[str] = frozenset()

    async def record_poll(self, **kw: Any) -> None:
        if kw.get("outcome") in self.refuse:
            raise RuntimeError("database refused the row")
        await super().record_poll(**kw)


@pytest.mark.parametrize(("failure", "outcome", "state"), [
    (Failed(FailureKind.HALT, "needs_human"), "halted", "halted"),
    (Failed(FailureKind.BACK_OFF, "upstream_rejected"), "backed_off", "backed_off"),
])
async def test_a_halt_or_back_off_holds_when_its_row_cannot_be_written(
    world, failure, outcome, state,
) -> None:
    store = _RowsFail(world.engine)
    store.refuse = frozenset({outcome})
    world.store = store
    quiet(world, BEA)
    world.source.probes[ADA] = failure
    await world.poller().tick()
    assert world.probes() == [ADA], "the tick stopped: bea was not probed"
    assert world.transitions() == ["up", state]


async def test_a_restart_mid_back_off_waits_it_out_and_keeps_doubling(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    world.source.probes[BEA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    await world.poller().tick()

    world.clock.at(minutes=10)
    await world.poller().tick()
    assert world.probes() == [ADA], "a new process still waits out the 15 minutes"

    world.clock.at(minutes=20)
    await world.poller().tick()
    assert world.probes() == [ADA, BEA]
    details = [r.detail for r in world.health.rows if r.outcome == "backed_off"]
    assert details == ["upstream_rejected 900s", "upstream_rejected 1800s"]


async def test_a_restart_that_cannot_read_the_login_history_probes_nothing(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    await world.poller().tick()

    restarted = world.poller()
    world.health.fail = True
    world.clock.at(minutes=10)
    with pytest.raises(RuntimeError):
        await restarted.tick()
    world.health.fail = False
    world.clock.at(minutes=12)
    await restarted.tick()
    assert world.probes() == [ADA], "the back-off is read on the next tick, not forgotten"


async def test_a_restart_after_a_halt_resumes_on_the_ladder(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.HALT, "needs_human")
    await world.poller().tick()

    quiet(world, ADA)
    world.clock.at(hours=1)
    await world.poller().tick()
    assert world.probes() == [ADA], "the restarted process still treats the halt as live"
    assert world.transitions()[-2:] == ["up", "backed_off"]


async def test_two_halts_in_a_day_lock_the_login_until_an_operator_clears_it(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = Failed(FailureKind.HALT, "needs_human")
    poller = world.poller()
    await poller.tick()
    world.clock.at(hours=1)
    await poller.tick()                       # healthy: up, then the top step
    world.clock.at(hours=7, minutes=1)
    await poller.tick()                       # ada halts again
    assert world.transitions()[-2:] == ["halted", "locked"]

    assert world.probes() == [ADA, BEA, ADA]
    world.clock.at(hours=20)
    await poller.tick()
    await world.poller().tick()               # a restart does not clear it
    assert world.probes() == [ADA, BEA, ADA]

    await world.health.record(
        source="social.instagram", scope="", event="transition", outcome="cleared",
        at_ms=int(world.clock.now.timestamp() * 1000),
    )
    quiet(world, ADA)
    world.clock.at(hours=21)
    await poller.tick()
    assert world.probes() == [ADA, BEA, ADA, ADA, BEA]


async def test_a_restart_counts_the_last_hours_calls_against_the_budget(world) -> None:
    quiet(world, ADA, BEA)
    await world.poller(hourly_budget=3).tick()
    assert world.probes() == [ADA]

    world.clock.at(minutes=10)
    await world.poller(hourly_budget=3).tick()
    assert world.probes() == [ADA], "the hour's three calls are still spent"


async def test_the_first_call_of_a_tick_is_jittered_too(world) -> None:
    quiet(world, ADA)
    world.subjects = FakeSocialSubjectIndex({"ada": ADA})
    world.source.pages[ADA] = Failed(FailureKind.UNAVAILABLE, "503")
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    poller = SocialPoller(
        source=world.source, store=world.store, health=world.health,
        roster=functools.partial(instagram_roster, world.subjects),
        settings=SocialPollConfig(enabled=True, jitter_seconds=10),
        metrics=world.metrics, clock=world.clock, sleep=sleep, rand=lambda: 0.5,
    )
    await poller.tick()
    assert pauses == [5.0, 5.0], "one pause before the probe, one before the list read"


async def test_a_first_list_read_is_recorded_as_backfill_and_later_ones_as_poll(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(post(world, "p1"),), next_cursor=None)
    poller = world.poller()
    await poller.tick()

    world.source.probes[ADA] = probe(posts=2)
    world.source.pages[ADA] = Page(items=(post(world, "p2"), post(world, "p1")), next_cursor=None)
    world.clock.at(hours=6)
    await poller.tick()
    async with world.engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(SOCIAL_ITEM.c.item_id, SOCIAL_ITEM.c.origin).order_by(SOCIAL_ITEM.c.item_id),
        )).all()
    assert [tuple(r) for r in rows] == [("p1", "backfill"), ("p2", "poll")]


async def test_a_post_with_no_usable_media_is_recorded_and_not_read_again(world) -> None:
    """Through the real Instagram adapter, over a bridge whose listed media is
    off the CDN."""
    listing = copy.deepcopy(_bridge_fixture("items_last_page.json"))
    for row in listing["body"]["items"]:
        row["items"] = [{"kind": "image", "url": "https://evil.test/a.jpg"}]
    answers = {
        "account": _bridge_fixture("account_ok.json"),
        "story": _bridge_fixture("story_not_found.json"),
        "items": listing,
    }
    served: list[str] = []

    def route(name: str) -> Any:
        async def handler(_: web.Request) -> web.Response:
            served.append(name)
            return web.json_response(answers[name]["body"], status=answers[name]["status"])
        return handler

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"session": "authenticated"})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/accounts/{igid}", route("account"))
    app.router.add_get("/accounts/{igid}/story", route("story"))
    app.router.add_get("/accounts/{igid}/items", route("items"))
    async with TestServer(app) as server:
        poller = SocialPoller(
            source=InstagramSocialSource(base_url=str(server.make_url("")), timeout_seconds=5),
            store=world.store, health=world.health,
            roster=functools.partial(instagram_roster, FakeSocialSubjectIndex({"ada": ADA})),
            settings=SocialPollConfig(enabled=True, jitter_seconds=0, relist_pages_per_tick=0),
            metrics=world.metrics, clock=world.clock,
        )
        await poller.tick()
        reads = served.count("items")
        recorded = await world.items()
        assert recorded and all(ref is None for ref in recorded.values())
        world.clock.at(hours=6)
        await poller.tick()
    assert served.count("items") == reads, "the list read completed; nothing is read again"
    assert "post" not in served


# ---- the back-fill ------------------------------------------------------------


def grid(
    world: World, account: str, items: int, *, every: timedelta = timedelta(days=1),
) -> None:
    """`account` has posted `items` posts, one every `every` going back from
    a day ago, listed 12 a page newest first like the bridge's timeline: the
    first page in `pages`, each later one under the cursor the page before
    hands on (`<account>-c<n>`), the last handing on none. The probe's count
    makes the first list read due."""
    posts = [
        post(world, f"{account}-{k}", account, age=timedelta(days=1) + every * k)
        for k in range(items)
    ]
    chunks = [tuple(posts[i:i + 12]) for i in range(0, items, 12)] or [()]
    for n, chunk in enumerate(chunks):
        page = Page(items=chunk, next_cursor=f"{account}-c{n + 1}" if n + 1 < len(chunks) else None)
        if n == 0:
            world.source.pages[account] = page
        else:
            world.source.after[f"{account}-c{n}"] = page
    world.source.probes[account] = probe(posts=items)


async def backfilled(world: World, person: str) -> int:
    i = SOCIAL_ITEM
    async with world.engine.connect() as conn:
        return (await conn.execute(
            sa.select(sa.func.count()).where(i.c.person == person, i.c.origin == "backfill"),
        )).scalar_one()


async def ticks(world: World, poller: SocialPoller, n: int, *, every: int = 10) -> None:
    """`n` ticks `every` minutes apart, after the clock's current time: inside
    the ceiling, so nobody is probed again and each tick is the back-fill's."""
    start = world.clock.now
    for k in range(1, n + 1):
        world.clock.now = start + timedelta(minutes=every * k)
        await poller.tick()


def ended(world: World, reason: str) -> float:
    return world.metrics.get(
        "nora_social_backfill_ended_total", network="instagram", reason=reason,
    )


async def test_the_backfill_pages_back_until_fifty_items(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 80)
    poller = world.poller()

    await poller.tick()
    assert world.source.cursors == [None, None], "each member's first list read, nothing more"
    assert await backfilled(world, "ada") == 12

    await ticks(world, poller, 6)
    assert world.source.cursors[2:] == [f"{ADA}-c{n}" for n in range(1, 5)], (
        "one page a tick, newest first, and none after the limit"
    )
    assert await backfilled(world, "ada") == 50, "the first page's 12 count toward the 50"
    assert ended(world, "max_items") == 1
    assert world.metrics.get(
        "nora_social_backfill_items_total", network="instagram", outcome="past_limit",
    ) == 10, "the rest of the fifth page is not recorded"
    assert [r[:2] for r in await world.polls("ada")][-1] == ("backfill", "read_ok")


async def test_the_backfill_stops_at_ninety_days(world) -> None:
    """One post every three days: the 31st is past 90 days. It is on the
    third page, which records the six before it and ends the walk: every
    page after it is older still."""
    quiet(world, BEA)
    grid(world, ADA, 60, every=timedelta(days=3))
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 5)
    assert world.source.cursors[2:] == [f"{ADA}-c1", f"{ADA}-c2"], "no page past the limit"
    assert await backfilled(world, "ada") == 30, "every post inside 90 days, none past it"
    assert ended(world, "max_days") == 1
    assert world.metrics.get(
        "nora_social_backfill_items_total", network="instagram", outcome="past_limit",
    ) == 6


async def test_an_old_pinned_post_on_the_first_page_does_not_end_the_backfill(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 30)
    first = world.source.pages[ADA]
    assert isinstance(first, Page)
    pinned = post(world, "pinned", age=timedelta(days=400))
    world.source.pages[ADA] = Page(items=(pinned, *first.items), next_cursor=first.next_cursor)
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 3)
    assert world.source.cursors[2:] == [f"{ADA}-c1", f"{ADA}-c2"]
    assert await backfilled(world, "ada") == 31


async def test_the_backfill_stops_at_the_end_of_the_grid(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 20)
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 4)
    assert world.source.cursors[2:] == [f"{ADA}-c1"]
    assert await backfilled(world, "ada") == 20
    assert ended(world, "grid_end") == 2, "bea's empty grid ended on her first read"


async def test_the_backfill_never_takes_what_the_hour_owes_the_probes(world) -> None:
    """Two members: a page needs a probe and a read of each (6) left over."""
    grid(world, ADA, 30)
    grid(world, BEA, 30)
    poller = world.poller(hourly_budget=10)

    await poller.tick()
    assert sum(c for _, c in poller._spent) == 6, "two probes and two first list reads"
    await ticks(world, poller, 5)
    assert world.source.cursors == [None, None], "4 left in the hour: no page fits"

    world.clock.at(minutes=61)
    await poller.tick()
    assert world.source.cursors[2:] == [f"{ADA}-c1", f"{BEA}-c1"], (
        "the hour turned over: what the probes may need is back, and a page fits"
    )


@pytest.mark.parametrize(
    ("failure", "transition"),
    [
        (Failed(FailureKind.HALT, "needs_human"), "halted"),
        (Failed(FailureKind.BACK_OFF, "upstream_rejected"), "backed_off"),
    ],
)
async def test_a_halt_or_back_off_on_a_backfill_page_stops_the_backfill(
    world, failure: Failed, transition: str,
) -> None:
    grid(world, ADA, 30)
    grid(world, BEA, 30)
    world.source.after[f"{ADA}-c1"] = failure
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 1)
    assert world.source.cursors == [None, None, f"{ADA}-c1"], "bea's page is not read"
    assert world.transitions()[-1] == transition
    await ticks(world, poller, 1)
    assert world.source.cursors == [None, None, f"{ADA}-c1"], "and nothing while it holds"


async def test_a_restarted_poller_resumes_the_backfill_where_it_stood(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 40)
    first = world.poller()
    await first.tick()
    await ticks(world, first, 1)
    assert world.source.cursors == [None, None, f"{ADA}-c1"]

    second = world.poller()
    await ticks(world, second, 4)
    assert world.source.cursors[2:] == [f"{ADA}-c1", f"{ADA}-c2", f"{ADA}-c3"], (
        "the new poller went on from the cursor the last page left: no page twice"
    )
    assert await backfilled(world, "ada") == 40


async def test_a_cursor_the_bridge_refuses_ends_the_backfill(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 40)
    world.source.after[f"{ADA}-c1"] = Failed(FailureKind.UNAVAILABLE, "invalid_id")
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 3)
    assert world.source.cursors == [None, None, f"{ADA}-c1"], "never sent again"
    assert [r[:2] for r in await world.polls("ada")][-2:] == [
        ("backfill", "error"), ("backfill", "abandoned"),
    ]
    assert ended(world, "bad_cursor") == 1
    assert world.transitions() == ["up"], "the login is fine"


async def test_a_cursor_refused_upstream_twice_ends_the_backfill(world) -> None:
    """Each refusal backs the login off; a third would too."""
    quiet(world, BEA)
    grid(world, ADA, 40)
    world.source.after[f"{ADA}-c1"] = Failed(FailureKind.BACK_OFF, "upstream_rejected")
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 1)
    world.clock.at(minutes=30)
    await poller.tick()
    assert world.source.cursors == [None, None, f"{ADA}-c1", f"{ADA}-c1"]
    assert [r[:2] for r in await world.polls("ada")][-2:] == [
        ("backfill", "backed_off"), ("backfill", "backed_off"),
    ], "the two refusals' own rows end the walk"
    world.clock.at(hours=2)
    await ticks(world, poller, 3)
    assert world.source.cursors.count(f"{ADA}-c1") == 2, "never sent a third time"
    assert ended(world, "bad_cursor") == 1


async def test_the_backfill_reads_at_most_its_pages_a_tick(world) -> None:
    world.subjects = FakeSocialSubjectIndex({"ada": ADA, "bea": BEA, "cy": "10000003"})
    for account in (ADA, BEA, "10000003"):
        grid(world, account, 40)
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 1)
    assert world.source.cursors[3:] == [f"{ADA}-c1", f"{BEA}-c1"], "two pages, one a member"
    await ticks(world, poller, 1)
    assert world.source.cursors[5:] == ["10000003-c1", f"{ADA}-c2"], "least recently paged first"


async def test_a_member_who_set_social_off_is_never_backfilled(world) -> None:
    grid(world, ADA, 40)
    grid(world, BEA, 40)
    poller = world.poller()
    await poller.tick()

    world.subjects.social_off = frozenset({"bea"})
    await ticks(world, poller, 3)
    assert world.source.cursors[2:] == [f"{ADA}-c1", f"{ADA}-c2", f"{ADA}-c3"]


async def test_a_first_page_read_again_leaves_a_new_post_to_the_poll(world) -> None:
    """A member whose first list read predates the back-fill posts: the
    back-fill reads their first page again for its cursor before the probe
    sees the count rise. The post is the poll's, as news, with its read."""
    store = world.store
    await store.insert_item(
        post(world, "old", age=timedelta(days=5)), person="ada", origin="backfill",
        seen_at=world.clock.now,
    )
    await store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="items_due",
        latest_reel_media=0, media_count=1, total_clips_count=0,
    )
    await store.record_poll(
        network="instagram", person="ada", stage="items", outcome="read_ok",
        media_count=1, total_clips_count=0, advanced_counts=True,
    )
    quiet(world, BEA)
    new = post(world, "new", age=timedelta(hours=1))
    old = tagged(post(world, "old", age=timedelta(days=5)), "Placeholder Square")
    world.source.pages[ADA] = Page(items=(new, old), next_cursor=f"{ADA}-c1")
    world.source.after[f"{ADA}-c1"] = Page(items=(), next_cursor=None)
    world.source.probes[ADA] = probe(posts=2)
    poller = world.poller()

    await poller.tick()
    assert world.source.cursors == [None, None], "bea's first read, then ada's page 1 again"
    assert "new" not in await world.items(), "the re-read took the cursor only"
    assert await places(world) == [("old", "Placeholder Square")], (
        "it still compares the places of what it re-listed"
    )

    world.clock.at(hours=6, minutes=1)
    await poller.tick()
    i = SOCIAL_ITEM
    async with world.engine.connect() as conn:
        origin = (await conn.execute(
            sa.select(i.c.origin).where(i.c.item_id == "new"),
        )).scalar_one()
    assert origin == "poll"
    reads = await store.settled_reads(
        require_perceived=False, units={"ada": ("u1",)},
        since=world.clock.start - timedelta(hours=1),
    )
    assert [[it.item_id for it in r.items] for r in reads] == [["new"]], "its stimulus is due"


async def test_an_unusable_page_ends_the_backfill_after_three_while_probes_answer(
    world,
) -> None:
    """The adapter cannot read the page at ada's cursor, while every tick's
    probes are answered: the bridge is up, so the page is to blame."""
    quiet(world, BEA)
    grid(world, ADA, 40)
    world.source.after[f"{ADA}-c1"] = Failed(FailureKind.UNAVAILABLE, "")
    poller = world.poller(floor_minutes=10, ceiling_hours=0.5)

    await poller.tick()
    await ticks(world, poller, 6, every=30)
    assert world.probes().count(ADA) == 7, "both probed and answered on every tick"
    assert world.source.cursors.count(f"{ADA}-c1") == 3
    assert ended(world, "bad_cursor") == 1


async def test_a_bridge_outage_of_several_ticks_abandons_no_backfill(world) -> None:
    """Every call answers unavailable for hours: the pages' rows are recorded
    and not counted, and the walk goes on once the bridge is back."""
    quiet(world, BEA)
    grid(world, ADA, 40)
    poller = world.poller()
    await poller.tick()

    page = world.source.after[f"{ADA}-c1"]
    down = Failed(FailureKind.UNAVAILABLE, "503")
    world.source.after[f"{ADA}-c1"] = down
    world.source.probes[ADA] = world.source.probes[BEA] = down
    world.clock.at(minutes=5)
    await ticks(world, poller, 30)
    sent = world.source.cursors.count(f"{ADA}-c1")
    assert sent > 3, "the outage outlasted three tries"
    assert ended(world, "bad_cursor") == 0
    assert ("backfill", "unavailable") in [r[:2] for r in await world.polls("ada")]

    world.source.after[f"{ADA}-c1"] = page
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=40)
    await ticks(world, poller, 2)
    assert world.source.cursors[-2:] == [f"{ADA}-c1", f"{ADA}-c2"], "the walk went on"


async def test_a_lost_abandoned_row_still_ends_the_backfill(world) -> None:
    """The bridge refuses the cursor and the marker cannot be written: the
    refusals' own error rows still end it."""
    class NoMarker(SqlSocialStreamStore):
        async def record_poll(self, **kw: Any) -> None:
            if kw.get("outcome") == "abandoned":
                raise RuntimeError("write refused")
            await super().record_poll(**kw)

    world.store = NoMarker(world.engine)
    quiet(world, BEA)
    grid(world, ADA, 40)
    world.source.after[f"{ADA}-c1"] = Failed(FailureKind.UNAVAILABLE, "invalid_id")
    poller = world.poller()

    await poller.tick()
    await ticks(world, poller, 6)
    assert world.source.cursors.count(f"{ADA}-c1") == 3


async def test_the_item_limit_holds_on_the_first_page(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 30)
    poller = world.poller(backfill_max_items=5)

    await poller.tick()
    await ticks(world, poller, 2)
    assert await backfilled(world, "ada") == 5
    assert world.source.cursors == [None, None], "the limit was reached on page 1"
    assert ended(world, "max_items") == 1


# ---- tagged places ------------------------------------------------------------


async def places(world: World) -> list[tuple[str, str | None]]:
    """Every `place` state row, oldest first: (item, new place)."""
    s = SOCIAL_ITEM_STATE
    async with world.engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(s.c.item_id, s.c.place).where(s.c.state == "place")
            .order_by(s.c.recorded_at, s.c.id),
        )).all()
    return [(r.item_id, r.place) for r in rows]


def tagged(item: SocialItem, place: str | None) -> SocialItem:
    return dataclasses.replace(item, location=place)


async def test_a_relisted_item_records_a_place_change_in_both_directions(world) -> None:
    """A list read re-lists an item already recorded: a tag added, edited or
    removed since is a `place` row; an unchanged one is none."""
    quiet(world, BEA)
    p1 = post(world, "p1")
    # Another post whose tag never changes: every page carries a place.
    anchor = tagged(post(world, "p0", age=timedelta(days=2)), "Placeholder Hill")
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(p1, anchor), next_cursor=None)
    poller = world.poller()
    await poller.tick()
    assert "p1" in await world.items() and await places(world) == []

    steps: list[str | None] = ["Placeholder Square", "Placeholder Pier", None, None]
    for n, place in enumerate(steps, start=2):
        world.source.probes[ADA] = probe(posts=n)
        world.source.pages[ADA] = Page(items=(tagged(p1, place), anchor), next_cursor=None)
        world.clock.at(hours=6 * (n - 1))
        await poller.tick()
    assert world.source.reads("items").count(ADA) == 5, "one list read each time, nothing more"
    assert await places(world) == [
        ("p1", "Placeholder Square"), ("p1", "Placeholder Pier"), ("p1", None),
    ], "added, edited, removed; the unchanged last read appends nothing"


async def test_no_removal_from_an_unknown_place_or_a_read_that_carries_no_place(
    world,
) -> None:
    """An item whose place the source could not read records nothing; nor
    does a page that re-lists tagged items and carries no place at all, which
    is what one upstream shape change would look like."""
    quiet(world, BEA)
    p1 = tagged(post(world, "p1"), "Placeholder Square")
    p2 = tagged(post(world, "p2", age=timedelta(days=2)), "Placeholder Pier")
    world.source.probes[ADA] = probe(posts=2)
    world.source.pages[ADA] = Page(items=(p1, p2), next_cursor=None)
    poller = world.poller()
    await poller.tick()
    assert await places(world) == []

    unknown = dataclasses.replace(p1, location=None, location_known=False)
    world.source.probes[ADA] = probe(posts=3)
    world.source.pages[ADA] = Page(items=(unknown, p2), next_cursor=None)
    world.clock.at(hours=6)
    await poller.tick()
    assert await places(world) == [], (
        "an unknown place is not a removal, on a page that carries a place"
    )

    world.source.probes[ADA] = probe(posts=4)
    world.source.pages[ADA] = Page(items=(tagged(p1, None), tagged(p2, None)), next_cursor=None)
    world.clock.at(hours=12)
    await poller.tick()
    assert await places(world) == [], "a page carrying no place removes none"

    world.source.probes[ADA] = probe(posts=5)
    world.source.pages[ADA] = Page(items=(tagged(p1, None), p2), next_cursor=None)
    world.clock.at(hours=18)
    await poller.tick()
    assert world.source.reads("items").count(ADA) == 4
    assert await places(world) == [("p1", None)], "a page carrying a place removes one"


async def walked(world: World, poller: SocialPoller) -> list[str | None]:
    """The cursors the re-walk read: every list read after the back-fill's."""
    await ticks(world, poller, 10)
    return list(world.source.cursors)


async def test_a_story_read_records_a_place_change_of_a_frame_it_shows_again(world) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(marker=100)
    world.source.stories[ADA] = story(frame("f1", world.clock))
    held(world, "f1")
    poller = world.poller()
    await poller.tick()
    assert "f1" in await world.items() and await places(world) == []

    world.source.probes[ADA] = probe(marker=200)
    world.source.stories[ADA] = story(
        dataclasses.replace(frame("f1", world.clock), location="Placeholder Harbour"),
    )
    world.clock.at(hours=6)
    await poller.tick()
    assert world.source.reads("story") == [ADA, ADA]
    assert await places(world) == [("f1", "Placeholder Harbour")]


async def test_the_rewalk_gives_recorded_items_their_place_once(world) -> None:
    """Items recorded before places were compared (a back-fill run by a
    poller with no re-walk), then a restart onto one with it: one re-walk
    lists them again, newest first, and each gains its place; it ends at the
    grid's end and never runs again."""
    quiet(world, BEA)
    grid(world, ADA, 30)
    poller = world.poller()
    await poller.tick()
    await ticks(world, poller, 3)
    assert world.source.cursors == [None, None, f"{ADA}-c1", f"{ADA}-c2"], "the back-fill"
    assert await backfilled(world, "ada") == 30 and await places(world) == []

    # The network now lists every item with a tag.
    first = world.source.pages[ADA]
    assert isinstance(first, Page)
    world.source.pages[ADA] = Page(
        items=tuple(tagged(i, f"Placeholder {i.item_id}") for i in first.items),
        next_cursor=first.next_cursor,
    )
    for cursor, page in list(world.source.after.items()):
        assert isinstance(page, Page)
        world.source.after[cursor] = Page(
            items=tuple(tagged(i, f"Placeholder {i.item_id}") for i in page.items),
            next_cursor=page.next_cursor,
        )
    cursors = await walked(world, world.poller(relist_pages_per_tick=2))
    assert cursors[4:] == [None, f"{ADA}-c1", f"{ADA}-c2"], "three pages once, then nothing"
    got = await places(world)
    assert len(got) == 30 and all(place == f"Placeholder {item}" for item, place in got)
    assert [r[:2] for r in await world.polls("ada")][-1] == ("relist", "read_ok")


async def test_the_rewalk_ends_at_its_page_cap_whatever_an_old_pinned_post_says(
    world,
) -> None:
    """A pinned post years old makes the member's oldest recorded item older
    than any page reaches, so the walk ends at `relist_max_pages` pages."""
    quiet(world, BEA)
    grid(world, ADA, 120)
    first = world.source.pages[ADA]
    assert isinstance(first, Page)
    pinned = post(world, "pinned", age=timedelta(days=4000))
    world.source.pages[ADA] = Page(items=(pinned, *first.items), next_cursor=first.next_cursor)
    poller = world.poller()
    await poller.tick()
    await ticks(world, poller, 6)
    before = len(world.source.cursors)
    cursors = await walked(world, world.poller(relist_pages_per_tick=2))
    assert len(cursors) - before == relist_max_pages(50) == 6
    assert cursors[before:] == [None, *(f"{ADA}-c{n}" for n in range(1, 6))]


async def test_a_halting_backfill_page_ends_the_tick_before_any_rewalk_page(world) -> None:
    """A back-fill page that halts the login (`needs_human`) is the tick's
    last call: a member waiting for the re-walk is not read after it."""
    grid(world, BEA, 5)
    grid(world, ADA, 30)
    world.source.after[f"{ADA}-c1"] = Failed(FailureKind.HALT, "needs_human")
    poller = world.poller(relist_pages_per_tick=2)
    await poller.tick()
    assert world.source.cursors == [None, None], "each member's first list read"
    await ticks(world, poller, 1)
    assert world.source.cursors == [None, None, f"{ADA}-c1"], (
        "the halting page, and no re-walk page of bea's after it"
    )
    assert "halted" in world.transitions()


async def test_the_rewalk_reads_nothing_while_the_login_is_halted_or_backed_off(world) -> None:
    """The re-walk checks the login itself, whichever path reaches it."""
    quiet(world, ADA)
    grid(world, BEA, 5)
    poller = world.poller(relist_pages_per_tick=2)
    await poller.tick()
    roster = await instagram_roster(world.subjects)
    assert roster is not None
    before = list(world.source.cursors)
    poller._listed = set()  # as at a later tick's start

    poller._back_off_until = world.clock.now + timedelta(hours=1)
    await poller._relist(roster)
    poller._back_off_until = None
    poller._halted = True
    await poller._relist(roster)
    assert world.source.cursors == before

    poller._halted = False
    await poller._relist(roster)
    assert world.source.cursors == [*before, None], "positive control: bea's first re-walk page"


# ---- a post's or reel's counts ---------------------------------------------------


def counted(item: SocialItem, likes: int | None, comments: int | None = None) -> SocialItem:
    return dataclasses.replace(item, counts=ItemCounts(likes=likes, comments=comments))


async def count_rows(world: World) -> list[tuple[str, int | None, int | None, str]]:
    """Every `social_item_count` row, oldest first: (item, likes, comments,
    the stage of the read it names)."""
    c, p = SOCIAL_ITEM_COUNT, SOCIAL_POLL
    async with world.engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(c.c.item_id, c.c.likes, c.c.comments, p.c.stage)
            .select_from(c.outerjoin(p, p.c.id == c.c.read_id))
            .order_by(c.c.recorded_at, c.c.id),
        )).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def counts_recorded(world: World) -> float:
    return world.metrics.get("nora_social_counts_recorded_total", network="instagram")


async def test_a_list_read_records_counts_where_they_moved_and_a_new_items_first(
    world,
) -> None:
    quiet(world, BEA)
    p1 = post(world, "p1")
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(counted(p1, 3, 1),), next_cursor=None)
    poller = world.poller()
    await poller.tick()
    assert await count_rows(world) == [("p1", 3, 1, "backfill")], (
        "the first list read: the item and its first counts, named by the read"
    )

    p2 = post(world, "p2")
    reads = [
        (counted(p2, 0, 0), counted(p1, 3, 1)),     # a new item; p1 unchanged
        (counted(p2, 0, 0), counted(p1, None)),     # p1 carries nothing
        (counted(p2, 2, 0), counted(p1, 5, None)),  # both moved
    ]
    for n, items in enumerate(reads, start=2):
        world.source.probes[ADA] = probe(posts=n)
        world.source.pages[ADA] = Page(items=items, next_cursor=None)
        world.clock.at(hours=6 * (n - 1))
        await poller.tick()
    assert world.source.reads("items").count(ADA) == 4
    assert await count_rows(world) == [
        ("p1", 3, 1, "backfill"), ("p2", 0, 0, "items"),
        ("p1", 5, None, "items"), ("p2", 2, 0, "items"),
    ]
    assert counts_recorded(world) == 4


async def test_backfill_and_rewalk_pages_record_counts_with_no_call_of_their_own(
    world,
) -> None:
    quiet(world, BEA)
    grid(world, ADA, 20)

    def recount_grid(likes: int) -> None:
        first = world.source.pages[ADA]
        assert isinstance(first, Page)
        world.source.pages[ADA] = Page(
            items=tuple(counted(i, likes) for i in first.items), next_cursor=first.next_cursor,
        )
        later = world.source.after[f"{ADA}-c1"]
        assert isinstance(later, Page)
        world.source.after[f"{ADA}-c1"] = Page(
            items=tuple(counted(i, likes) for i in later.items), next_cursor=None,
        )

    recount_grid(1)
    poller = world.poller()
    await poller.tick()
    await ticks(world, poller, 2)
    assert world.source.cursors == [None, None, f"{ADA}-c1"], "two back-fill pages, no more"
    rows = await count_rows(world)
    assert len(rows) == 20 and {r[3] for r in rows} == {"backfill"}

    recount_grid(2)
    cursors = await walked(world, world.poller(relist_pages_per_tick=2))
    assert cursors[3:] == [None, f"{ADA}-c1"], "the re-walk's two pages, nothing else"
    relisted = [r for r in await count_rows(world) if r[3] == "relist"]
    assert len(relisted) == 20 and all(r[1] == 2 for r in relisted)


async def recount_world(world: World) -> SocialPoller:
    """Ada and bea each listed once at the start, ada's one post counted at
    3 likes; the recount on at its default interval."""
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(counted(post(world, "p1"), 3),), next_cursor=None)
    poller = world.poller(recount_interval_hours=24)
    await poller.tick()
    return poller


async def test_the_recount_rereads_the_first_page_once_the_interval_has_passed(world) -> None:
    poller = await recount_world(world)
    new = post(world, "new-since")
    world.source.pages[ADA] = Page(
        items=(counted(new, 1), counted(post(world, "p1"), 9)), next_cursor=None,
    )
    for hours in (6, 12, 18, 23.5):
        world.clock.at(hours=hours)
        await poller.tick()
    assert world.source.reads("items") == [ADA, BEA], "inside the interval: no list read"

    world.clock.at(hours=24, minutes=1)
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA, ADA, BEA], "one recount page each"
    assert world.source.cursors[2:] == [None, None], "the first page"
    assert ("recount", "read_ok", False, False) in await world.polls("ada")
    assert [r for r in await count_rows(world) if r[3] == "recount"] == [("p1", 9, None, "recount")]
    assert "new-since" not in await world.items(), "a recount records no item: it is the poll's"


async def test_the_recount_is_off_at_zero(world) -> None:
    await recount_world(world)
    poller = world.poller(recount_interval_hours=0)
    world.clock.at(hours=48)
    await poller.tick()
    assert world.source.reads("items") == [ADA, BEA]


async def test_a_halting_backfill_or_rewalk_page_ends_the_tick_before_any_recount(
    world,
) -> None:
    """Ada is due a recount; bea's back-fill page halts the login first, and
    on another login her re-walk page does: no recount page follows either."""
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(counted(post(world, "p1"), 3),), next_cursor=None)
    grid(world, BEA, 30)
    world.source.after[f"{BEA}-c1"] = Failed(FailureKind.HALT, "needs_human")
    poller = world.poller(recount_interval_hours=24)
    await poller.tick()
    world.clock.at(hours=24)
    await poller.tick()
    assert world.source.cursors == [None, None, f"{BEA}-c1"], "the halt, and nothing after it"
    assert "recount" not in {stage for stage, *_ in await world.polls("ada")}


async def test_a_halting_rewalk_page_ends_the_tick_before_any_recount(world) -> None:
    world.source.probes[ADA] = probe(posts=1)
    world.source.pages[ADA] = Page(items=(counted(post(world, "p1"), 3),), next_cursor=None)
    grid(world, BEA, 13)  # two pages: the re-walk's second one is at a cursor
    poller = world.poller(recount_interval_hours=24, relist_pages_per_tick=2)
    await ticks(world, poller, 3)
    assert world.source.cursors == [None, None, f"{BEA}-c1", None, None], (
        "first pages, bea's back-fill page, then both re-walks' first pages"
    )
    world.source.after[f"{BEA}-c1"] = Failed(FailureKind.HALT, "needs_human")
    world.clock.at(hours=25)
    await poller.tick()
    assert world.source.cursors[5:] == [f"{BEA}-c1"], "the halting re-walk page, then nothing"
    assert "recount" not in {stage for stage, *_ in await world.polls("ada")}


async def test_the_recount_reads_nothing_while_the_login_is_halted_or_backed_off(world) -> None:
    poller = await recount_world(world)
    roster = await instagram_roster(world.subjects)
    assert roster is not None
    world.clock.at(hours=24, minutes=1)
    poller._listed = set()  # as at a later tick's start

    poller._back_off_until = world.clock.now + timedelta(hours=1)
    await poller._recount(roster)
    poller._back_off_until = None
    poller._halted = True
    await poller._recount(roster)
    assert world.source.reads("items") == [ADA, BEA]

    poller._halted = False
    await poller._recount(roster)
    assert world.source.reads("items") == [ADA, BEA, ADA, BEA], "positive control"


async def test_the_recount_takes_only_what_the_hour_leaves_after_the_reserve(world) -> None:
    """Two members reserve a probe and a read each (6); at a budget of 7,
    the tick's own probes (4) leave room for no recount page, and an hour
    later, with no probe due, for one."""
    poller = await recount_world(world)
    world.clock.at(hours=24, minutes=1)
    budgeted = world.poller(recount_interval_hours=24, hourly_budget=7)
    await budgeted.tick()
    assert world.source.reads("items") == [ADA, BEA], "the probes took what the reserve left"
    world.clock.at(hours=25, minutes=2)
    await budgeted.tick()
    assert world.source.reads("items") == [ADA, BEA, ADA], (
        "one page: the next would eat the reserve"
    )
    del poller


async def test_learned_polling_spends_spare_budget_on_the_likeliest_member(world) -> None:
    quiet(world, ADA, BEA)
    poller = world.poller(hourly_budget=10)
    await poller.tick()
    for person, account, count in (("ada", ADA, 4), ("bea", BEA, 1)):
        for n in range(count):
            item = dataclasses.replace(
                post(world, f"rhythm-{person}-{n}", account),
                taken_at=world.clock.start + timedelta(hours=1),
            )
            await world.store.insert_item(
                item, person=person, origin="backfill", seen_at=world.clock(),
            )
    world.clock.at(days=7)
    await poller.tick()
    before = len(world.probes())
    world.clock.at(days=7, hours=1)
    await poller.tick()
    assert world.probes()[before:] == [ADA], "learned rank uses spare budget before ceiling"
    world.clock.at(days=7, hours=1, minutes=10)
    await poller.tick()
    assert world.probes()[before:] == [ADA], "the floor and hourly reserve still hold"


async def test_a_cold_posting_history_does_not_accelerate_polling(world) -> None:
    quiet(world, ADA, BEA)
    poller = world.poller()
    await poller.tick()
    await world.store.insert_item(
        post(world, "old-history"), person="ada", origin="backfill", seen_at=world.clock(),
    )
    world.clock.at(hours=1)
    await poller.tick()
    assert world.probes() == [ADA, BEA], "backfilled ages are not a week of observation"


@pytest.mark.parametrize("kind", ["message", "event", "outing", "colocation"])
async def test_activity_boost_reads_only_the_persons_current_unit_activity(world, kind):
    now = world.clock()
    ms = round(now.timestamp() * 1000)
    async with world.engine.begin() as conn:
        if kind == "message":
            await conn.execute(sa.insert(MESSAGES).values(
                platform="signal", conversation_id="g1", conversation_type="group",
                sender_id="synthetic-account", person_id="ada", ts=ms, body="hello",
            ))
        elif kind == "event":
            await conn.execute(sa.insert(EVENTS).values(
                id=uuid4(), conversation_id="g1", unit_id="friends", title="Dinner",
                created_by="ada", people=["ada"], when=now, status="proposed", created_at=now,
            ))
        else:
            build = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="test", params_sha256="test", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
            table = ROLLUP_OUTING if kind == "outing" else ROLLUP_COLOCATION
            extra = {} if kind == "outing" else {"radius_m": 50.0, "stay_count": 2}
            await conn.execute(sa.insert(table).values(
                build_id=build, unit="friends", lineage={}, valid_from_ms=ms-1000,
                valid_to_ms=ms, as_of_ms=ms, persons=["ada"], lat=0.0, lng=0.0,
                open=1, **extra,
            ))
    args = {"person": "ada", "units": ("friends",), "rooms": (("signal", "g1"),),
            "now": now, "timezone": "Asia/Hong_Kong"}
    assert await world.store.social_activity(**args)
    assert not await world.store.social_activity(**{**args, "person": "bea"})
    assert not await world.store.social_activity(**{**args, "units": (), "rooms": ()})
    assert not await SqlSocialStreamStore(
        world.engine, opted_out=frozenset({"ada"}),
    ).social_activity(**args)
    async with world.engine.begin() as conn:
        if kind == "message":
            await conn.execute(sa.update(MESSAGES).values(hidden_at=ms))
        elif kind == "event":
            await conn.execute(sa.update(EVENTS).values(status="cancelled"))
        else:
            await conn.execute(sa.update(ROLLUP_BUILD).values(status="superseded"))
    assert not await world.store.social_activity(**args)


async def _warm_account(world) -> None:
    world.subjects = FakeSocialSubjectIndex({"ada": ADA})
    async with world.engine.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_POLL), [
            {"network": "instagram", "person": "ada", "stage": "probe",
             "outcome": "unchanged", "recorded_at": world.clock()-timedelta(days=8)},
            {"network": "instagram", "person": "ada", "stage": "probe",
             "outcome": "unchanged", "recorded_at": world.clock()-timedelta(hours=1)},
        ])


async def test_restarting_never_retries_an_unanswered_adaptive_probe_before_the_ceiling(world):
    await _warm_account(world)
    world.source.probes[ADA] = Failed(FailureKind.NOT_FOUND)
    await world.poller().tick()
    assert world.probes() == [ADA]
    world.clock.at(minutes=31)
    await world.poller().tick()
    assert world.probes() == [ADA], "restart must not forget the permanent failure's hold"


async def test_an_adaptive_probe_reserves_its_two_possible_reads_before_admission(world):
    await _warm_account(world)
    world.source.probes[ADA] = probe(marker=10, posts=2)
    world.source.stories[ADA] = story(frame("new-frame", world.clock))
    world.source.pages[ADA] = Page(items=(post(world, "new-post"),), next_cursor=None)
    await world.poller(hourly_budget=5).tick()
    assert world.probes() == [], "probe plus two reads must fit beside the three-call reserve"
    await world.poller(hourly_budget=7).tick()
    assert world.probes() == [ADA], "sufficient spare budget still permits the read"


@pytest.mark.parametrize("full_history", [False, True])
@pytest.mark.parametrize("posts,is_private,following,reason,done", [
    (0, False, False, "grid_end", True),
    (5, None, None, "unreadable", False),
    (5, True, None, "unreadable", False),
    (5, None, False, "unreadable", False),
    (5, False, False, "unreadable", False),
    (5, True, True, "unreadable", False),
    (5, True, False, "private_not_followed", True),
])
async def test_empty_backfill_requires_the_probe_to_confirm_an_empty_grid(
    world, full_history, posts, is_private, following, reason, done,
) -> None:
    quiet(world, BEA)
    world.source.probes[ADA] = probe(posts=posts, is_private=is_private, following=following)
    world.source.pages[ADA] = Page(items=(), next_cursor=None)
    await world.poller(backfill_full_history=full_history).tick()
    assert await world.items() == {}
    standing = await world.store.backfills(network="instagram", full_history=full_history)
    assert standing["ada"].done is done
    # A new poller must retry the first page through the existing backfill
    # queue, retaining the distinction when the bridge still returns empty.
    calls = len(world.source.reads("items"))
    await ticks(world, world.poller(backfill_full_history=full_history), 1)
    assert len(world.source.reads("items")) == calls + (not done)
    async with world.engine.connect() as conn:
        reasons = (await conn.execute(
            sa.select(SOCIAL_POLL.c.end_reason)
            .where(SOCIAL_POLL.c.person == "ada", SOCIAL_POLL.c.stage == "backfill")
            .order_by(SOCIAL_POLL.c.recorded_at, SOCIAL_POLL.c.id),
        )).scalars().all()
    assert reasons == [reason] * (1 if done else 2)
    standing = await world.store.backfills(network="instagram", full_history=full_history)
    assert standing["ada"].done is done
    if not done:
        grid(world, ADA, posts)
        await ticks(world, world.poller(backfill_full_history=full_history), 2)
        # Bounded recovery uses the first page only for its cursor; a full
        # walk also captures the formerly unreadable first page.
        assert await backfilled(world, "ada") == (posts if full_history else 0)
        standing = await world.store.backfills(network="instagram", full_history=full_history)
        assert standing["ada"].done


async def test_full_history_reopens_a_legacy_bounded_walk_and_stays_finished(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 80, every=timedelta(days=3))
    bounded = world.poller()
    await bounded.tick()
    await ticks(world, bounded, 5)
    assert await backfilled(world, "ada") == 30
    # A deployed pre-full-history terminal row has no end reason. Its opaque
    # continuation was discarded, so the safe recovery is a deduplicated walk.
    await world.store.record_poll(
        network="instagram", person="ada", stage="backfill", outcome="read_ok",
    )
    full = world.poller(backfill_full_history=True)
    await ticks(world, full, 3)
    await ticks(world, world.poller(backfill_full_history=True), 7)
    assert await backfilled(world, "ada") == 80
    assert len(await world.items()) == 80, "the re-walk must not duplicate known items"
    async with world.engine.connect() as conn:
        reason = (await conn.execute(
            sa.select(SOCIAL_POLL.c.end_reason)
            .where(SOCIAL_POLL.c.person == "ada", SOCIAL_POLL.c.stage == "backfill")
            .order_by(SOCIAL_POLL.c.recorded_at.desc(), SOCIAL_POLL.c.id.desc()).limit(1)
        )).scalar_one()
    assert reason == "grid_end", "completion is durable evidence of exhausting the grid"
    calls = len(world.source.reads("items"))
    await ticks(world, world.poller(backfill_full_history=True), 2)
    assert len(world.source.reads("items")) == calls, (
        "a restart must not reopen a full completed walk"
    )


async def test_full_history_fetches_first_page_items_missing_from_legacy_record(world) -> None:
    quiet(world, BEA)
    grid(world, ADA, 20, every=timedelta(days=10))
    await world.store.record_poll(
        network="instagram", person="ada", stage="probe", outcome="items_due",
    )
    await world.store.record_poll(
        network="instagram", person="ada", stage="items", outcome="read_ok",
        media_count=20, total_clips_count=0, advanced_counts=True,
    )
    await world.store.record_poll(
        network="instagram", person="ada", stage="backfill", outcome="read_ok",
    )
    poller = world.poller(backfill_full_history=True)
    await poller.tick()
    await ticks(world, poller, 3)
    assert await backfilled(world, "ada") == 20, "full history includes the recovery first page"


@pytest.mark.parametrize(
    ("items", "every", "limit", "kept"),
    [(20, timedelta(days=10), 50, 12), (8, timedelta(days=1), 5, 5)],
)
async def test_full_history_reopens_a_bounded_last_page(world, items, every, limit, kept):
    quiet(world, BEA)
    grid(world, ADA, items, every=every)
    bounded = world.poller(backfill_max_items=limit)
    await bounded.tick()
    await ticks(world, bounded, 2)
    assert await backfilled(world, "ada") == kept
    await ticks(world, world.poller(backfill_full_history=True), 4)
    assert await backfilled(world, "ada") == items, (
        "reaching the grid end while omitting bounded items is not a full import"
    )


@pytest.mark.parametrize("failed_recovery", [False, True])
async def test_full_history_recovery_leaves_fresh_first_page_posts_to_the_poll(
    world, failed_recovery,
):
    quiet(world, BEA)
    grid(world, ADA, 8)
    await world.poller(backfill_max_items=5).tick()
    world.clock.at(hours=2)
    fresh = post(world, "fresh-news", age=timedelta(hours=1))
    first = world.source.pages[ADA]
    assert isinstance(first, Page)
    world.source.pages[ADA] = dataclasses.replace(first, items=(fresh, *first.items))
    world.source.probes[ADA] = probe(posts=9)
    if failed_recovery:
        # An attempt after publication must not redefine the historical
        # boundary. Append a receipt at the controlled clock's time.
        async with world.engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_POLL).values(
                network="instagram", person="ada", stage="backfill", outcome="error",
                recorded_at=world.clock(),
            ))
    poller = world.poller(backfill_full_history=True)
    await poller.tick()
    assert await backfilled(world, "ada") == 8
    assert "fresh-news" not in await world.items(), "fresh news belongs to the next due poll"
    world.clock.at(hours=6.1)
    await poller.tick()
    async with world.engine.connect() as conn:
        origin = (await conn.execute(
            sa.select(SOCIAL_ITEM.c.origin).where(SOCIAL_ITEM.c.item_id == "fresh-news")
        )).scalar_one()
    assert origin == "poll", "fresh posts must retain their stimulus and Persona wake eligibility"


async def test_an_older_backfill_page_does_not_move_the_fresh_news_boundary(world):
    quiet(world, BEA)
    grid(world, ADA, 20)
    bounded = world.poller(backfill_max_items=15)
    await bounded.tick()
    # Use the database clock between the actual first and older page receipts:
    # moving the poller's test clock does not move social_poll.recorded_at.
    await asyncio.sleep(1.05)
    async with world.engine.connect() as conn:
        posted = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
    first = world.source.pages[ADA]
    assert isinstance(first, Page)
    taken_at = _when(int(posted.timestamp()))
    assert taken_at is not None
    fresh = dataclasses.replace(post(world, "between-pages"), taken_at=taken_at)
    world.source.pages[ADA] = dataclasses.replace(first, items=(fresh, *first.items[:-1]))
    older = world.source.after[f"{ADA}-c1"]
    assert isinstance(older, Page)
    world.source.after[f"{ADA}-c1"] = dataclasses.replace(
        older, items=(first.items[-1], *older.items),
    )
    world.source.probes[ADA] = probe(posts=21)
    await asyncio.sleep(1.05)
    await ticks(world, bounded, 1)
    assert await backfilled(world, "ada") == 15
    full = world.poller(backfill_full_history=True)
    await ticks(world, full, 3)
    assert await backfilled(world, "ada") == 20
    assert "between-pages" not in await world.items()
    world.clock.at(hours=6.1)
    await full.tick()
    async with world.engine.connect() as conn:
        origin = (await conn.execute(
            sa.select(SOCIAL_ITEM.c.origin).where(SOCIAL_ITEM.c.item_id == "between-pages")
        )).scalar_one()
    assert origin == "poll"


async def test_a_post_published_during_custody_remains_fresh_news(world):
    quiet(world, BEA)
    grid(world, ADA, 8)
    original_media = world.media
    published = None

    class PublishDuringCustody(FakeMediaSource):
        async def fetch(self, *, ref):
            nonlocal published
            if published is None:
                # Span whole source seconds on both sides of publication so
                # a completion timestamp cannot stand in for a pre-fetch one.
                await asyncio.sleep(1.05)
                async with world.engine.connect() as conn:
                    published = (await conn.execute(
                        sa.select(sa.func.clock_timestamp())
                    )).scalar_one()
                taken_at = _when(int(published.timestamp()))
                assert taken_at is not None
                fresh = dataclasses.replace(post(world, "during-custody"), taken_at=taken_at)
                first = world.source.pages[ADA]
                assert isinstance(first, Page)
                world.source.pages[ADA] = dataclasses.replace(first, items=(fresh, *first.items))
                world.source.probes[ADA] = probe(posts=9)
                await asyncio.sleep(1.05)
            return await original_media.fetch(ref=ref)

    world.media = PublishDuringCustody()
    world.media.blobs = original_media.blobs
    await world.poller(backfill_max_items=5).tick()
    assert published is not None
    assert "during-custody" not in await world.items(), "the fetched page predates publication"
    async with world.engine.connect() as conn:
        receipt = (await conn.execute(sa.select(SOCIAL_POLL.c.recorded_at).where(
            SOCIAL_POLL.c.person == "ada", SOCIAL_POLL.c.stage == "backfill",
        ))).scalar_one()
    assert published < receipt, "publication occurred before the completion receipt"
    full = world.poller(backfill_full_history=True)
    await ticks(world, full, 1)
    assert await backfilled(world, "ada") == 8
    assert "during-custody" not in await world.items()
    world.clock.at(hours=6.1)
    await full.tick()
    assert await world.store.polled_count(persons=["ada"], after_us=0) == 1


async def test_full_history_requires_a_grounded_first_page_boundary(world):
    quiet(world, BEA)
    grid(world, ADA, 8)
    # A success receipt without the preceding probe cannot date its fetch.
    await world.store.record_poll(
        network="instagram", person="ada", stage="items", outcome="read_ok",
        media_count=8, total_clips_count=0, advanced_counts=True,
    )
    await world.poller(backfill_full_history=True).tick()
    assert ADA not in world.source.reads("items")
    standing = await world.store.backfills(network="instagram", full_history=True)
    assert not standing["ada"].done
    assert world.metrics.get("nora_errors_total", stage="social_poller") == 1


async def test_recovery_reserves_the_whole_source_second_for_fresh_news(world):
    cutoff = datetime(2026, 10, 20, 12, 0, 0, 100000, tzinfo=UTC)
    ambiguous_at = _when(int(cutoff.timestamp()))
    older_at = _when(int(cutoff.timestamp()) - 1)
    assert ambiguous_at is not None and older_at is not None
    ambiguous = dataclasses.replace(post(world, "same-second"), taken_at=ambiguous_at)
    older = dataclasses.replace(post(world, "older-second"), taken_at=older_at)
    roster = await instagram_roster(world.subjects)
    assert roster is not None
    member = next(m for m in roster if m.person == "ada")
    await world.poller(backfill_full_history=True)._record_page(
        member, Page(items=(ambiguous, older), next_cursor=None), origin="backfill",
        read_id=str(uuid4()), backfilled=0, before=cutoff,
    )
    assert set(await world.items()) == {"older-second"}, (
        "whole-second source timestamps cannot place this second before the probe"
    )


@pytest.mark.parametrize("full_history", [False, True])
@pytest.mark.parametrize("access", [{"is_private": True, "following": True},
                                    {"is_private": False, "following": False}])
async def test_private_unfollowed_grid_reopens_only_after_access_is_confirmed(
    world, full_history, access,
) -> None:
    quiet(world, BEA)
    grid(world, ADA, 5)
    visible = world.source.pages[ADA]
    world.source.probes[ADA] = probe(posts=5, is_private=True, following=False)
    world.source.pages[ADA] = Page(items=(), next_cursor=None)
    await world.poller(backfill_full_history=full_history, relist_pages_per_tick=2,
                       recount_interval_hours=24).tick()
    calls = world.source.reads("items").count(ADA)
    # Across restarts, changed counts, an unknown access probe, and the
    # recount deadline, no item path may re-read the settled private grid.
    for hours, flags in [(6, {"is_private": True, "following": False}),
                         (30, {"is_private": None, "following": None})]:
        world.clock.at(hours=hours)
        world.source.probes[ADA] = probe(posts=6, **flags)
        await world.poller(backfill_full_history=full_history, relist_pages_per_tick=2,
                       recount_interval_hours=24).tick()
        assert world.source.reads("items").count(ADA) == calls
        assert (await world.store.backfills(
            network="instagram", full_history=full_history,
        ))["ada"].done
    world.clock.at(hours=36)
    world.source.pages[ADA] = visible
    world.source.probes[ADA] = probe(posts=5, **access)
    await world.poller(backfill_full_history=full_history, relist_pages_per_tick=2,
                       recount_interval_hours=24).tick()
    assert world.source.reads("items").count(ADA) > calls
    assert await backfilled(world, "ada") == (5 if full_history else 0)
    assert (await world.store.backfills(
        network="instagram", full_history=full_history,
    ))["ada"].done
