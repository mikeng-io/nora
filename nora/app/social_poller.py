"""`SocialPoller` — the social stream's clock (design/cognition-loop/22-social-stream.md
§3, §4, §5): it probes each member's account on one network, reads a story or
a page of posts and reels only when the probe says it changed, and records
what it found.

**One poller per login.** Every member it reads goes through one
`SocialSource`, so the budget, the halt and the back-off are this object's
state, and nothing is shared with another network's poller.

**The cadence is the ceiling.** A tick probes every member whose last
answered probe is older than `ceiling_hours`, oldest first, and never a member
probed (answered or not) within `floor_minutes`. Both times are kept in
memory and seeded from `social_poll` on the first tick, so a restart keeps
them.

**A probe decides, then the read** (§3, `nora.domain.social_stream.decide`).
A completed story read advances the marker, including one that found nothing;
a list read reads the first page only and advances the counts only when every
new item on it was recorded, so a failed or interrupted list read leaves the
rise due on the next probe. A frame recorded earlier that a story read no
longer shows, before its own expiry, is recorded `gone`.

**What stops it** (§4, keyed on `Failed.kind`): `halt` stops every call for
the login until `SocialSource.healthy` says the session can read, and is
never retried on the tick; `back_off` (and a probe whose counts half was rate
limited) pauses the login for `back_off_start_minutes`, doubling to
`back_off_max_hours`, until one clean probe; `stale_query` halts that read
kind for every member and is tried again once per ceiling; anything else is
that call's `error` and the tick moves on. Each halt, back-off and recovery
is a `source_health` transition (`source` `social.<network>`, `scope` empty
for the login or the read kind), so silence during one is never read as
absence.

**The budget** is upstream calls in any trailing hour, a probe counted at its
source's `probe_cost` and a read at one; a call it cannot afford ends the
tick. A pause of up to `jitter_seconds` precedes every call after a tick's
first.

**Custody** (§5): each NEW item's media is fetched in the same poll through
the network's `MediaSource`, kept in the `BlobStore` and indexed in
`media_objects` (`source = "social"`), and its content addresses become
`social_item.media_ref`, comma-separated in the item's order. An item already
recorded is never fetched. Custody that is not wired, or a fetch that fails,
records the item with no `media_ref` for that media, counted, never dropped.
A story frame's bytes are kept like a post's: nothing here deletes them.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from nora.config import SocialPollConfig
from nora.domain.media import SOCIAL_MEDIA_SOURCE, MediaObject, MediaRef, ground_media_type
from nora.domain.ports import (
    BlobStore,
    MediaObjectStore,
    MediaSource,
    SocialSource,
    SourceHealthLog,
)
from nora.domain.social_stream import (
    CUSTODY_OUTCOMES,
    Cap,
    CustodyOutcome,
    Failed,
    FailureKind,
    Member,
    PollOutcome,
    PollStage,
    Probe,
    SkipReason,
    SocialItem,
    decide,
    frame_item,
)
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.social_poller")

_HOUR = timedelta(hours=1)

#: The members to poll on the source's network, or None when they could not be
#: read (`nora.infra.social.subjects.instagram_roster` for Instagram).
Roster = Callable[[], Awaitable[Sequence[Member] | None]]


class SocialPoller:
    def __init__(
        self,
        *,
        source: SocialSource,
        store: SqlSocialStreamStore,
        health: SourceHealthLog,
        roster: Roster,
        settings: SocialPollConfig,
        metrics: Metrics,
        media: MediaSource | None = None,
        blobs: BlobStore | None = None,
        media_objects: MediaObjectStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._source = source
        self._network = source.network
        self._store = store
        self._health = health
        self._health_source = f"social.{source.network}"
        self._roster = roster
        self._settings = settings
        self._floor = timedelta(minutes=settings.floor_minutes)
        self._ceiling = timedelta(hours=settings.ceiling_hours)
        self._back_off_start = timedelta(minutes=settings.back_off_start_minutes)
        self._back_off_max = timedelta(hours=settings.back_off_max_hours)
        self._metrics = metrics
        self._media = media
        self._blobs = blobs
        self._media_objects = media_objects
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        # None until the first tick has asked the source: a start is observed
        # once, as `up` or `halted`.
        self._halted: bool | None = None
        self._back_off: timedelta | None = None
        self._back_off_until: datetime | None = None
        # Read kind ("story", "items") -> when its query went stale.
        self._stale: dict[str, datetime] = {}
        self._spent: deque[tuple[datetime, int]] = deque()
        self._attempted: dict[str, datetime] = {}
        self._answered: dict[str, datetime] = {}
        self._seeded = False
        self._calls_this_tick = 0

    async def run(self) -> None:
        log.info(
            "social poller started (%s): tick every %.0fs", self._network,
            self._settings.tick_seconds,
        )
        while True:
            await self._sleep(self._settings.tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("social poller tick failed")
                self._metrics.inc("nora_errors_total", stage="social_poller")

    async def tick(self) -> None:
        """Probe the members due, oldest first, until the budget, a halt or a
        back-off stops it."""
        self._calls_this_tick = 0
        now = self._clock()
        if not await self._login_ready(now):
            return
        if not self._seeded:
            for person, times in (await self._store.last_probes(network=self._network)).items():
                self._attempted[person] = times.attempted
                if times.answered is not None:
                    self._answered[person] = times.answered
            self._seeded = True
        roster = await self._roster()
        if roster is None:
            self._skip("roster_unavailable")
            return
        due = sorted(
            (m for m in roster if self._due(m.person, now)),
            key=lambda m: (self._answered.get(m.person) or datetime.min.replace(tzinfo=UTC),
                           m.person),
        )
        for member in due:
            if not self._afford(self._source.probe_cost):
                self._skip("budget")
                return
            try:
                stop = await self._poll(member)
            except Exception:
                log.exception("social poll of one member failed; the tick continues")
                self._metrics.inc("nora_errors_total", stage="social_poller")
                continue
            if stop is not None:
                self._skip(stop)
                return

    # -- the login -------------------------------------------------------------

    async def _login_ready(self, now: datetime) -> bool:
        if self._halted is not False:
            if not await self._source.healthy():
                if self._halted is None:
                    await self._transition("", "halted", "session")
                self._halted = True
                self._skip("halted")
                return False
            self._halted = False
            await self._transition("", "up")
        if self._back_off_until is not None and now < self._back_off_until:
            self._skip("backed_off")
            return False
        return True

    def _due(self, person: str, now: datetime) -> bool:
        attempted = self._attempted.get(person)
        if attempted is not None and now - attempted < self._floor:
            return False
        answered = self._answered.get(person)
        return answered is None or now - answered >= self._ceiling

    def _afford(self, cost: int) -> bool:
        horizon = self._clock() - _HOUR
        while self._spent and self._spent[0][0] <= horizon:
            self._spent.popleft()
        return sum(c for _, c in self._spent) + cost <= self._settings.hourly_budget

    def _may_read(self, cap: Cap, kind: str, now: datetime) -> bool:
        if cap not in self._source.capabilities:
            return False
        since = self._stale.get(kind)
        return since is None or now - since >= self._ceiling

    async def _call(
        self, stage: PollStage, cost: int, call: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, int]:
        """One upstream call: jitter after the tick's first, then the call,
        spent against the budget. Answers `(result, latency_ms)`."""
        if self._calls_this_tick and self._settings.jitter_seconds > 0:
            await self._sleep(self._rand() * self._settings.jitter_seconds)
        self._calls_this_tick += 1
        started = time.monotonic()
        result = await call()
        latency = int((time.monotonic() - started) * 1000)
        self._spent.append((self._clock(), cost))
        self._metrics.inc(
            "nora_social_upstream_calls_total", cost, network=self._network, stage=stage,
        )
        return result, latency

    # -- one member ------------------------------------------------------------

    async def _poll(self, member: Member) -> SkipReason | None:
        probe, ms = await self._call(
            "probe", self._source.probe_cost,
            lambda: self._source.probe(account=member.account),
        )
        now = self._clock()
        self._attempted[member.person] = now
        if isinstance(probe, Failed):
            return await self._failed(member, "probe", probe, ms)
        if probe.rate_limited:
            await self._record(member, "probe", "backed_off", ms, probe=probe)
            return await self._start_back_off("rate_limited")
        await self._clean_probe()
        self._answered[member.person] = now
        marker = await self._store.read_marker(network=self._network, person=member.person)
        last = await self._store.last_counts(network=self._network, person=member.person)
        due = decide(probe, marker=marker, last_counts=last)
        await self._record(
            member, "probe", due.outcome, ms, probe=probe,  # type: ignore[arg-type]
            advanced_counts=not due.partial and not due.items_due,
        )
        if due.story_due and self._may_read(Cap.STORY, "story", now):
            stop = await self._read_story(member, probe)
            if stop is not None:
                return stop
        if due.items_due and self._may_read(Cap.LIST_ITEMS, "items", now):
            return await self._read_items(member, probe)
        return None

    async def _read_story(self, member: Member, probe: Probe) -> SkipReason | None:
        if not self._afford(1):
            return "budget"
        snap, ms = await self._call("story", 1, lambda: self._source.story(account=member.account))
        if isinstance(snap, Failed):
            return await self._failed(
                member, "story", snap, ms, latest_reel_media=probe.latest_reel_media,
            )
        await self._recovered("story")
        now = self._clock()
        frames = [
            frame_item(network=self._network, account=member.account.id, frame=f)
            for f in snap.frames
        ]
        known = await self._store.known_items(
            network=self._network, item_ids=[f.item_id for f in frames],
        )
        for item in frames:
            if item.item_id not in known:
                await self._insert(member, item, now)
        shown = {f.item_id for f in frames}
        live = await self._store.live_frames(network=self._network, person=member.person, now=now)
        for item_id in sorted(live - shown):
            await self._store.record_state(
                network=self._network, item_id=item_id, state="gone",
                reason="not shown on a story read before its expiry",
            )
        await self._record(
            member, "story", "read_ok", ms, latest_reel_media=probe.latest_reel_media,
            advanced_marker=True,
        )
        return None

    async def _read_items(self, member: Member, probe: Probe) -> SkipReason | None:
        if not self._afford(1):
            return "budget"
        page, ms = await self._call(
            "items", 1, lambda: self._source.list_items(account=member.account, after=None),
        )
        if isinstance(page, Failed):
            return await self._failed(member, "items", page, ms)
        await self._recovered("items")
        known = await self._store.known_items(
            network=self._network, item_ids=[r.item_id for r in page.items],
        )
        complete = True
        stop: SkipReason | None = None
        for ref in page.items:
            if ref.item_id in known:
                continue
            if Cap.READ_ITEM not in self._source.capabilities:
                complete = False
                break
            if not self._afford(1):
                complete, stop = False, "budget"
                break
            item, item_ms = await self._call(
                "item", 1, functools.partial(self._source.read_item, ref=ref),
            )
            if isinstance(item, Failed):
                complete = False
                stop = await self._failed(member, "item", item, item_ms)
                if stop is not None:
                    break
                continue
            await self._insert(member, item, self._clock())
            await self._record(member, "item", "read_ok", item_ms)
        # The list read itself completed; the counts it carries advance only
        # when every new item on the page was recorded.
        await self._record(
            member, "items", "read_ok", ms, media_count=probe.media_count,
            total_clips_count=probe.total_clips_count, advanced_counts=complete,
        )
        return stop

    async def _failed(
        self, member: Member, stage: PollStage, failure: Failed, ms: int, *,
        latest_reel_media: int | None = None,
    ) -> SkipReason | None:
        """Record a failed call and act on its kind. Answers why the tick
        stops, or None when it goes on."""
        if failure.kind is FailureKind.HALT:
            await self._record(member, stage, "halted", ms, latest_reel_media=latest_reel_media)
            self._halted = True
            await self._transition("", "halted", failure.detail)
            return "halted"
        if failure.kind is FailureKind.BACK_OFF:
            await self._record(member, stage, "backed_off", ms, latest_reel_media=latest_reel_media)
            return await self._start_back_off(failure.detail)
        if failure.kind is FailureKind.STALE_QUERY and stage != "probe":
            await self._record(member, stage, "halted", ms, latest_reel_media=latest_reel_media)
            kind = "story" if stage == "story" else "items"
            if kind not in self._stale:
                await self._transition(kind, "stale_query", failure.detail)
            self._stale[kind] = self._clock()
            return None
        await self._record(member, stage, "error", ms, latest_reel_media=latest_reel_media)
        return None

    async def _start_back_off(self, detail: str) -> SkipReason:
        step = (
            self._back_off_start if self._back_off is None
            else min(self._back_off * 2, self._back_off_max)
        )
        self._back_off = step
        self._back_off_until = self._clock() + step
        await self._transition("", "backed_off", f"{detail} {int(step.total_seconds())}s")
        return "backed_off"

    async def _clean_probe(self) -> None:
        if self._back_off is not None:
            self._back_off = self._back_off_until = None
            await self._transition("", "up")

    async def _recovered(self, kind: str) -> None:
        if self._stale.pop(kind, None) is not None:
            await self._transition(kind, "up")

    # -- the Record ------------------------------------------------------------

    async def _insert(self, member: Member, item: SocialItem, now: datetime) -> None:
        media_ref = await self._keep(item, now)
        await self._store.insert_item(
            item, person=member.person, origin="poll", seen_at=now, media_ref=media_ref,
        )

    # ponytail: a story frame's bytes are never deleted; deleting them at
    # `expires_at` plus a bound needs a `BlobStore` delete and a tombstone row,
    # which custody does not have yet.
    async def _keep(self, item: SocialItem, now: datetime) -> str | None:
        """Each of `item`'s media into custody → their content addresses,
        comma-separated, or None when none was kept."""
        if self._media is None or self._blobs is None:
            for _ in item.media:
                self._custody("not_configured")
            return None
        kept: list[str] = []
        for media in item.media:
            declared = f"{media.kind}/*"
            got = await self._media.fetch(ref=MediaRef(
                platform_id=item.item_id, media_type=declared,
                fetch_token=f"attachment:{media.url}",
            ))
            if not got.available:
                reason = got.reason if got.reason in CUSTODY_OUTCOMES else "unreachable"
                self._custody(reason)  # type: ignore[arg-type]
                continue
            try:
                sha256 = await self._blobs.put(data=got.data)
            except Exception:
                log.exception("social custody: the blob store refused an item's media")
                self._custody("store_failed")
                continue
            if self._media_objects is not None:
                try:
                    await self._media_objects.save(obj=MediaObject(
                        sha256=sha256,
                        media_type=ground_media_type(declared=declared, data=got.data),
                        size=len(got.data), source=SOCIAL_MEDIA_SOURCE, description="",
                        provenance={
                            "network": item.network, "item_id": item.item_id,
                            "kind": item.kind,
                        },
                        created_at_ms=int(now.timestamp() * 1000),
                    ))
                except Exception:
                    # The bytes are kept; only the index entry is lost.
                    log.exception("social custody: media_objects save failed")
            kept.append(sha256)
            self._custody("stored")
        return ",".join(kept) or None

    async def _record(
        self, member: Member, stage: PollStage, outcome: PollOutcome, ms: int, *,
        probe: Probe | None = None, latest_reel_media: int | None = None,
        media_count: int | None = None, total_clips_count: int | None = None,
        advanced_marker: bool = False, advanced_counts: bool = False,
    ) -> None:
        if probe is not None:
            latest_reel_media = probe.latest_reel_media
            media_count, total_clips_count = probe.media_count, probe.total_clips_count
        await self._store.record_poll(
            network=self._network, person=member.person, stage=stage, outcome=outcome,
            latest_reel_media=latest_reel_media, media_count=media_count,
            total_clips_count=total_clips_count, advanced_marker=advanced_marker,
            advanced_counts=advanced_counts, latency_ms=ms,
        )
        self._metrics.inc(
            "nora_social_polls_total", network=self._network, stage=stage, outcome=outcome,
        )

    async def _transition(self, scope: str, outcome: str, detail: str = "") -> None:
        written = await self._health.record(
            source=self._health_source, scope=scope, event="transition", outcome=outcome,
            at_ms=int(self._clock().timestamp() * 1000), detail=detail,
        )
        if not written:
            self._metrics.inc("nora_errors_total", stage="source_health")

    def _skip(self, reason: SkipReason) -> None:
        self._metrics.inc("nora_social_poll_skipped_total", network=self._network, reason=reason)

    def _custody(self, outcome: CustodyOutcome) -> None:
        self._metrics.inc("nora_social_custody_total", network=self._network, outcome=outcome)
