"""`SocialPoller` — the social stream's clock (design/cognition-loop/22-social-stream.md
§3, §4, §5): it probes each member's account on one network, reads a story or
a page of posts and reels only when the probe says it changed, and records
what it found.

**One poller per login.** Every member it reads goes through one
`SocialSource`, so the budget, the halt and the back-off are this object's
state, and nothing is shared with another network's poller.

**The cadence is the ceiling.** A tick probes every member whose last
answered probe is older than `ceiling_hours`, least recently probed first,
and never a member probed (answered or not) within `floor_minutes`; a probe
whose failure will not change on its own waits for the ceiling instead. The
schedule and the hour's spent calls are seeded from `social_poll`, and the
login's halt, lock and back-off from its `source_health` transitions, on the
first tick, so a restart keeps them.

**A probe decides, then the read** (§3, `nora.domain.social_stream.decide`).
A completed story read advances the marker, including one that found nothing;
a list read reads the first page only, which carries every item's media, and
records each new item from it; a failed list read leaves the rise due on the
next probe. A read's row and the items it recorded share one id
(`social_item.read_id`), which is what the read's stimulus is raised for
(`nora.app.social_perception`). A post with no usable media is recorded
without it. A frame recorded earlier that a story read no longer shows, before
its own expiry, is recorded `gone`.

**The back-fill** (§7). A person's first list read, before any counts were
recorded, is history: its items are recorded `origin = "backfill"` and its row
is the first `backfill` page, carrying the cursor the next page starts at.
After the tick's probes and due reads, each member whose back-fill is not done
gets at most one more page, newest first, up to `backfill_pages_per_tick`
pages a tick, and only while what is left of the hour's budget still covers a
probe and a read of every member, so history never takes a call the news
needs. A page records only items newer than `backfill_max_days` and up to
`backfill_max_items` back-filled items in all; the walk ends there, at a page
read at a cursor that reaches past the day limit (the first page: one wholly
past it, since a pinned post there can be old), or at the end of the grid. Where each
member stands is read off their `backfill` rows every tick
(`SqlSocialStreamStore.backfills`), so a restart reads no page twice. A cursor
the bridge refuses (`invalid_id`, the only request field the adapter has not
shape-checked) or one refused upstream twice in a row is judged bad and ends
that member's back-fill (`abandoned`), as does one whose pages failed three
times in a row for any reason but a halt or a stale query; both are read off
the rows. A first page read again only to find its cursor (the person's first
list read predates the back-fill) records nothing: an item new on it is the
poll's. Back-filled items raise no stimulus.

**A tagged place follows the author** (§5). Every page and story read
compares each item it lists that is already recorded with that item's
current place (`SqlSocialStreamStore.current_places`) and appends a `place`
state row only where they differ: a tag added, edited or removed after the
item was recorded. It reuses the read already made. So that items recorded
before places were compared get theirs, a member whose back-fill is done is
re-walked ONCE (`relist` rows), after the back-fill and on its budget rule,
at most `relist_pages_per_tick` pages a tick, one per member: newest first, recording no new
item (an item new on it is the poll's), and ending at the grid's end, at a
page reaching past their oldest recorded item, or after `relist_max_pages`
pages. Where each member stands is read off their `relist` rows
(`SqlSocialStreamStore.relists`), so a restart reads no page twice.

**What stops it** (§4, keyed on `Failed.kind`): `halt` stops every call for
the login until `SocialSource.healthy` says the session can read, and is
never retried on the tick, and a cleared halt resumes on the back-off
ladder's top step, while two halts within 24 hours lock the login until an
operator appends a `cleared` transition; `back_off` (and a probe whose counts half was rate
limited) pauses the login for `back_off_start_minutes`, doubling to
`back_off_max_hours`, until one clean probe; `stale_query` halts that read
kind for every member and is tried again once per ceiling; anything else is
that call's `error` and the tick moves on. Each halt, back-off and recovery
is a `source_health` transition (`source` `social.<network>`, `scope` empty
for the login or the read kind), so silence during one is never read as
absence.

**The budget** is upstream calls in any trailing hour, a probe counted at its
source's `probe_cost` and a read at one; a call it cannot afford ends the
tick. A pause of up to `jitter_seconds` precedes every call.

**Custody** (§5): each NEW item's media is fetched in the same poll through
the network's `MediaSource`, kept in the `BlobStore` and indexed in
`media_objects` (`source = "social"`), and its content addresses become
`social_item.media_ref`, comma-separated in the item's order. An item already
recorded is never fetched. Custody that is not wired, or a fetch that fails,
records the item with no `media_ref` for that media, counted, never dropped.
Nothing here deletes bytes: a story frame's go at their bound, in
`nora.app.social_expiry`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

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
    BackfillEnd,
    BackfillItemOutcome,
    Cap,
    CustodyOutcome,
    Failed,
    FailureKind,
    Member,
    Page,
    PollOutcome,
    PollStage,
    Probe,
    SkipReason,
    SocialItem,
    cursor_is_bad,
    decide,
    frame_item,
)
from nora.infra.sql.social_stream import Backfill, Relist, SqlSocialStreamStore
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.social_poller")

_HOUR = timedelta(hours=1)
#: Two login halts inside this window lock the poller until an operator
#: clears it.
_HALT_WINDOW = timedelta(hours=24)
_HALTS_TO_LOCK = 2
#: A failed probe whose answer will not change on its own (no such account,
#: no mechanism, an id that is not an id) waits for the ceiling, not the floor.
_LASTING = frozenset({FailureKind.NOT_FOUND, FailureKind.UNSUPPORTED})
#: The bridge's 400 for a request it will not send. The adapter shape-checks
#: the igid and the username before they leave, so on a back-fill page it is
#: the cursor.
_REFUSED_REQUEST = "invalid_id"

#: A list page's items, as the web client asks for them (the bridge's
#: `timelinePageSize`).
LIST_PAGE_ITEMS = 12
#: The re-walk's pages per member beyond what the back-fill can have recorded
#: (`relist_max_pages`): one page of news recorded on top of it.
RELIST_EXTRA_PAGES = 1


def relist_max_pages(backfill_max_items: int) -> int:
    """The re-walk's last page per member: the pages the back-fill's items
    fill, and `RELIST_EXTRA_PAGES` more. No walk is longer, whatever an old
    pinned post makes their oldest recorded item."""
    return math.ceil(backfill_max_items / LIST_PAGE_ITEMS) + RELIST_EXTRA_PAGES

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
        # Login halts since the last operator clear; two inside
        # `_HALT_WINDOW` lock the login until a `cleared` transition.
        self._halts: list[datetime] = []
        self._locked_at_ms: int | None = None
        self._back_off: timedelta | None = None
        self._back_off_until: datetime | None = None
        # Person -> how long a failed probe of theirs waits (floor or ceiling).
        self._hold: dict[str, timedelta] = {}
        # Read kind ("story", "items") -> when its query went stale.
        self._stale: dict[str, datetime] = {}
        self._spent: deque[tuple[datetime, int]] = deque()
        self._attempted: dict[str, datetime] = {}
        self._answered: dict[str, datetime] = {}
        self._seeded = False
        self._calls_this_tick = 0
        # Whether any call this tick was answered (anything but unavailable):
        # the bridge is up, so an unavailable page is the page's own.
        self._answered_tick = False
        # Persons whose list was read this tick: one list page a member a tick.
        self._listed: set[str] = set()

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
        self._answered_tick = False
        self._listed = set()
        now = self._clock()
        if not self._seeded:
            await self._restore(now)
            self._seeded = True
        if not await self._login_ready(now):
            return
        roster = await self._roster()
        if roster is None:
            self._skip("roster_unavailable")
            return
        # Least recently probed first, answered or not, so a member whose
        # probes keep failing never sorts ahead of the rest for good.
        due = sorted(
            (m for m in roster if self._due(m.person, now)),
            key=lambda m: (self._attempted.get(m.person) or datetime.min.replace(tzinfo=UTC),
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
        await self._backfill(roster)
        await self._relist(roster)

    # -- the login -------------------------------------------------------------

    async def _restore(self, now: datetime) -> None:
        """Rebuild what a previous process knew: each member's last probe,
        the hour's spent calls (`social_poll`), and the login's halt, lock
        and back-off (its `source_health` transitions, replayed)."""
        # Every read comes before any state is set, and an unreadable one
        # raises: the tick stops, `_seeded` stays False and the next tick
        # reads all three again. Starting fresh instead would forget a live
        # halt, lock or back-off for the life of the process.
        probes = await self._store.last_probes(network=self._network)
        calls = await self._store.calls_since(network=self._network, since=now - _HOUR)
        # ponytail: replays every transition the login ever had; they are a
        # handful a day, so a bound is not worth a second query.
        rows = await self._health.transitions(source=self._health_source, since_ms=0)
        if rows is None:
            raise RuntimeError("social poller: login transitions unreadable")
        for person, times in probes.items():
            self._attempted[person] = times.attempted
            if times.answered is not None:
                self._answered[person] = times.answered
        for at, stage in calls:
            self._spent.append((at, self._source.probe_cost if stage == "probe" else 1))
        halted = False
        for row in rows:
            if row.scope:
                continue
            at = datetime.fromtimestamp(row.at_ms / 1000, UTC)
            if row.outcome == "cleared":
                halted, self._halts, self._locked_at_ms = False, [], None
                self._back_off = self._back_off_until = None
            elif row.outcome == "halted":
                halted = True
                if row.detail != "session":
                    self._halts.append(at)
            elif row.outcome == "locked":
                self._locked_at_ms = row.at_ms
            elif row.outcome == "backed_off":
                step = _step_of(row.detail)
                if step is not None:
                    self._back_off, self._back_off_until = step, at + step
            elif row.outcome == "up" and row.detail == "clean_probe":
                self._back_off = self._back_off_until = None
            elif row.outcome == "up" and row.detail == "session":
                halted = False
        if halted:
            self._halted = True

    async def _login_ready(self, now: datetime) -> bool:
        if self._locked_at_ms is not None and not await self._cleared(self._locked_at_ms):
            self._skip("halted")
            return False
        if self._halted is not False:
            if not await self._source.healthy():
                if self._halted is None:
                    await self._transition("", "halted", "session")
                self._halted = True
                self._skip("halted")
                return False
            was_halted = self._halted is True
            self._halted = False
            await self._transition("", "up", "session" if was_halted else "start")
            if was_halted:
                # A cleared checkpoint resumes on the ladder's top step.
                self._back_off = self._back_off_max
                self._back_off_until = now + self._back_off_max
                await self._transition(
                    "", "backed_off", f"after_halt {int(self._back_off_max.total_seconds())}s",
                )
        if self._back_off_until is not None and now < self._back_off_until:
            self._skip("backed_off")
            return False
        return True

    async def _cleared(self, locked_at_ms: int) -> bool:
        """Whether an operator has cleared the lock since it was set: a
        `cleared` transition row for this login."""
        rows = await self._health.transitions(
            source=self._health_source, since_ms=locked_at_ms,
        )
        if not any(r.outcome == "cleared" and not r.scope for r in rows or ()):
            return False
        log.info("social poller: the login's lock was cleared by an operator")
        self._locked_at_ms, self._halts, self._halted = None, [], None
        return True

    def _due(self, person: str, now: datetime) -> bool:
        attempted = self._attempted.get(person)
        if attempted is not None and now - attempted < self._hold.get(person, self._floor):
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
        """One upstream call: jitter, then the call, spent against the
        budget. Answers `(result, latency_ms)`. An `unsupported` answer is the
        bridge refusing locally, with nothing sent upstream, so it is not
        spent."""
        if self._settings.jitter_seconds > 0:
            await self._sleep(self._rand() * self._settings.jitter_seconds)
        self._calls_this_tick += 1
        started = time.monotonic()
        result = await call()
        latency = int((time.monotonic() - started) * 1000)
        if not (isinstance(result, Failed) and result.kind is FailureKind.UNAVAILABLE
                and result.detail != _REFUSED_REQUEST):
            self._answered_tick = True
        if isinstance(result, Failed) and result.kind is FailureKind.UNSUPPORTED:
            return result, latency
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
            backed = await self._start_back_off("rate_limited")
            await self._record_failure(member, "probe", "backed_off", ms, probe=probe)
            return backed
        await self._clean_probe()
        self._hold.pop(member.person, None)
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
            # A person's first list read, with no counts ever recorded, is
            # their history, not news, and the back-fill's first page (§7).
            return await self._read_items(member, probe, first=last is None)
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
        read_id = str(uuid.uuid4())
        frames = [
            frame_item(network=self._network, account=member.account.id, frame=f)
            for f in snap.frames
        ]
        known = await self._store.known_items(
            network=self._network, item_ids=[f.item_id for f in frames],
        )
        for item in frames:
            if item.item_id not in known:
                await self._insert(member, item, now, read_id=read_id)
        await self._follow_places([f for f in frames if f.item_id in known])
        shown = {f.item_id for f in frames}
        live = await self._store.live_frames(network=self._network, person=member.person, now=now)
        for item_id in sorted(live - shown):
            await self._store.record_state(
                network=self._network, item_id=item_id, state="gone",
                reason="not shown on a story read before its expiry",
            )
        await self._record(
            member, "story", "read_ok", ms, latest_reel_media=probe.latest_reel_media,
            advanced_marker=True, read_id=read_id,
        )
        return None

    async def _read_items(
        self, member: Member, probe: Probe, *, first: bool,
    ) -> SkipReason | None:
        if not self._afford(1):
            return "budget"
        stage: PollStage = "backfill" if first else "items"
        self._listed.add(member.person)
        page, ms = await self._call(
            stage, 1, lambda: self._source.list_items(account=member.account, after=None),
        )
        if isinstance(page, Failed):
            return await self._failed(member, stage, page, ms)
        await self._recovered("items")
        read_id = str(uuid.uuid4())
        # A first page's items are history up to the item limit; the day
        # limit is not applied, since a pinned post there can be old.
        recorded = await self._record_page(
            member, page, origin="backfill" if first else "poll", read_id=read_id,
            backfilled=0 if first else None, days=False,
        )
        await self._record(
            member, stage, "read_ok", ms, media_count=probe.media_count,
            total_clips_count=probe.total_clips_count, advanced_counts=True,
            read_id=read_id,
            cursor=self._onward(page, backfilled=recorded, paged=False) if first else None,
        )
        return None

    async def _record_page(
        self, member: Member, page: Page, *, origin: Literal["poll", "backfill"],
        read_id: str, backfilled: int | None = None, days: bool = True,
    ) -> int:
        """Record each new item of `page` → how many were recorded. The page
        carries each item's media: nothing is read per item. With
        `backfilled` (the person's back-filled items so far) it is a back-fill
        page: an item past the item limit, or (with `days`) older than the
        day limit, is not recorded."""
        known = await self._store.known_items(
            network=self._network, item_ids=[i.item_id for i in page.items],
        )
        await self._follow_places([i for i in page.items if i.item_id in known])
        seen = set(known)
        horizon = self._clock() - timedelta(days=self._settings.backfill_max_days)
        count = 0
        for item in page.items:
            if item.item_id in seen:
                self._backfill_item(origin, "known")
                continue
            if backfilled is not None and (
                (days and item.taken_at < horizon)
                or backfilled + count >= self._settings.backfill_max_items
            ):
                self._backfill_item(origin, "past_limit")
                continue
            seen.add(item.item_id)
            await self._insert(member, item, self._clock(), origin=origin, read_id=read_id)
            self._backfill_item(origin, "recorded")
            count += 1
        return count

    def _onward(self, page: Page, *, backfilled: int, paged: bool) -> str | None:
        """Where the back-fill goes on after `page`: its next cursor, or None
        when the walk ends on it (the grid ended, the item limit is reached,
        or the page reached past the day limit). A page read at a cursor
        reaches past it when any of its items is older, since every page
        after it is older still; the first page only when all of them are,
        since a pinned post there can be old whatever follows."""
        horizon = self._clock() - timedelta(days=self._settings.backfill_max_days)
        end: BackfillEnd | None = None
        if page.next_cursor is None:
            end = "grid_end"
        elif backfilled >= self._settings.backfill_max_items:
            end = "max_items"
        elif page.items and (any if paged else all)(i.taken_at < horizon for i in page.items):
            end = "max_days"
        if end is None:
            return page.next_cursor
        self._metrics.inc("nora_social_backfill_ended_total", network=self._network, reason=end)
        return None

    # -- the back-fill -----------------------------------------------------------

    async def _backfill(self, roster: Sequence[Member]) -> None:
        """One page for each member whose back-fill is not done, least
        recently paged first, up to the tick's cap, with what the probes and
        due reads left (§7). A halt or a back-off ends it, as it ends the
        tick."""
        cap = self._settings.backfill_pages_per_tick
        if cap <= 0 or Cap.LIST_ITEMS not in self._source.capabilities:
            return
        standing = await self._store.backfills(network=self._network)
        waiting = sorted(
            (m for m in roster
             if (b := standing.get(m.person)) is not None and not b.done
             and m.person not in self._listed),
            key=lambda m: (standing[m.person].last_at or datetime.min.replace(tzinfo=UTC),
                           m.person),
        )
        # What a page may not take: one probe and one read of every member,
        # so the hour still holds what the news needs.
        # ponytail: past hourly_budget / (probe_cost + 1) - 1 members (19 at
        # the defaults) no page ever fits; reserve only the members due within
        # the hour if a unit grows that large.
        reserve = len(roster) * (self._source.probe_cost + 1)
        for member in waiting[:cap]:
            if not self._may_read(Cap.LIST_ITEMS, "items", self._clock()):
                return
            if not self._afford(1 + reserve):
                return
            try:
                stop = await self._backfill_page(member, standing[member.person])
            except Exception:
                log.exception("social back-fill of one member failed; the tick continues")
                self._metrics.inc("nora_errors_total", stage="social_poller")
                continue
            if stop is not None:
                self._skip(stop)
                return

    async def _backfill_page(self, member: Member, at: Backfill) -> SkipReason | None:
        self._listed.add(member.person)
        page, ms = await self._call(
            "backfill", 1,
            lambda: self._source.list_items(account=member.account, after=at.cursor),
        )
        if isinstance(page, Failed):
            if at.cursor is None:
                return await self._failed(member, "backfill", page, ms)
            if page.kind is FailureKind.UNAVAILABLE and page.detail != _REFUSED_REQUEST \
                    and not self._answered_tick:
                # Nothing on this tick was answered: the bridge is healing,
                # and the page is not to blame. Recorded, not counted.
                await self._record(member, "backfill", "unavailable", ms)
                return "bridge_unavailable"
            stop = await self._failed(member, "backfill", page, ms)
            if page.detail == _REFUSED_REQUEST:
                await self._abandon(member)
                return stop
            # Its own row counts toward the cursor's failures
            # (`SqlSocialStreamStore.backfills`), which is what ends the walk.
            if page.kind not in (FailureKind.HALT, FailureKind.STALE_QUERY) and cursor_is_bad(
                rejected=at.rejected + (page.kind is FailureKind.BACK_OFF),
                failures=at.failures + 1,
            ):
                self._metrics.inc(
                    "nora_social_backfill_ended_total", network=self._network,
                    reason="bad_cursor",
                )
            return stop
        await self._recovered("items")
        read_id = str(uuid.uuid4())
        # The first page read again (the person's first list read predates the
        # back-fill) gives its cursor only: an item on it not yet recorded is
        # new since their last list read, and is the poll's, as news.
        if at.cursor is None:
            recorded = 0
            await self._follow_places(page.items)
        else:
            recorded = await self._record_page(
                member, page, origin="backfill", read_id=read_id, backfilled=at.items,
            )
        await self._record(
            member, "backfill", "read_ok", ms, read_id=read_id,
            cursor=self._onward(
                page, backfilled=at.items + recorded, paged=at.cursor is not None,
            ),
        )
        return None

    # -- tagged places -----------------------------------------------------------

    async def _follow_places(self, items: Sequence[SocialItem]) -> None:
        """Append a `place` row for each listed, already-recorded item whose
        tagged place differs from its current one (§5): no upstream call, the
        read is the one just made."""
        if not items:
            return
        current = await self._store.current_places(
            network=self._network, item_ids=[i.item_id for i in items],
        )
        for item in items:
            if item.item_id in current and current[item.item_id] != item.location:
                await self._store.record_place(
                    network=self._network, item_id=item.item_id, place=item.location,
                )

    async def _relist(self, roster: Sequence[Member]) -> None:
        """One re-walk page for each member whose back-fill is done and whose
        re-walk is not, least recently paged first, up to
        `relist_pages_per_tick` pages, after the back-fill and on its budget
        rule: the lowest priority of the tick."""
        cap = self._settings.relist_pages_per_tick
        if cap <= 0 or Cap.LIST_ITEMS not in self._source.capabilities:
            return
        standing = await self._store.backfills(network=self._network)
        walks = await self._store.relists(network=self._network)
        waiting = sorted(
            (m for m in roster
             if (b := standing.get(m.person)) is not None and b.done
             and (r := walks.get(m.person)) is not None and not r.done
             and m.person not in self._listed),
            key=lambda m: (walks[m.person].last_at or datetime.min.replace(tzinfo=UTC),
                           m.person),
        )
        reserve = len(roster) * (self._source.probe_cost + 1)
        for member in waiting[:cap]:
            if not self._may_read(Cap.LIST_ITEMS, "items", self._clock()):
                return
            if not self._afford(1 + reserve):
                return
            try:
                stop = await self._relist_page(member, walks[member.person])
            except Exception:
                log.exception("social re-walk of one member failed; the tick continues")
                self._metrics.inc("nora_errors_total", stage="social_poller")
                continue
            if stop is not None:
                self._skip(stop)
                return

    async def _relist_page(self, member: Member, at: Relist) -> SkipReason | None:
        self._listed.add(member.person)
        page, ms = await self._call(
            "relist", 1,
            lambda: self._source.list_items(account=member.account, after=at.cursor),
        )
        if isinstance(page, Failed):
            stop = await self._failed(member, "relist", page, ms)
            if page.detail == _REFUSED_REQUEST:
                await self._record_failure(member, "relist", "abandoned", 0)
            return stop
        await self._recovered("items")
        await self._follow_places(page.items)
        past = at.oldest is not None and any(i.taken_at < at.oldest for i in page.items)
        onward = (
            None if page.next_cursor is None or past
            or at.pages + 1 >= relist_max_pages(self._settings.backfill_max_items)
            else page.next_cursor
        )
        await self._record(member, "relist", "read_ok", ms, cursor=onward)
        return None

    async def _abandon(self, member: Member) -> None:
        """End a member's back-fill on a cursor the bridge refused: an
        `abandoned` row beside the call's own `error` row. It is a marker, not
        a call; were it lost, the error rows still end the walk after
        `BAD_CURSOR_FAILURES`."""
        await self._record_failure(member, "backfill", "abandoned", 0)
        self._metrics.inc(
            "nora_social_backfill_ended_total", network=self._network, reason="bad_cursor",
        )

    async def _failed(
        self, member: Member, stage: PollStage, failure: Failed, ms: int, *,
        latest_reel_media: int | None = None,
    ) -> SkipReason | None:
        """Act on a failed call's kind, then record it. Answers why the tick
        stops, or None when it goes on. A halt or back-off is held in memory
        and written to `source_health` BEFORE its `social_poll` row, and a
        row that cannot be written never undoes it."""
        if failure.kind is FailureKind.HALT:
            await self._halt(failure.detail)
            await self._record_failure(
                member, stage, "halted", ms, latest_reel_media=latest_reel_media,
            )
            return "halted"
        if failure.kind is FailureKind.BACK_OFF:
            stop = await self._start_back_off(failure.detail)
            await self._record_failure(
                member, stage, "backed_off", ms, latest_reel_media=latest_reel_media,
            )
            return stop
        if stage == "probe":
            lasting = failure.kind in _LASTING or failure.detail == "bad_target"
            self._hold[member.person] = self._ceiling if lasting else self._floor
        if failure.kind is FailureKind.STALE_QUERY and stage != "probe":
            await self._record(member, stage, "halted", ms, latest_reel_media=latest_reel_media)
            kind = "story" if stage == "story" else "items"
            if kind not in self._stale:
                await self._transition(kind, "stale_query", failure.detail)
            self._stale[kind] = self._clock()
            return None
        await self._record(member, stage, "error", ms, latest_reel_media=latest_reel_media)
        return None

    async def _halt(self, detail: str) -> None:
        now = self._clock()
        self._halted = True
        self._halts = [t for t in self._halts if now - t < _HALT_WINDOW] + [now]
        await self._transition("", "halted", detail)
        if len(self._halts) >= _HALTS_TO_LOCK and self._locked_at_ms is None:
            self._locked_at_ms = int(now.timestamp() * 1000)
            hours = int(_HALT_WINDOW.total_seconds() // 3600)
            await self._transition("", "locked", f"{len(self._halts)} halts in {hours}h")

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
            await self._transition("", "up", "clean_probe")

    async def _recovered(self, kind: str) -> None:
        if self._stale.pop(kind, None) is not None:
            await self._transition(kind, "up")

    # -- the Record ------------------------------------------------------------

    async def _insert(
        self, member: Member, item: SocialItem, now: datetime, *, read_id: str,
        origin: Literal["poll", "backfill"] = "poll",
    ) -> None:
        media_ref = await self._keep(item, now)
        await self._store.insert_item(
            item, person=member.person, origin=origin, seen_at=now, media_ref=media_ref,
            location=item.location, read_id=read_id,
        )

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
                            # What a video's perception will cost is read off
                            # its length (`nora.app.social_perception`).
                            **({"duration_seconds": media.duration_seconds}
                               if media.duration_seconds is not None else {}),
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
        read_id: str | None = None, cursor: str | None = None,
    ) -> None:
        if probe is not None:
            latest_reel_media = probe.latest_reel_media
            media_count, total_clips_count = probe.media_count, probe.total_clips_count
        await self._store.record_poll(
            network=self._network, person=member.person, stage=stage, outcome=outcome,
            latest_reel_media=latest_reel_media, media_count=media_count,
            total_clips_count=total_clips_count, advanced_marker=advanced_marker,
            advanced_counts=advanced_counts, latency_ms=ms, id=read_id, cursor=cursor,
        )
        self._metrics.inc(
            "nora_social_polls_total", network=self._network, stage=stage, outcome=outcome,
        )

    async def _record_failure(
        self, member: Member, stage: PollStage, outcome: PollOutcome, ms: int, *,
        probe: Probe | None = None, latest_reel_media: int | None = None,
    ) -> None:
        """`_record` for a halt or back-off, whose state is already set: a row
        that cannot be written is logged and counted, never raised."""
        try:
            await self._record(
                member, stage, outcome, ms, probe=probe, latest_reel_media=latest_reel_media,
            )
        except Exception:
            log.exception("social poller: a %s row was not written", outcome)
            self._metrics.inc("nora_errors_total", stage="social_poller")

    async def _transition(self, scope: str, outcome: str, detail: str = "") -> None:
        written = await self._health.record(
            source=self._health_source, scope=scope, event="transition", outcome=outcome,
            at_ms=int(self._clock().timestamp() * 1000), detail=detail,
        )
        if not written:
            self._metrics.inc("nora_errors_total", stage="source_health")

    def _skip(self, reason: SkipReason) -> None:
        self._metrics.inc("nora_social_poll_skipped_total", network=self._network, reason=reason)

    def _backfill_item(self, origin: str, outcome: BackfillItemOutcome) -> None:
        if origin == "backfill":
            self._metrics.inc(
                "nora_social_backfill_items_total", network=self._network, outcome=outcome,
            )

    def _custody(self, outcome: CustodyOutcome) -> None:
        self._metrics.inc("nora_social_custody_total", network=self._network, outcome=outcome)


def _step_of(detail: str) -> timedelta | None:
    """The back-off step a `backed_off` transition's detail ends in
    (`"<code> <seconds>s"`), or None when it carries none."""
    last = detail.rsplit(" ", 1)[-1]
    if not last.endswith("s") or not last[:-1].isdigit():
        return None
    return timedelta(seconds=int(last[:-1]))
