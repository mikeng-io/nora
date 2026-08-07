"""`PresenceCollector` — the change-recorder daemon (design/19 §4, DEC-55).

A SEPARATE process from Nora (`python -m nora.presence`). It shares the `nora`
database and the Life360 client library, nothing else, and either can be down
without the other (§4): if the collector is down, `whereis` degrades to
`unavailable`; if Nora is down, the collector keeps recording.

The per-circle loop (§4):
  1. read the circle (the client ensures the token, re-auths only on 403);
  2. classify each member's `wire_state` and count it, so «sharing off» is a
     KNOWN state, not a gap (§7.3);
  3. write a fix ONLY when the member's `ts` advanced — the change-recorder
     dedup lives in `PresenceStore.record_fix`, and this loop just hands it
     every located member;
  4. publish a liveness heartbeat (the age of this circle's last success);
  5. sleep the cadence — LONGER when the client reports auth failures, the
     exponential back-off that keeps a 403 from becoming a ban (§4.1).

**The canary lives here, not in Nora (§4):** a non-JSON body and an
unexpectedly-empty circle are the app-update breakage the study named, alarmed
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

from nora.domain.presence import PresenceFix
from nora.infra.geocode import ReverseGeocoder
from nora.infra.life360 import CircleRead, Life360Client, Member, ReadStatus
from nora.infra.sql.presence import PresenceStore

log = logging.getLogger("nora.presence.collector")

# The exponential back-off ceiling: a banned client is retried no more often than
# once an hour, however many failures accrue. The floor is the cadence; the
# growth is `cadence * 2^failures` (§4.1 — never hammer the credential POST).
MAX_BACKOFF_SECONDS = 3600.0

# How often a circle's saved PLACES refresh (design/23 §3). Places are static
# household landmarks — an hourly read is generous, and it rides the member
# cycle so it can never out-poll the back-off.
PLACES_REFRESH_SECONDS = 3600.0

# How often the retention sweep runs. Off the write path (§9), like the record
# store's prune; a movement history bounded by 180 days does not need pruning
# more than a few times a day.
PRUNE_INTERVAL_SECONDS = 6 * 3600.0


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
        geocoder: ReverseGeocoder | None = None,
        metrics: Any = None,
    ) -> None:
        self._client = client
        self._store = store
        self._circles = tuple(circles)
        self._cadence_seconds = cadence_seconds
        # Optional (None when no Google key): fills a place name on a fix Life360
        # returned without an address (§5). Never load-bearing — a miss stores
        # the coordinate as before.
        self._geocoder = geocoder
        self._metrics = metrics
        self._last_success_ms: dict[str, int] = {}
        # When each circle was FIRST attempted. The liveness age falls back to
        # this until a circle's first success, so a from-boot-broken collector
        # (rotated credential, challenge page on cycle one) emits a CLIMBING age
        # instead of no series at all — «not running» and «up but never
        # collecting» must not look identical (council #8, §4).
        self._first_seen_ms: dict[str, int] = {}
        # When each circle's saved PLACES were last fetched (design/23 §3).
        # Places change rarely, so they refresh at most every
        # `PLACES_REFRESH_SECONDS` — piggybacked on the member cycle rather than
        # a second loop, so places share the cadence back-off and can never
        # out-poll the account's fragility budget.
        self._places_fetched_ms: dict[str, int] = {}

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
            return read

        if not read.members:
            # A circle that read cleanly but has NO members is the study's other
            # canary (§4): a required host/UA/token changed and the envelope came
            # back empty. A real Ridgeline read returns five people. This is the
            # breakage, NOT a success — do not stamp `_last_success_ms`, or the
            # heartbeat reports age 0 (green) during the exact outage it exists to
            # expose (council #3). Publish liveness so the age keeps climbing from
            # the last real success, then stop before pretending a fix was written.
            self._count("nora_presence_canary_total", reason="empty_circle")
            self._publish_liveness(circle.circle_id, now_ms)
            return read

        for member in read.members:
            self._count("nora_presence_member_states_total", wire_state=member.wire_state.value)
            await self._record(circle, member, now_ms=now_ms)

        # Places refresh AFTER a clean member read on purpose: a cycle that
        # could not even read members has no business spending a second call on
        # the same struggling account (§3's fragility-budget argument).
        await self._refresh_places(circle, now_ms=now_ms)

        self._last_success_ms[circle.circle_id] = now_ms
        self._publish_liveness(circle.circle_id, now_ms)
        return read

    async def _refresh_places(self, circle: CircleSpec, *, now_ms: int) -> None:
        """Fetch the circle's saved places if due (first cycle, then at most
        every `PLACES_REFRESH_SECONDS`). NEVER raises; a failed fetch leaves the
        stored places as they were and retries next due cycle."""
        last = self._places_fetched_ms.get(circle.circle_id)
        if last is not None and (now_ms - last) < PLACES_REFRESH_SECONDS * 1000:
            return
        read = await self._client.read_places(circle_id=circle.circle_id, now_ms=now_ms)
        self._count(
            "nora_presence_place_reads_total",
            circle=circle.circle_id, status=read.status.value,
        )
        if read.status is not ReadStatus.OK:
            # Not stamped: the next member cycle retries. The read already
            # counted; auth-class failures alarmed inside the client.
            return
        try:
            await self._store.replace_places(
                circle_id=circle.circle_id, places=list(read.places)
            )
        except Exception:
            log.exception("places store failed for circle")
            return
        self._places_fetched_ms[circle.circle_id] = now_ms
        log.info("places refreshed: %d for circle", len(read.places))

    async def _record(self, circle: CircleSpec, member: Member, *, now_ms: int) -> None:
        person_id = circle.person_id_for(member.life360_id)
        if person_id is None:
            # A circle member Nora has no person for — recorded as nothing (the
            # config mapping is the operator's, §6). Its state was already
            # counted above; there is no fix to write without a person key.
            return
        if not member.has_fix:
            # sharing_off / disconnected / no_fix: a KNOWN state, already counted.
            # No row — its state rides the metric and its last stored fix, never
            # a null-island row (§7.6).
            return
        # `has_fix` guarantees these three are not None; narrow explicitly rather
        # than asserting (S101 forbids `assert` in production code).
        ts_ms, lat, lng = member.ts_ms, member.lat, member.lng
        if ts_ms is None or lat is None or lng is None:
            return
        # Life360 attaches an address to most fixes; reverse-geocode the ones it
        # does not so whereis can name a place, not raw coordinates (§5). Only the
        # WRITTEN-worthy address-less fix reaches here often, and the geocoder
        # memoises by location, so a stationary member is not re-billed. Fail-soft:
        # a miss leaves address None and the fix stores the coordinate, as before.
        address = member.address
        if address is None and self._geocoder is not None:
            address = await self._geocoder.address_for(lat, lng)
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
        )
        try:
            written = await self._store.record_fix(fix=fix)
        except Exception:
            # A store failure must not take the loop down; the next cycle retries.
            log.exception("presence record failed for %s", person_id)
            return
        if written:
            self._count(
                "nora_presence_fixes_total",
                circle=circle.circle_id,
                wire_state=member.wire_state.value,
            )

    def sleep_seconds(self) -> float:
        """The next inter-cycle sleep. The cadence when healthy, grown
        exponentially by the client's consecutive auth failures — the ban-safe
        back-off (§4.1), capped so a wedged account is still retried hourly."""
        failures = self._client.auth_failures
        if failures <= 0:
            return self._cadence_seconds
        # float() because typeshed types `int ** int` as Any (a negative
        # exponent would make it a float), and this module is mypy-strict.
        return min(self._cadence_seconds * float(2**failures), MAX_BACKOFF_SECONDS)

    def _publish_liveness(self, circle_id: str, now_ms: int) -> None:
        """Set the heartbeat gauge to the age of this circle's last SUCCESS. It
        is 0 right after a success and climbs while collections fail — «up but
        not collecting», which a frozen fix count cannot show (§4).

        Before the first success the baseline is when the circle was first
        attempted, so a collector that has NEVER succeeded still emits a climbing
        age from boot rather than no series at all (council #8)."""
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
            await asyncio.sleep(self.sleep_seconds())

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
            try:
                deleted = await self._store.prune(now_ms=int(time.time() * 1000))
                if deleted:
                    log.info("presence prune deleted %d fixes past retention", deleted)
            except Exception:
                log.exception("presence prune failed")
