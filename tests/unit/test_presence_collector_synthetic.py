"""`PresenceCollector` against fakes for Life360 and the store (DEC-55, DEC-60,
DEC-67, DEC-69).

The data is invented: person keys `p_alpha` / `p_beta`, circle `c_one`,
coordinates around (22.3000, 114.1700).

The fakes only speak the surface the collector uses. The Life360 fake is
scripted: each `read_circle` / `read_places` call pops the next scripted
answer, and a 403-class answer bumps `auth_failures` the way the real client
does, so the collector's back-off reads the same signal it reads in production.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest

from nora.domain.presence import PresenceFix, SavedPlace, WireState
from nora.infra.life360 import CircleRead, Member, PlacesRead, ReadStatus
from nora.presence import collector as collector_module
from nora.presence.collector import (
    MAX_BACKOFF_SECONDS,
    PLACES_REFRESH_SECONDS,
    CircleSpec,
    PresenceCollector,
)
from nora.testing.stimuli import InMemorySourceHealthLog

T0 = 1_790_000_000_000
CIRCLE = "c_one"
CADENCE = 90.0
SPEC = CircleSpec(circle_id=CIRCLE, members={"m_alpha": "p_alpha", "m_beta": "p_beta"})


def member(life_id: str, ts_ms: int, *, lat: float = 22.3000, lng: float = 114.1700,
           driving: bool = False) -> Member:
    return Member(
        life360_id=life_id, wire_state=WireState.LIVE, ts_ms=ts_ms, lat=lat, lng=lng,
        accuracy_m=20.0, is_driving=driving,
    )


def ok(*members: Member) -> CircleRead:
    return CircleRead(status=ReadStatus.OK, members=tuple(members))


def banned() -> CircleRead:
    return CircleRead(status=ReadStatus.BANNED, detail="403 on a fresh token")


def place(place_id: str, name: str) -> SavedPlace:
    return SavedPlace(
        circle_id=CIRCLE, place_id=place_id, name=name, lat=22.3010, lng=114.1710,
        updated_ms=T0, radius_m=80.0,
    )


class FakeLife360:
    """Scripted stand-in for `Life360Client`. The last scripted answer repeats."""

    def __init__(self, circle_reads=(), places_reads=()) -> None:
        self._circle = list(circle_reads)
        self._places = list(places_reads)
        self.auth_failures = 0
        self.circle_calls = 0
        self.places_calls = 0
        self.pings: list[tuple[str, str]] = []

    @staticmethod
    def _next(script: list):
        return script.pop(0) if len(script) > 1 else script[0]

    async def read_circle(self, *, circle_id: str, now_ms: int) -> CircleRead:
        self.circle_calls += 1
        read = self._next(self._circle)
        if read.status is ReadStatus.OK:
            self.auth_failures = 0
        elif read.status is ReadStatus.BANNED:
            self.auth_failures += 1
        return read

    async def read_places(self, *, circle_id: str, now_ms: int) -> PlacesRead:
        self.places_calls += 1
        return self._next(self._places)

    async def request_location(self, *, circle_id: str, member_id: str) -> bool:
        self.pings.append((circle_id, member_id))
        return True


class FakeStore:
    """`record_fix` writes only when `ts` strictly advances (R-32)."""

    def __init__(self) -> None:
        self.fixes: list[PresenceFix] = []
        self.places: dict[str, list[SavedPlace]] = {}
        self.place_writes = 0
        self._last_ts: dict[str, int] = {}

    async def record_fix(self, *, fix: PresenceFix) -> bool:
        if fix.ts <= self._last_ts.get(fix.person_id, -1):
            return False
        self._last_ts[fix.person_id] = fix.ts
        self.fixes.append(fix)
        return True

    async def replace_places(self, *, circle_id: str, places: list[SavedPlace]) -> None:
        self.place_writes += 1
        self.places[circle_id] = places

    async def prune(self, *, now_ms: int) -> int:
        return 0


class FakeMetrics:
    def __init__(self) -> None:
        self.counts: dict[tuple, float] = defaultdict(float)
        self.gauges: dict[tuple, float] = {}

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        self.counts[(name, tuple(sorted(labels.items())))] += value

    def gauge(self, name: str, value: float, **labels: object) -> None:
        self.gauges[(name, tuple(sorted(labels.items())))] = value

    def total(self, name: str, **labels: object) -> float:
        want = set(labels.items())
        return sum(v for (n, lab), v in self.counts.items() if n == name and want <= set(lab))

    def age(self) -> float:
        return self.gauges[("nora_presence_last_collection_age_seconds", (("circle", CIRCLE),))]


def build(client, *, store=None, metrics=None, health=None, **kw):
    store = store or FakeStore()
    collector = PresenceCollector(
        client=client, store=store, circles=[SPEC], cadence_seconds=CADENCE,
        metrics=metrics, health_log=health, **kw,
    )
    return collector, store


# ── DEC-55 (1): a change-recorder, not a sampler ────────────────────────────────


async def test_two_reads_of_an_unmoved_member_record_one_fix():
    client = FakeLife360([ok(member("m_alpha", T0))], [PlacesRead(ReadStatus.OK)])
    metrics = FakeMetrics()
    collector, store = build(client, metrics=metrics)
    await collector.collect_once(SPEC, now_ms=T0 + 1_000)
    await collector.collect_once(SPEC, now_ms=T0 + 91_000)
    assert [f.person_id for f in store.fixes] == ["p_alpha"]
    assert metrics.total("nora_presence_fixes_total") == 1
    assert metrics.total("nora_presence_reads_total", status="ok") == 2


async def test_a_member_with_no_person_mapping_or_no_position_writes_no_row():
    unmapped = member("m_stranger", T0)
    sharing_off = Member(life360_id="m_beta", wire_state=WireState.SHARING_OFF)
    client = FakeLife360([ok(unmapped, sharing_off)], [PlacesRead(ReadStatus.OK)])
    metrics = FakeMetrics()
    collector, store = build(client, metrics=metrics)
    await collector.collect_once(SPEC, now_ms=T0 + 1_000)
    assert store.fixes == []
    # ...but «sharing off» is a KNOWN state, counted, not a gap.
    assert metrics.total("nora_presence_member_states_total", wire_state="sharing_off") == 1


# ── DEC-55 (2): the 403 path is bounded, the waiting is the collector's ─────────


def test_the_sleep_grows_with_auth_failures_doubling_from_the_cadence_to_an_hourly_cap():
    client = FakeLife360([ok()])
    collector, _ = build(client)
    assert collector.sleep_seconds(CIRCLE) == CADENCE
    seen = []
    for failures in range(1, 8):
        client.auth_failures = failures
        seen.append(collector.sleep_seconds(CIRCLE))
    assert seen[:3] == [CADENCE * 2, CADENCE * 4, CADENCE * 8]
    assert seen == sorted(seen)
    assert max(seen) <= MAX_BACKOFF_SECONDS
    client.auth_failures = 12
    assert collector.sleep_seconds(CIRCLE) == MAX_BACKOFF_SECONDS
    client.auth_failures = 10**6  # days of a ban must not overflow float
    assert collector.sleep_seconds(CIRCLE) == MAX_BACKOFF_SECONDS


async def test_a_banned_circle_waits_longer_even_while_someone_is_driving():
    client = FakeLife360(
        [ok(member("m_alpha", T0, driving=True)), banned()], [PlacesRead(ReadStatus.OK)],
    )
    collector, _ = build(client, cadence_moving_seconds=30.0, cadence_still_seconds=600.0)
    await collector.collect_once(SPEC, now_ms=T0 + 1_000)
    assert collector.sleep_seconds(CIRCLE) == 30.0  # healthy and moving: the floor
    await collector.collect_once(SPEC, now_ms=T0 + 31_000)
    assert client.auth_failures == 1
    assert collector.sleep_seconds(CIRCLE) == CADENCE * 2  # ban safety beats freshness


async def test_a_banned_read_spends_no_second_call_and_writes_nothing():
    client = FakeLife360([banned()], [PlacesRead(ReadStatus.OK, (place("pl_1", "Place A"),))])
    health = InMemorySourceHealthLog()
    collector, store = build(client, health=health)
    read = await collector.collect_once(SPEC, now_ms=T0)
    assert read.status is ReadStatus.BANNED
    assert client.places_calls == 0  # no places call on a failed member read
    assert store.fixes == [] and store.place_writes == 0
    # one poll row, and not an `ok` one
    assert [(r.outcome, r.at_ms) for r in health.rows] == [("banned", T0)]


async def test_a_places_403_backs_off_instead_of_retrying_every_member_cycle():
    refused = PlacesRead(ReadStatus.BANNED, detail="places 403")
    client = FakeLife360([ok(member("m_alpha", T0))], [refused])
    collector, store = build(client)
    for i in range(6):  # six member cycles, 90 s apart
        await collector.collect_once(SPEC, now_ms=T0 + i * 90_000)
    # Tries at 0 s and 180 s (2 * cadence out); the next is gated to 540 s.
    # Hammering would have made one call per member cycle: six.
    assert client.places_calls == 2
    assert store.place_writes == 0


async def test_the_places_retry_backoff_is_capped_at_an_hour():
    refused = PlacesRead(ReadStatus.BANNED)
    client = FakeLife360([ok(member("m_alpha", T0))], [refused])
    collector, _ = build(client)
    now = T0
    for _ in range(20):
        await collector.collect_once(SPEC, now_ms=now)
        now += int(MAX_BACKOFF_SECONDS * 1000)
    calls = client.places_calls
    await collector.collect_once(SPEC, now_ms=now)
    assert client.places_calls == calls + 1  # still retried hourly, never wedged


# ── DEC-55 (3): the canary lives in the daemon ──────────────────────────────────


async def test_a_non_json_challenge_trips_the_non_json_canary():
    client = FakeLife360([CircleRead(ReadStatus.CHALLENGE, detail="html page")])
    metrics = FakeMetrics()
    collector, store = build(client, metrics=metrics)
    await collector.collect_once(SPEC, now_ms=T0)
    assert metrics.total("nora_presence_canary_total", reason="non_json") == 1
    assert metrics.total("nora_presence_canary_total", reason="empty_circle") == 0
    assert store.fixes == []


async def test_an_empty_circle_trips_the_canary_and_is_not_a_success():
    health = InMemorySourceHealthLog()
    metrics = FakeMetrics()
    client = FakeLife360([ok(member("m_alpha", T0)), ok()], [PlacesRead(ReadStatus.OK)])
    collector, _ = build(client, metrics=metrics, health=health)
    await collector.collect_once(SPEC, now_ms=T0)
    assert metrics.age() == 0.0
    await collector.collect_once(SPEC, now_ms=T0 + 300_000)
    assert metrics.total("nora_presence_canary_total", reason="empty_circle") == 1
    # The empty read must not re-stamp success: the age keeps climbing from the
    # last real one, or the heartbeat reads green during the very outage.
    assert metrics.age() == 300.0
    assert [r.outcome for r in health.rows] == ["ok", "empty_circle"]


async def test_a_normal_read_trips_no_canary():
    client = FakeLife360([ok(member("m_alpha", T0))], [PlacesRead(ReadStatus.OK)])
    metrics = FakeMetrics()
    collector, _ = build(client, metrics=metrics)
    await collector.collect_once(SPEC, now_ms=T0)
    assert metrics.total("nora_presence_canary_total") == 0


# ── DEC-55 (4): liveness is the daemon's ────────────────────────────────────────


async def test_the_heartbeat_climbs_while_up_but_never_collecting():
    client = FakeLife360([CircleRead(ReadStatus.CHALLENGE)])
    metrics = FakeMetrics()
    collector, _ = build(client, metrics=metrics)
    ages = []
    for i in range(3):  # never succeeds from boot: the age runs from first attempt
        await collector.collect_once(SPEC, now_ms=T0 + i * 120_000)
        ages.append(metrics.age())
    assert ages == [0.0, 120.0, 240.0]


# ── DEC-60: the hourly saved-place refresh ──────────────────────────────────────


async def test_saved_places_refresh_at_most_hourly_and_ride_the_member_cycle():
    client = FakeLife360(
        [ok(member("m_alpha", T0))],
        [PlacesRead(ReadStatus.OK, (place("pl_1", "Place A"), place("pl_2", "Place B")))],
    )
    collector, store = build(client)
    await collector.collect_once(SPEC, now_ms=T0)
    assert [p.name for p in store.places[CIRCLE]] == ["Place A", "Place B"]
    assert client.places_calls == 1
    just_before = T0 + int(PLACES_REFRESH_SECONDS * 1000) - 1
    for now in (T0 + 90_000, T0 + 1_800_000, just_before):
        await collector.collect_once(SPEC, now_ms=now)
    assert client.places_calls == 1  # static landmarks: one read per hour
    await collector.collect_once(SPEC, now_ms=T0 + int(PLACES_REFRESH_SECONDS * 1000))
    assert client.places_calls == 2
    assert store.place_writes == 2


async def test_a_failed_places_read_keeps_the_stored_places():
    refused = PlacesRead(ReadStatus.UNAVAILABLE)
    good = PlacesRead(ReadStatus.OK, (place("pl_1", "Place A"),))
    client = FakeLife360([ok(member("m_alpha", T0))], [good, refused])
    collector, store = build(client)
    await collector.collect_once(SPEC, now_ms=T0)
    await collector.collect_once(SPEC, now_ms=T0 + int(PLACES_REFRESH_SECONDS * 1000))
    assert client.places_calls == 2
    assert [p.name for p in store.places[CIRCLE]] == ["Place A"]  # not wiped
    assert store.place_writes == 1


async def test_a_store_failure_on_places_does_not_stop_the_cycle():
    class BrokenPlacesStore(FakeStore):
        async def replace_places(self, **_kw) -> None:
            raise RuntimeError("write refused")

    client = FakeLife360(
        [ok(member("m_alpha", T0))], [PlacesRead(ReadStatus.OK, (place("pl_1", "Place A"),))],
    )
    metrics = FakeMetrics()
    collector, store = build(client, store=BrokenPlacesStore(), metrics=metrics)
    await collector.collect_once(SPEC, now_ms=T0)
    assert [f.person_id for f in store.fixes] == ["p_alpha"]  # the fix still landed
    await collector.collect_once(SPEC, now_ms=T0 + 90_000)
    assert client.places_calls == 2  # not marked fetched: the next cycle retries


# ── DEC-67 / DEC-69: the on-demand live refresh ─────────────────────────────────


class Clock:
    """A fake wall clock that only moves when the collector sleeps."""

    def __init__(self) -> None:
        self.now = T0 / 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(collector_module, "time", SimpleNamespace(time=fake.time))
    monkeypatch.setattr(
        collector_module, "asyncio", SimpleNamespace(sleep=fake.sleep, wait_for=asyncio.wait_for),
    )
    return fake


async def seeded(client: FakeLife360, clock: Clock) -> tuple[PresenceCollector, FakeStore]:
    """A collector whose store already holds p_alpha's last fix at T0."""
    collector, store = build(client)
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE, person_id="p_alpha", ts=T0, lat=22.3, lng=114.17,
        wire_state=WireState.LIVE, recorded_at=T0,
    ))
    store.fixes.clear()
    return collector, store


