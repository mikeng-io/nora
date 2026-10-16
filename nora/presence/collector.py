"""`PresenceCollector` — the change-recorder daemon (DEC-55).

A SEPARATE process from Nora (`python -m nora.presence`). It shares the `nora`
database and the Life360 client library, nothing else, and either can be down
without the other: if the collector is down, `whereis` degrades to
`unavailable`; if Nora is down, the collector keeps recording.

The per-circle loop:
  1. read the circle (the client ensures the token, re-auths only on 403);
  2. classify each member's `wire_state` and count it, so «sharing off» is a
     KNOWN state, not a gap;
  3. write a fix ONLY when the member's `ts` advanced — the change-recorder
     dedup lives in `PresenceStore.record_fix`, and this loop just hands it
     every located member;
  4. publish a liveness heartbeat (the age of this circle's last success);
  5. sleep the cadence — LONGER when the client reports auth failures, the
     exponential back-off that keeps a 403 from becoming a ban.

**The canary lives here, not in Nora:** a non-JSON body and an
unexpectedly-empty circle are the app-update breakage, alarmed
via `nora_presence_canary_total`. Nora never sees this fragility — she reads the
local table.

`collect_once` is separated from the infinite loop so the loop is a thin
sleep-and-repeat and every behaviour (dedup, classification, canary, heartbeat)
is unit-testable against one cycle without a running daemon.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nora.domain.ports import SourceHealthLog
from nora.domain.presence import PresenceFix
from nora.infra.life360 import CircleRead, Life360Client, Member, ReadStatus
from nora.infra.sql.presence import PresenceStore

log = logging.getLogger("nora.presence.collector")

# The exponential back-off ceiling: a banned client is retried no more often than
# once an hour, however many failures accrue. The floor is the cadence; the
# growth is `cadence * 2^failures` (never hammer the credential POST).
MAX_BACKOFF_SECONDS = 3600.0
# Ceiling on the back-off/still EXPONENT (not the result) so `2**n` cannot
# overflow float after days of stillness or a long ban; the result is bounded by
# the still-ceiling / MAX_BACKOFF regardless.
_EXP_CAP = 32

# How often a circle's saved PLACES refresh. Places are static
# household landmarks — an hourly read is generous, and it rides the member
# cycle so it can never out-poll the back-off.
PLACES_REFRESH_SECONDS = 3600.0

# How often the retention sweep runs. Off the write path, like the record
# store's prune; a movement history bounded by 180 days does not need pruning
# more than a few times a day.
PRUNE_INTERVAL_SECONDS = 6 * 3600.0

# How long one `source_health` poll row may take to write before the cycle
# gives up on it (counted, never raised).
HEALTH_WRITE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class CircleSpec:
    """One circle to collect, and how its Life360 member ids map to `person_id`s
    (from `PresenceConfig`). `member_person_ids` values ARE the disclosure roster
    the R-21 seam checks against."""

    circle_id: str
    members: Mapping[str, str]   # life360_member_id -> person_id

    def person_id_for(self, life360_id: str) -> str | None:
        return self.members.get(life360_id)


class PresenceCollector:
    """Collects presence for one or more circles into `PresenceStore`."""

    def __init__(
        self,
        *,
        client: Life360Client,
        store: PresenceStore,
        circles: Sequence[CircleSpec],
        cadence_seconds: float,
        cadence_moving_seconds: float | None = None,
        cadence_still_seconds: float | None = None,
        metrics: Any = None,
        health_log: SourceHealthLog | None = None,
    ) -> None:
        self._client = client
        # Every background poll's outcome, one append-only row each
        # «the collector's recorded polls succeeded» is how a later step proves
        # presence was healthy for an interval before reading an absence as a
        # signal. `None` records nothing.
        self._health_log = health_log
        self._store = store
        self._circles = tuple(circles)
        self._cadence_seconds = cadence_seconds
        # Adaptive band. Default to the fixed cadence at both ends when a
        # caller omits them, giving a constant interval.
        self._cadence_moving_seconds = (
            cadence_moving_seconds if cadence_moving_seconds is not None else cadence_seconds
        )
        self._cadence_still_seconds = (
            cadence_still_seconds if cadence_still_seconds is not None else cadence_seconds
        )
        self._metrics = metrics
        self._last_success_ms: dict[str, int] = {}
        # When each circle was FIRST attempted. The liveness age falls back to
        # this until a circle's first success, so a from-boot-broken collector
        # (rotated credential, challenge page on cycle one) emits a CLIMBING age
        # instead of no series at all — «not running» and «up but never
        # collecting» must not look identical.
        self._first_seen_ms: dict[str, int] = {}
        # The saved-places refresh schedule, per circle. Three
        # maps rather than one because success and failure pace differently:
        # `_places_fetched_ms` is the NEXT-eligible
        # instant after a success (now + `PLACES_REFRESH_SECONDS` — places are
        # static); `_places_retry_ms` is the next-eligible instant after a
        # FAILURE, set by an exponential back-off so a places-endpoint 403 does
        # not re-POST the credential every member cycle; `_places_failures`
        # counts consecutive failures to grow that back-off. A success clears
        # the failure pair.
        self._places_fetched_ms: dict[str, int] = {}
        self._places_retry_ms: dict[str, int] = {}
        self._places_failures: dict[str, int] = {}
        # Consecutive still cycles per circle — how many OK reads in a row saw
        # NOBODY driving. 0 means the last read had a driver (poll fast); it
        # grows while the circle sits, and `sleep_seconds` reads it to ramp the
        # interval from the moving floor toward the still ceiling.
        self._still_cycles: dict[str, int] = {}

    def circle_summary(self) -> list[dict[str, object]]:
        """The circles this collector is configured to poll, with the count of
        member→person mappings each carries — for the `/health` endpoint. The
        diagnostic that makes a stale identity.toml obvious:
        `1 circle, 1 member` is instantly wrong for a 10-person circle."""
        return [
            {"circle_id": c.circle_id, "members": len(c.members)}
            for c in self._circles
        ]

    async def collect_once(self, circle: CircleSpec, *, now_ms: int) -> CircleRead:
        """One collection cycle for one circle. NEVER raises.

        Reads the circle, classifies and counts every member, writes a fix for
        each located member whose `ts` advanced, trips the canary on a non-JSON
        or empty read, and publishes the liveness heartbeat. Returns the read so
        the loop can size its back-off.
        """
        self._first_seen_ms.setdefault(circle.circle_id, now_ms)
        read = await self._client.read_circle(circle_id=circle.circle_id, now_ms=now_ms)
        self._count("nora_presence_reads_total", circle=circle.circle_id, status=read.status.value)

        if read.status is ReadStatus.CHALLENGE:
            # The client already alarmed `nora_presence_auth_alarms_total`; the
            # canary is the collector's own view of the same breakage.
            self._count("nora_presence_canary_total", reason="non_json")
        if read.status is not ReadStatus.OK:
            self._publish_liveness(circle.circle_id, now_ms)
            await self._record_poll(circle.circle_id, read.status.value, now_ms)
            return read

        if not read.members:
            # A circle that read cleanly but has NO members is the study's other
            # canary: a required host/UA/token changed and the envelope came
            # back empty. This is the
            # breakage, NOT a success — do not stamp `_last_success_ms`, or the
            # heartbeat reports age 0 (green) during the exact outage it exists to
            # expose. Publish liveness so the age keeps climbing from
            # the last real success, then stop before pretending a fix was written.
            self._count("nora_presence_canary_total", reason="empty_circle")
            self._publish_liveness(circle.circle_id, now_ms)
            # Not `ok`: the read returned, and it is the breakage (the canary).
            await self._record_poll(circle.circle_id, "empty_circle", now_ms)
            return read

        moving = False
        for member in read.members:
            self._count("nora_presence_member_states_total", wire_state=member.wire_state.value)
            if member.is_driving:
                moving = True
            await self._record(circle, member, now_ms=now_ms)
        # Adaptive cadence signal: a circle with any driver polls at the
        # moving floor next cycle; one where all fixes are stationary lets the
        # still-counter grow so the interval ramps toward the ceiling. Driving is
        # the movement flag, not «a fix was written» — Life360 pushes a fresh ts
        # for a PARKED phone too, so a written fix does not mean anyone moved.
        self._still_cycles[circle.circle_id] = (
            0 if moving else self._still_cycles.get(circle.circle_id, 0) + 1
        )

        # Places refresh AFTER a clean member read on purpose: a cycle that
        # could not even read members has no business spending a second call on
        # the same struggling account.
        await self._refresh_places(circle, now_ms=now_ms)

        self._last_success_ms[circle.circle_id] = now_ms
        self._publish_liveness(circle.circle_id, now_ms)
        await self._record_poll(circle.circle_id, ReadStatus.OK.value, now_ms)
        return read

    async def _record_poll(self, circle_id: str, outcome: str, now_ms: int) -> None:
        """One `source_health` row for this poll. Never raises and never
        stops the cycle: the log answers False on a failed write, which is
        counted here."""
        if self._health_log is None:
            return
        try:
            # Bounded: a hung write must not stall the circle's cycle.
            ok = await asyncio.wait_for(
                self._health_log.record(
                    source="presence", scope=circle_id, event="poll", outcome=outcome,
                    at_ms=now_ms,
                ),
                timeout=HEALTH_WRITE_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception("presence poll health row not written")
            ok = False
        if not ok:
            self._count("nora_errors_total", stage="source_health")

    async def _refresh_places(self, circle: CircleSpec, *, now_ms: int) -> None:
        """Fetch the circle's saved places if due. NEVER raises.

        Two gates, not one: a SUCCESS waits the full
        `PLACES_REFRESH_SECONDS` (places are static), and a FAILURE backs off
        exponentially — `cadence * 2^failures`, capped hourly — instead of
        retrying every member cycle. Without the failure gate a places-endpoint
        403 would re-POST the credential every cycle indefinitely (`read_places`'s
        403 path drops the token and re-auths), because the member read resets
        the client's shared `auth_failures` before each places fetch, so its own
        back-off could never see the places failure. This gate is the rate half;
        `Life360Client._places_banned` is the alarm half."""
        cid = circle.circle_id
        next_ok = self._places_fetched_ms.get(cid)
        next_retry = self._places_retry_ms.get(cid)
        due_after = max(x for x in (next_ok, next_retry) if x is not None) if (
            next_ok is not None or next_retry is not None
        ) else None
        if due_after is not None and now_ms < due_after:
            return
        read = await self._client.read_places(circle_id=cid, now_ms=now_ms)
        self._count(
            "nora_presence_place_reads_total", circle=cid, status=read.status.value,
        )
        if read.status is not ReadStatus.OK:
            self._places_failures[cid] = failures = self._places_failures.get(cid, 0) + 1
            backoff = min(self._cadence_seconds * (2**failures), MAX_BACKOFF_SECONDS)
            self._places_retry_ms[cid] = now_ms + int(backoff * 1000)
            return
        try:
            await self._store.replace_places(circle_id=cid, places=list(read.places))
        except Exception:
            log.exception("places store failed for circle")
            return
        self._places_failures.pop(cid, None)
        self._places_retry_ms.pop(cid, None)
        self._places_fetched_ms[cid] = now_ms + int(PLACES_REFRESH_SECONDS * 1000)
        log.info("places refreshed: %d for circle", len(read.places))

    async def _record(self, circle: CircleSpec, member: Member, *, now_ms: int) -> bool:
        """Write the member's fix if its `ts` advanced. Returns whether a row was
        written — the background loop ignores it, but `refresh_member` reads it to
        know the on-demand fresh fix has landed."""
        person_id = circle.person_id_for(member.life360_id)
        if person_id is None:
            # A circle member Nora has no person for — recorded as nothing (the
            # config mapping is the operator's). Its state was already
            # counted above; there is no fix to write without a person key.
            return False
        if not member.has_fix:
            # sharing_off / disconnected / no_fix: a KNOWN state, already counted.
            # No row — its state rides the metric and its last stored fix, never
            # a null-island row.
            return False
        # `has_fix` guarantees these three are not None; narrow explicitly rather
        # than asserting (S101 forbids `assert` in production code).
        ts_ms, lat, lng = member.ts_ms, member.lat, member.lng
        if ts_ms is None or lat is None or lng is None:
            return False
        # Life360's own label, stored as-is. The collector talks to Life360
        # ALONE. It does not reverse-geocode:
        # that would bill a Google Geocoding call per tracked person every tick
        # for addresses nobody reads. `where_is`
        # (`nora.agent.whereis.Whereis.locate`) does it on demand, only for a
        # fix someone actually asked about and only when Life360 gave no
        # address of its own.
        address = member.address
        fix = PresenceFix(
            circle_id=circle.circle_id,
            person_id=person_id,
            ts=ts_ms,
            lat=lat,
            lng=lng,
            wire_state=member.wire_state,
            recorded_at=now_ms,
            accuracy_m=member.accuracy_m,
            is_driving=member.is_driving,
            battery=member.battery,
            address=address,
            speed_kmh=member.speed_kmh,
        )
        try:
            written = await self._store.record_fix(fix=fix)
        except Exception:
            # A store failure must not take the loop down; the next cycle retries.
            log.exception("presence record failed for %s", person_id)
            return False
        if written:
            self._count(
                "nora_presence_fixes_total",
                circle=circle.circle_id,
                wire_state=member.wire_state.value,
            )
        return written

    def _member_for(self, person_id: str) -> tuple[CircleSpec, str] | None:
        """The (circle, life360 member id) a person_id maps to, or None — the
        reverse of `CircleSpec.person_id_for`, for the on-demand refresh."""
        for circle in self._circles:
            for life360_id, pid in circle.members.items():
                if pid == person_id:
                    return circle, life360_id
        return None

    async def refresh_member(
        self, *, person_id: str, timeout_seconds: float, poll_interval: float = 2.0
    ) -> bool:
        """On-demand LIVE refresh for one person (DEC-67, whereis freshness): ping
        the member's phone for a fresh position, then poll until a fix newer than
        what's stored lands or `timeout_seconds` elapses — recording it through the
        SAME path as the background loop, so the on-demand reading persists to the
        DB. True once a fresh fix is written; False on an unknown person, a refused
        request with nothing fresher to read, or timeout. NEVER raises.

        The poll is BOUNDED (a read every `poll_interval` up to the deadline) so an
        on-demand request can never become the tight Life360 read-loop the ban
        back-off exists to prevent. And it HONOURS that back-off: when the
        background loop is in a ban-safe back-off (the client reports auth
        failures — a 429 or a fresh-token 403), an on-demand ping+poll would fire
        UNPACED reads exactly when the account is most rate-sensitive, so it is
        skipped — `where_is` falls back to the stored fix (never load-bearing) and
        the loop's back-off does the waiting."""
        if self._client.auth_failures > 0:
            return False
        located = self._member_for(person_id)
        if located is None:
            return False
        circle, member_id = located
        await self._client.request_location(circle_id=circle.circle_id, member_id=member_id)
        deadline = time.time() + timeout_seconds
        while True:
            now_ms = int(time.time() * 1000)
            read = await self._client.read_circle(circle_id=circle.circle_id, now_ms=now_ms)
            if read.status is not ReadStatus.OK:
                # A ban/challenge that began DURING the refresh: stop polling
                # immediately rather than fire more unpaced reads at a refusing
                # account (the entry gate only saw a clean state). where_is falls back to the
                # stored fix.
                return False
            member = next(
                (m for m in read.members if m.life360_id == member_id), None
            )
            if member is not None and await self._record(circle, member, now_ms=now_ms):
                return True
            if time.time() + poll_interval >= deadline:
                return False
            await asyncio.sleep(poll_interval)

    def sleep_seconds(self, circle_id: str) -> float:
        """The next inter-cycle sleep for one circle. The larger of two floors,
        so whichever demands MORE waiting wins:

        - the adaptive cadence: the moving floor while a driver is present,
          ramped toward the still ceiling by consecutive stationary cycles;
        - the ban-safe back-off: the base cadence grown `* 2^failures` by the
          client's consecutive auth failures, capped so a wedged account is
          still retried hourly.

        `max` of the two means a 429 (failures > 0) always lengthens the sleep
        regardless of who is driving — ban safety is never traded for freshness —
        while a healthy moving circle polls at the floor.
        """
        # float() because typeshed types `int ** int` as Any (a negative
        # exponent would make it a float), and this module is mypy-strict.
        #
        # Both exponents are CLAMPED before exponentiating.
        # `min(...)` caps the RESULT but Python evaluates `2**n` first, and
        # `_still_cycles` grows unbounded (one per still cycle), so after ~1024
        # consecutive still reads — days of a circle sitting home — `2**still`
        # exceeds DBL_MAX and `float(...)` raises OverflowError. That call is
        # outside `_run_circle`'s try/except, so it would take down the WHOLE
        # collector. `_EXP_CAP` (2^32·cadence already dwarfs both ceilings) makes
        # the exponent safe while the `min` still bounds the result.
        failures = min(self._client.auth_failures, _EXP_CAP)
        backoff = (
            min(self._cadence_seconds * float(2**failures), MAX_BACKOFF_SECONDS)
            if failures > 0
            else 0.0
        )
        still = min(self._still_cycles.get(circle_id, 0), _EXP_CAP)
        adaptive = (
            self._cadence_moving_seconds
            if still <= 0
            else min(
                self._cadence_moving_seconds * float(2**still),
                self._cadence_still_seconds,
            )
        )
        return max(adaptive, backoff)

    def _publish_liveness(self, circle_id: str, now_ms: int) -> None:
        """Set the heartbeat gauge to the age of this circle's last SUCCESS. It
        is 0 right after a success and climbs while collections fail — «up but
        not collecting», which a frozen fix count cannot show.

        Before the first success the baseline is when the circle was first
        attempted, so a collector that has NEVER succeeded still emits a climbing
        age from boot rather than no series at all."""
        baseline = self._last_success_ms.get(circle_id) or self._first_seen_ms.get(circle_id)
        if baseline is None:
            return
        age = max(0.0, (now_ms - baseline) / 1000.0)
        if self._metrics is not None:
            self._metrics.gauge(
                "nora_presence_last_collection_age_seconds", age, circle=circle_id
            )

    def _count(self, name: str, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, **labels)

    # ── the infinite loops (thin; behaviour is in collect_once/prune) ─────────

    async def run(self) -> None:
        """One independent loop per circle, plus the periodic prune. Runs until
        cancelled."""
        loops = [self._run_circle(c) for c in self._circles]
        loops.append(self._prune_loop())
        await asyncio.gather(*loops)

    async def _run_circle(self, circle: CircleSpec) -> None:
        while True:
            now_ms = int(time.time() * 1000)
            try:
                await self.collect_once(circle, now_ms=now_ms)
            except Exception:
                # collect_once is documented never to raise; belt for braces so
                # one bad cycle never kills a circle's loop.
                log.exception("presence collect cycle failed for %s", circle.circle_id)
            await asyncio.sleep(self.sleep_seconds(circle.circle_id))

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
            try:
                deleted = await self._store.prune(now_ms=int(time.time() * 1000))
                if deleted:
                    log.info("presence prune deleted %d fixes past retention", deleted)
            except Exception:
                log.exception("presence prune failed")
