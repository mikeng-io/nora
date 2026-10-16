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
    MediaItem,
    Page,
    Probe,
    SocialItem,
    StoryFrame,
    StorySnapshot,
)
from nora.infra.social.instagram_source import InstagramSocialSource
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
            # The re-walk is off unless a test asks for it: the back-fill's
            # tests count the back-fill's own pages.
            settings=SocialPollConfig(
                enabled=True, jitter_seconds=0, **{"relist_pages_per_tick": 0, **settings},
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