async def test_a_refresh_pings_the_phone_then_records_the_fresher_fix(clock):
    fresher = T0 + 600_000
    client = FakeLife360([ok(member("m_alpha", fresher, lat=22.3050, lng=114.1750))])
    collector, store = await seeded(client, clock)
    landed = await collector.refresh_member(person_id="p_alpha", timeout_seconds=8.0)
    assert landed is True
    assert client.pings == [(CIRCLE, "m_alpha")]
    assert [(f.person_id, f.ts, f.lat) for f in store.fixes] == [("p_alpha", fresher, 22.3050)]


async def test_a_refresh_polls_until_a_newer_fix_lands_not_just_once(clock):
    stale = ok(member("m_alpha", T0))  # the phone has not answered yet
    fresh = ok(member("m_alpha", T0 + 600_000))
    client = FakeLife360([stale, stale, fresh])
    collector, store = await seeded(client, clock)
    assert await collector.refresh_member(person_id="p_alpha", timeout_seconds=8.0) is True
    assert client.circle_calls == 3
    assert len(store.fixes) == 1


async def test_a_refresh_for_an_unknown_person_does_nothing():
    client = FakeLife360([ok(member("m_alpha", T0))])
    collector, _ = build(client)
    assert await collector.refresh_member(person_id="p_nobody", timeout_seconds=8.0) is False
    assert client.pings == [] and client.circle_calls == 0


