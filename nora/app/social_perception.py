"""`SocialPerception` — what the social stream's items show, and the stimulus
each read raises (design/cognition-loop/22-social-stream.md §5, §6, §7;
design/cognition-loop/20-observe.md §1, §3).

**A pass over the Record, not a step of the poll.** The poller records an item
and its bytes and moves on; this pass, on its own clock, reads what is
recorded and still unperceived. A vision failure or a spent cap therefore
never holds up a probe, an item perceived on a later day is the same query
run on that day, and a restart loses nothing: every fact the pass decides on
is a row.

**Perception is ingest's comprehension, awaited** (`MediaComprehension.
perceive`): the same describer, the same concurrency slot, the same
`media_objects` row keyed by the bytes. The describer is told what it is
reading (`perception_context`: story frame, post or reel, collab, when it was
posted), from system facts only, never the caption.

**The cap is model calls per unit per day** (`[social.poll]
perception_daily_cap`, the day `[cognition] timezone`'s). Before an item is
read, what it may cost is estimated (`vision_calls`: one per image; per video,
one per `[media.describe.video] chunk_seconds` window of its length, up to
`chunk_max`, all of them when the length is unknown, plus the text pass), and
it is read only when EVERY unit its person is listed in can afford that; a
person in no unit's room is never read. What it is charged is what it spent:
nothing for a read that ended before any model was called, a landed video's
windows off the row it wrote, otherwise the estimate. Polled items go first;
a back-filled item may use at most half of what polled items leave
(`perception_allowance`). An item the cap stops is recorded `capped` and waits
a day; one whose read failed is recorded `unavailable` and waits a day too,
until its third failure, which is `failed`; a failure no later day can change
(`is_terminal`) is `failed` at once, and so is an item whose bytes were
deleted at the story bound before it was read (`expired`, no model called). A
`failed` item is never read again, and counts as attempted, so its read still
settles. Usage is summed from
`social_perception`, so a restart keeps it.

**One stimulus per read** (§5). A read that recorded new polled items raises
one `social` stimulus per unit of its person, once each of its items has an
attempt on record (perceived, failed or capped), so a session wakes on a
described story rather than a URL. Its `item` says what the read recorded
(`read_item`), its `at_ms` when the newest of them was posted, and its cues
are `cues_of` over the captions and what perception wrote. `ref` names the
read, and the stimulus table keeps one per `(unit, ref)`, so a pass that
finds the read again after a restart writes nothing. Back-filled items raise
none: history is evidence, not news. With `[cognition]` off there is no
stimulus store and nothing is raised.

**What is read is what may be read** (§2): `SqlSocialStreamStore` leaves out
an opted-out person's items and every gone or hidden item, for every reader.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo

from nora.domain.cues import cues_of
from nora.domain.media import MediaBlob, MediaObject
from nora.domain.ports import MediaObjectStore, StimulusStore
from nora.domain.social_stream import (
    MAX_PERCEPTION_ATTEMPTS,
    SOCIAL_READ_REF,
    PerceptionOutcome,
    is_terminal,
    made_no_call,
    perception_allowance,
    perception_context,
    read_item,
    vision_calls,
)
from nora.domain.stimuli import NewStimulus
from nora.infra.sql.social_stream import SettledRead, SqlSocialStreamStore, Unperceived
from nora.ingest.comprehension import MediaComprehension, Perceived, Subject
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.social_perception")

#: The labels a social item's media is read under; anything else is a file
#: nothing here reads.
_READ_TOPS = ("image", "video")


#: How long after a read completes its stimulus may still be raised. A read
#: waits for its items' perception, at most a day under the cap; past this a
#: unit the person joins later is not woken for reads that predate it.
_ANNOUNCE_WITHIN = timedelta(days=2)


class SocialPerception:
    def __init__(
        self,
        *,
        store: SqlSocialStreamStore,
        media_objects: MediaObjectStore,
        comprehension: MediaComprehension | None,
        stimuli: StimulusStore | None,
        units: Mapping[str, tuple[str, ...]],
        daily_cap: int,
        tz: tzinfo,
        chunk_seconds: int,
        chunk_max: int,
        tick_seconds: float,
        metrics: Metrics,
        batch: int = 50,
        rooms: Mapping[tuple[str, str], tuple[str, str]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._media_objects = media_objects
        self._comprehension = comprehension
        self._stimuli = stimuli
        self._units = units
        # `(person, unit) -> (platform, conversation_id)`: the room each
        # stimulus binds (`composition.social_person_rooms`). A pair with none
        # binds the unit's home room.
        self._rooms = rooms or {}
        self._cap = daily_cap
        self._tz = tz
        self._chunk_seconds = chunk_seconds
        self._chunk_max = chunk_max
        self._tick_seconds = tick_seconds
        self._metrics = metrics
        self._batch = batch
        self._clock = clock
        self._sleep = sleep

    async def run(self) -> None:
        log.info("social perception started: tick every %.0fs", self._tick_seconds)
        while True:
            await self._sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("social perception tick failed")
                self._metrics.inc("nora_errors_total", stage="social_perception")

    async def tick(self) -> None:
        """Perceive what is due today, then announce every read whose items
        are all settled."""
        if self._comprehension is not None:
            await self._perceive(self._comprehension)
        await self._announce()

    # -- perception ------------------------------------------------------------

    def _day_start(self, now: datetime) -> datetime:
        local = now.astimezone(self._tz)
        return datetime(local.year, local.month, local.day, tzinfo=self._tz)

    async def _perceive(self, comprehension: MediaComprehension) -> None:
        now = self._clock()
        since = self._day_start(now)
        items = await self._store.unperceived(since=since, limit=self._batch)
        if not items:
            return
        found = await self._media_objects.lookup(
            sha256s=tuple(sha for item in items for sha in item.media),
        )
        if not found.available:
            # Without the index the media's type is unknown, and reading
            # unknown bytes would claim nothing is in them. Next tick.
            log.warning("social perception: the media index is unreadable; nothing read")
            return
        used = await self._store.perception_used(since=since)
        for item in items:
            units = self._units.get(item.person, ())
            if item.expired:
                # Its bytes went at the story bound before any read landed:
                # nothing is left to read, and no day changes that.
                await self._settle(item, units, "failed", 0, now, "expired")
                continue
            objs = [found.objects.get(sha) for sha in item.media]
            cost = sum(self._calls_at_most(obj) for obj in objs)
            if not units or any(
                perception_allowance(
                    cap=self._cap, poll_used=used.get(u, {}).get("poll", 0),
                    backfill_used=used.get(u, {}).get("backfill", 0), origin=item.origin,
                ) < cost
                for u in units
            ):
                await self._settle(item, units, "capped", 0, now, "cap")
                continue
            context = perception_context(
                network=item.network, kind=item.kind, collab=item.collab,
                taken_at=item.taken_at, tz=self._tz,
            )
            reads = [
                await self._read(comprehension, item, obj, sha, context)
                for sha, obj in zip(item.media, objs, strict=True)
            ]
            calls = await self._spent(reads, objs, item.media)
            outcome, reason = self._outcome(reads, item.failures)
            await self._settle(item, units, outcome, calls, now, reason)
            for u in units:
                spent = used.setdefault(u, {})
                spent[item.origin] = spent.get(item.origin, 0) + calls

    def _calls_at_most(self, obj: MediaObject | None) -> int:
        """What reading one medium may cost, before it is read."""
        if obj is None:
            return 0
        duration = (obj.provenance or {}).get("duration_seconds")
        return vision_calls(
            top=obj.media_type.partition("/")[0].lower(),
            duration_seconds=float(duration) if isinstance(duration, int | float) else None,
            chunk_seconds=self._chunk_seconds, chunk_max=self._chunk_max,
        )

    async def _spent(
        self, reads: list[Perceived], objs: list[MediaObject | None], shas: tuple[str, ...],
    ) -> int:
        """The model calls the reads made: none for a read that ended before
        any model was called; for a video read that landed, the windows it
        cut, off the row it wrote, plus its text pass; otherwise the most the
        read could have made."""
        spent = 0
        landed = await self._media_objects.lookup(sha256s=shas)
        for read, obj, sha in zip(reads, objs, shas, strict=True):
            if made_no_call(read.reason) or read.status == "nothing_found":
                continue
            row = landed.objects.get(sha) if landed.available else None
            windows = (row.provenance or {}).get("video_windows_cut") if row is not None else None
            if read.status == "ok" and isinstance(windows, int):
                spent += windows + 1
            else:
                spent += self._calls_at_most(obj)
        return spent

    @staticmethod
    def _outcome(reads: list[Perceived], failures: int) -> tuple[PerceptionOutcome, str]:
        """An item's outcome from its media's reads: a passing failure is
        `unavailable` until the item has failed `MAX_PERCEPTION_ATTEMPTS` times,
        then `failed`; a permanent one is `failed` at once."""
        passing = [r for r in reads if r.status == "unavailable" and not is_terminal(r.reason)]
        if passing:
            if failures + 1 >= MAX_PERCEPTION_ATTEMPTS:
                return "failed", "attempts"
            return "unavailable", passing[0].reason
        lasting = [r for r in reads if r.status == "unavailable"]
        if lasting:
            return "failed", lasting[0].reason
        if any(r.status == "ok" for r in reads):
            return "ok", ""
        return "nothing_found", reads[0].reason if reads else ""

    async def _read(
        self, comprehension: MediaComprehension, item: Unperceived,
        obj: MediaObject | None, sha256: str, context: str,
    ) -> Perceived:
        if obj is None:
            # Custody kept the bytes and their index row was not written:
            # their type is unknown, so they are not read (R-27).
            return Perceived("unavailable", "no_index")
        top = obj.media_type.partition("/")[0].lower()
        return await comprehension.perceive(
            subject=Subject(
                blob=MediaBlob(sha256=sha256, media_type=obj.media_type, size=obj.size),
                label=top if top in _READ_TOPS else "file",
            ),
            platform=item.network, context=context,
        )

    async def _settle(
        self, item: Unperceived, units: tuple[str, ...], outcome: PerceptionOutcome,
        calls: int, now: datetime, reason: str,
    ) -> None:
        await self._store.record_perception(
            network=item.network, item_id=item.item_id, person=item.person, units=units,
            origin=item.origin, outcome=outcome, calls=calls, at=now, reason=reason or None,
        )
        self._metrics.inc("nora_perception_total", source="social", outcome=outcome)

    # -- the stimulus ----------------------------------------------------------

    async def _announce(self) -> None:
        if self._stimuli is None:
            return
        reads = await self._store.settled_reads(
            require_perceived=self._comprehension is not None, units=self._units,
            since=self._clock() - _ANNOUNCE_WITHIN,
        )
        for read in reads:
            units = tuple(u for u in self._units.get(read.person, ()) if u not in read.announced)
            if not units:
                continue
            cues = await self._cues(read)
            if cues is None:
                continue
            stimulus_item = read_item([(i.kind, i.collab) for i in read.items])
            at_ms = int(max(i.taken_at for i in read.items).timestamp() * 1000)
            now_ms = int(self._clock().timestamp() * 1000)
            for unit in units:
                platform, conversation_id = self._rooms.get((read.person, unit), (None, None))
                sid = await self._stimuli.append(
                    stimulus=NewStimulus(
                        unit=unit, kind="social", item=stimulus_item,
                        ref=f"{SOCIAL_READ_REF}:{read.read_id}", at_ms=at_ms,
                        platform=platform, conversation_id=conversation_id,
                        person_id=read.person, cues=cues,
                    ),
                    recorded_at_ms=now_ms,
                )
                if sid is not None:
                    for cue in cues or ("none",):
                        self._metrics.inc("nora_stimulus_total", kind="social", cue=cue)

    async def _cues(self, read: SettledRead) -> tuple[str, ...] | None:
        """§3's cues over the read's captions and what perception wrote, or
        None when the media index could not be read (tried next tick)."""
        shas = tuple(sha for item in read.items for sha in item.media)
        texts = [item.caption or "" for item in read.items]
        if shas:
            found = await self._media_objects.lookup(sha256s=shas)
            if not found.available:
                return None
            for sha in shas:
                obj = found.objects.get(sha)
                if obj is not None:
                    texts += [obj.title, obj.description, obj.content]
        return cues_of("\n".join(t for t in texts if t))