async def test_a_refresh_is_bounded_and_gives_up_when_nothing_fresher_arrives(clock):
    client = FakeLife360([ok(member("m_alpha", T0))])  # the stored fix, forever
    collector, store = await seeded(client, clock)
    landed = await collector.refresh_member(
        person_id="p_alpha", timeout_seconds=8.0, poll_interval=2.0,
    )
    assert landed is False
    assert store.fixes == []
    assert client.circle_calls <= 5  # 8 s at one read per 2 s, never a tight loop
    assert clock.sleeps and all(s == 2.0 for s in clock.sleeps)
    assert clock.now - T0 / 1000.0 <= 8.0


async def test_a_refresh_is_skipped_while_the_loop_is_backing_off(clock):
    client = FakeLife360([ok(member("m_alpha", T0 + 600_000))])
    collector, store = await seeded(client, clock)
    client.auth_failures = 1
    assert await collector.refresh_member(person_id="p_alpha", timeout_seconds=8.0) is False
    assert client.pings == [] and client.circle_calls == 0  # no unpaced reads at a ban
    assert store.fixes == []


async def test_a_refresh_stops_at_once_when_a_ban_starts_mid_poll(clock):
    client = FakeLife360([ok(member("m_alpha", T0)), banned(), ok(member("m_alpha", T0 + 1))])
    collector, store = await seeded(client, clock)
    assert await collector.refresh_member(person_id="p_alpha", timeout_seconds=8.0) is False
    assert client.circle_calls == 2  # the refused read ended it; no third read
    assert store.fixes == []
