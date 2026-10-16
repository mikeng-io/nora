"""Shared contract for the stimulus store. Every implementation runs every test.

Two implementations: `nora.infra.sql.stimuli.SqlStimulusStore` over real
Postgres, and `nora.testing.stimuli.InMemoryStimulusStore`. The evaluator's unit
tests run on the fake, so the fake must consume stimuli by exactly the rule
the SQL does, and this suite is what says it does.

**The property it is really about is the watermark.** A unit's watermark is
its newest wake row, never a row rewritten; a stimulus is consumed when its id
AND its pending-since time are both at or under that row's. The second bound
is what keeps a `self`/`time` stimulus due later pending across an earlier wake
(«pending only from its due time»).

The database is built ONCE for this module and truncated between tests
(AGENTS.md, «A contract suite scopes its database to the MODULE»).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.stimuli import NewStimulus, NewWake, WakeGuard
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_790_000_000_000
UNIT = "nora"


@pytest.fixture(scope="module")
def _stimulus_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_stimulus_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryStimulusStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_stimulus_db"))
    try:
        yield SqlStimulusStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "truncate stimulus, cognition_wake, stimulus_retraction restart identity"
            ))
        await engine.dispose()


def _msg(ref: str, *, unit: str = UNIT, cues: tuple[str, ...] = (), waits: bool = False,
         at: int = NOW) -> NewStimulus:
    return NewStimulus(
        unit=unit, kind="message", item="message", ref=ref, at_ms=at,
        platform="signal", conversation_id="g1", person_id="p1",
        cues=cues, waits=waits,
    )


def _wake(through: int, at: int, *, cut: bool = False) -> NewWake:
    return NewWake(
        unit=UNIT, mode="live", reason="settled", from_stimulus_id=0,
        through_stimulus_id=through, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=cut, at_ms=at, detail={"k": "v"},
    )


async def test_an_appended_stimulus_is_pending_with_its_fields(store):
    sid = await store.append(stimulus=_msg("messages:1", cues=("datetime", "keep"), waits=True),
                             recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW, limit=100)
    assert [s.id for s in view.pending] == [sid]
    got = view.pending[0]
    assert (got.kind, got.item, got.ref, got.cues, got.waits, got.person_id) == (
        "message", "message", "messages:1", ("datetime", "keep"), True, "p1",
    )
    assert got.recorded_at_ms == NOW and got.due_ms is None
    assert view.last_activity_ms == NOW
    assert view.last_wake is None


async def test_ids_increase_and_pending_is_ascending(store):
    ids = [await store.append(stimulus=_msg(f"messages:{i}"), recorded_at_ms=NOW + i)
           for i in range(3)]
    assert ids == sorted(ids) and len(set(ids)) == 3
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 10, limit=100)
    assert [s.id for s in view.pending] == ids


async def test_pending_is_scoped_to_the_unit(store):
    await store.append(stimulus=_msg("messages:1", unit="other"), recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW, limit=100)
    assert view.pending == () and view.last_activity_ms is None


async def test_a_wake_is_the_watermark_and_consumes_through_its_id(store):
    a = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(stimulus=_msg("messages:2"), recorded_at_ms=NOW + 1)
    await store.record_wake(wake=_wake(a, NOW + 5), recorded_at_ms=NOW + 5)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 10, limit=100)
    assert [s.id for s in view.pending] == [b]
    assert view.last_wake is not None
    assert (view.last_wake.through_stimulus_id, view.last_wake.at_ms) == (a, NOW + 5)
    assert view.last_wake.detail == {"k": "v"}


async def test_the_newest_wake_is_the_watermark(store):
    a = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(stimulus=_msg("messages:2"), recorded_at_ms=NOW + 1)
    await store.record_wake(wake=_wake(a, NOW + 5), recorded_at_ms=NOW + 5)
    await store.record_wake(wake=_wake(b, NOW + 9, cut=True), recorded_at_ms=NOW + 9)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 10, limit=100)
    assert view.pending == ()
    assert view.last_wake is not None and view.last_wake.cut_cooldown is True


async def test_a_stimulus_due_later_stays_pending_across_an_earlier_wake(store):
    """A self-wake set for 19:40 does not count at 15:00, and a wake at
    15:00 does not consume it, though its id is under the watermark."""
    due = NOW + 60_000
    later = await store.append(
        stimulus=NewStimulus(unit=UNIT, kind="self", item="self", ref="wake:1",
                         at_ms=NOW, due_ms=due),
        recorded_at_ms=NOW,
    )
    now_msg = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW + 1)
    early = await store.view(unit=UNIT, mode="live", now_ms=NOW + 10, limit=100)
    assert [s.id for s in early.pending] == [now_msg]          # not yet due
    await store.record_wake(wake=_wake(now_msg, NOW + 10), recorded_at_ms=NOW + 10)
    before_due = await store.view(unit=UNIT, mode="live", now_ms=due - 1, limit=100)
    assert before_due.pending == ()
    at_due = await store.view(unit=UNIT, mode="live", now_ms=due, limit=100)
    assert [s.id for s in at_due.pending] == [later]


async def test_a_time_stimulus_is_written_once_per_ref(store):
    sig = NewStimulus(unit=UNIT, kind="time", item="floor", ref="floor:07:00@2026-09-28",
                    at_ms=NOW, due_ms=NOW)
    first = await store.append(stimulus=sig, recorded_at_ms=NOW)
    again = await store.append(stimulus=sig, recorded_at_ms=NOW + 30_000)
    other_unit = await store.append(
        stimulus=NewStimulus(unit="other", kind="time", item="floor",
                         ref="floor:07:00@2026-09-28", at_ms=NOW, due_ms=NOW),
        recorded_at_ms=NOW)
    assert first is not None and again is None and other_unit is not None
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 60_000, limit=100)
    assert [s.id for s in view.pending] == [first]


async def test_a_rollup_stimulus_is_written_once_per_ref(store):
    """Migration 0071: a Rollup stimulus names the row that raised it, and a
    later run (or a rebuild recomputing the row) raises it again; it is on
    record once."""
    sig = NewStimulus(unit=UNIT, kind="rollup", item="colocation_start",
                      ref="colocation|a,b|1000", at_ms=NOW)
    first = await store.append(stimulus=sig, recorded_at_ms=NOW)
    again = await store.append(stimulus=sig, recorded_at_ms=NOW + 30_000)
    assert first is not None and again is None
    time_same_ref = await store.append(
        stimulus=NewStimulus(unit=UNIT, kind="time", item="floor",
                             ref="colocation|a,b|1000", at_ms=NOW, due_ms=NOW),
        recorded_at_ms=NOW)
    assert time_same_ref is not None  # the two kinds' refs are separate spaces


async def test_a_social_stimulus_is_written_once_per_ref(store):
    """Migration 0108: a `social` stimulus names the read it announces, and a
    perception pass that finds the read again (after a restart) writes nothing."""
    sig = NewStimulus(unit=UNIT, kind="social", item="story_frame",
                      ref="social_read:r1", at_ms=NOW, person_id="ada")
    first = await store.append(stimulus=sig, recorded_at_ms=NOW)
    again = await store.append(stimulus=sig, recorded_at_ms=NOW + 30_000)
    other_unit = await store.append(
        stimulus=NewStimulus(unit="other", kind="social", item="story_frame",
                             ref="social_read:r1", at_ms=NOW, person_id="ada"),
        recorded_at_ms=NOW)
    assert first is not None and again is None and other_unit is not None


async def test_message_refs_are_not_deduplicated(store):
    """Only `time`, `rollup` and `social` are unique on their ref: two
    reactions that share a message are two Record items, and both are stimuli."""
    a = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    assert a is not None and b is not None and a != b


async def test_the_limit_bounds_the_pending_read(store):
    for i in range(5):
        await store.append(stimulus=_msg(f"messages:{i}"), recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW, limit=2)
    assert len(view.pending) == 2


async def test_last_activity_counts_message_stimuli_only(store):
    await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    await store.append(
        stimulus=NewStimulus(unit=UNIT, kind="time", item="floor", ref="floor:x",
                         at_ms=NOW + 50, due_ms=NOW + 50),
        recorded_at_ms=NOW + 50,
    )
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 100, limit=100)
    assert view.last_activity_ms == NOW


async def test_a_due_later_wake_does_not_resurrect_earlier_consumed_stimuli(store):
    """The watermark only moves forward, on both stores: a wake whose only
    pending stimulus is an OLD id that just became due (a self-wake appended at
    15:00, due at 19:40) records a watermark no lower than the one it read,
    so the stimuli an earlier wake consumed stay consumed. The through id is
    `decide`'s, exactly as the evaluator records it."""
    from nora.domain.stimuli import WakePolicy, decide

    policy = WakePolicy(quiet_seconds=0, cooldown_seconds=0, session_seconds=0)
    due = NOW + 60_000
    await store.append(stimulus=NewStimulus(unit=UNIT, kind="self", item="self", ref="wake:1",
                                        at_ms=NOW, due_ms=due), recorded_at_ms=NOW)
    for i in (2, 3, 4):
        await store.append(stimulus=_msg(f"messages:{i}"), recorded_at_ms=NOW + i)
    for at in (NOW + 10, due + 10):
        view = await store.view(unit=UNIT, mode="live", now_ms=at, limit=100)
        verdict = decide(view, policy, at)
        assert verdict.reason is not None
        await store.record_wake(wake=NewWake(
            unit=UNIT, mode="live", reason=verdict.reason, from_stimulus_id=0,
            through_stimulus_id=verdict.through_stimulus_id,
            stimulus_count=verdict.stimulus_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms, cut_cooldown=False, at_ms=at,
        ), recorded_at_ms=at)
    after = await store.view(unit=UNIT, mode="live", now_ms=due + 10**6, limit=100)
    assert after.pending == ()
    assert after.last_wake is not None and after.last_wake.through_stimulus_id >= 4


async def test_a_stimulus_recorded_after_the_wakes_time_bound_stays_pending_under_its_id(store):
    """A late commit: two concurrent ingests, the LOWER id stamped later.
    A wake that read through the higher id with its time bound between the
    two stamps must leave the later-stamped one pending — the evaluator's
    settle margin relies on exactly this."""
    late = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW + 100)
    early = await store.append(stimulus=_msg("messages:2"), recorded_at_ms=NOW)
    await store.record_wake(wake=_wake(early, NOW + 50), recorded_at_ms=NOW + 50)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 200, limit=100)
    assert [s.id for s in view.pending] == [late]


# ---- D2: a wake claims a SET, so no stimulus is lost or taken twice ---------
#
# A `rollup`, `time` or `self` wake claims only its own kind (DEC-245). The
# messages it leaves inside its range are listed on the wake row
# (`unclaimed_ids`), and both stores must read them as pending exactly as the
# SQL does. Every test below names what reverting the rule would break.


def _kind(kind: str, ref: str, *, at: int = NOW, due: int | None = None) -> NewStimulus:
    return NewStimulus(
        unit=UNIT, kind=kind, item=kind, ref=ref, at_ms=at, due_ms=due,  # type: ignore[arg-type]
        person_id="p1" if kind == "rollup" else None,
    )


async def test_a_wake_that_names_unclaimed_stimuli_leaves_exactly_those_pending(store):
    m1 = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    r2 = await store.append(stimulus=_kind("rollup", "colocation|p1,p2|1"), recorded_at_ms=NOW)
    m3 = await store.append(stimulus=_msg("messages:3"), recorded_at_ms=NOW)
    wake = NewWake(
        unit=UNIT, mode="live", reason="rollup", from_stimulus_id=0,
        through_stimulus_id=r2, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=False, at_ms=NOW + 10,
        unclaimed_ids=(m1,),
    )
    await store.record_wake(wake=wake, recorded_at_ms=NOW + 10)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 20, limit=100)
    # the rollup is consumed; the message BELOW its id stays pending, and so
    # does the one above it (an ordinary id past the watermark)
    assert [s.id for s in view.pending] == [m1, m3]
    assert view.last_wake.unclaimed_ids == (m1,)
    assert view.watermark.unclaimed == frozenset({m1})


async def test_an_unclaimed_stimulus_is_taken_by_the_next_wake_and_only_once(store):
    m1 = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    r2 = await store.append(stimulus=_kind("rollup", "colocation|p1,p2|1"), recorded_at_ms=NOW)
    await store.record_wake(wake=NewWake(
        unit=UNIT, mode="live", reason="rollup", from_stimulus_id=0,
        through_stimulus_id=r2, stimulus_count=1, eligible_count=1, oldest_pending_ms=NOW,
        cut_cooldown=False, at_ms=NOW + 10, unclaimed_ids=(m1,),
    ), recorded_at_ms=NOW + 10)
    await store.record_wake(wake=_wake(r2, NOW + 20), recorded_at_ms=NOW + 20)  # claims all
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 30, limit=100)
    assert view.pending == ()
    assert view.watermark.unclaimed == frozenset()


async def test_a_retracted_unclaimed_stimulus_is_not_pending(store):
    m1 = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    r2 = await store.append(stimulus=_kind("rollup", "colocation|p1,p2|1"), recorded_at_ms=NOW)
    await store.record_wake(wake=NewWake(
        unit=UNIT, mode="live", reason="rollup", from_stimulus_id=0,
        through_stimulus_id=r2, stimulus_count=1, eligible_count=1, oldest_pending_ms=NOW,
        cut_cooldown=False, at_ms=NOW + 10, unclaimed_ids=(m1,),
    ), recorded_at_ms=NOW + 10)
    await store.retract(unit=UNIT, stimulus_id=m1, reason="failed", recorded_at_ms=NOW + 11)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 30, limit=100)
    assert view.pending == ()


@pytest.mark.parametrize("seed", range(8))
async def test_a_random_run_of_wakes_claims_every_stimulus_exactly_once(store, seed):
    """The load-bearing invariant of D2: drive `decide` and the store with a
    seeded stream of message, rollup, time and self stimuli and a tick between
    arrivals, record every wake with the claim `decide` made, and read the
    claimed sets back: together they are EVERY stimulus, each once. A wake that
    advanced the watermark over a message it did not claim would lose it; one
    that forgot to advance would take it twice."""
    import random

    from nora.domain.stimuli import WakePolicy, decide

    rng = random.Random(seed)
    policy = WakePolicy(
        quiet_seconds=60, cue_quiet_seconds=30, batch_max=1000,
        max_age_seconds=10**9, cooldown_seconds=0, session_seconds=0,
    )
    made: list[int] = []
    claimed: list[int] = []
    t = NOW

    async def arrive() -> None:
        kind = rng.choice(["message", "message", "rollup", "time", "self"])
        n = len(made) + 1
        if kind == "message":
            stim = _msg(f"messages:{n}", at=t)
        elif kind == "rollup":
            stim = _kind("rollup", f"colocation|p1,p2|{n}", at=t)
        elif kind == "time":
            stim = _kind("time", f"floor:{n}", at=t, due=t + rng.choice([0, 30_000, 600_000]))
        else:
            stim = _kind("self", f"ref{n}", at=t, due=t + rng.choice([0, 90_000, 900_000]))
        sid = await store.append(stimulus=stim, recorded_at_ms=t)
        assert sid is not None
        made.append(sid)

    async def tick() -> bool:
        view = await store.view(unit=UNIT, mode="live", now_ms=t, limit=1000)
        verdict = decide(view, policy, t)
        if verdict.reason is None:
            return False
        assert verdict.claimed_ids <= {s.id for s in view.pending}
        await store.record_wake(wake=NewWake(
            unit=UNIT, mode="live", reason=verdict.reason, from_stimulus_id=0,
            through_stimulus_id=verdict.through_stimulus_id,
            stimulus_count=verdict.stimulus_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms, cut_cooldown=False, at_ms=t,
            unclaimed_ids=verdict.unclaimed_ids,
        ), recorded_at_ms=t)
        claimed.extend(sorted(verdict.claimed_ids))
        return True

    for _ in range(40):
        for _ in range(rng.randint(0, 3)):
            await arrive()
        t += rng.randint(5, 400) * 1000
        await tick()
    for _ in range(60):                      # let everything become due and settle
        t += 3_000_000
        await tick()
    # A Rollup stimulus starts no wake (DEC-279): whatever is still pending now
    # is one riding along, and the next daily floor's wake takes it.
    left = await store.view(unit=UNIT, mode="live", now_ms=t, limit=1000)
    assert all(s.kind == "rollup" for s in left.pending)
    floor = await store.append(
        stimulus=_kind("time", "floor:last", at=t, due=t), recorded_at_ms=t,
    )
    assert floor is not None
    made.append(floor)
    t += 3_000_000
    await tick()
    final = await store.view(unit=UNIT, mode="live", now_ms=t, limit=1000)
    assert final.pending == ()
    assert sorted(claimed) == sorted(made)   # every stimulus, exactly once


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("limit", [3, 1000])
async def test_tracked_wakes_that_fail_expire_or_carry_still_claim_every_stimulus(
    store, seed, limit,
):
    """The same invariant under what production does to it: TRACKED wakes (each
    awaits an outcome), 30% of which FAIL and 10% of which EXPIRE (no outcome
    row: a kill), closes that CARRY stimuli back (waiting or not) and release
    them later, and a `limit` small enough to TRUNCATE the read. A failed or
    expired wake claims nothing (the watermark falls back over it); a closed one
    claims its set; so every stimulus is claimed by a closing wake or is still
    pending, and one claimed twice was carried back. Both stores run every seed;
    the SQL one is the one that has to hold."""
    import random

    from nora.domain.ports import NewCarry, NewWakeOutcome
    from nora.domain.stimuli import WakeGuard, WakePolicy, decide

    if hasattr(store, "_engine"):
        async with store._engine.begin() as conn:
            await conn.execute(sa.text(
                "truncate stimulus, cognition_wake, cognition_wake_outcome, cognition_carry, "
                "stimulus_retraction restart identity cascade"
            ))
    rng = random.Random(seed)
    policy = WakePolicy(
        quiet_seconds=60, cue_quiet_seconds=30, batch_max=1000,
        max_age_seconds=10**9, cooldown_seconds=0, session_seconds=0,
    )
    made: list[int] = []
    closed_claims: list[int] = []
    carried_ever: set[int] = set()
    t = NOW

    async def arrive() -> None:
        kind = rng.choice(["message", "message", "rollup", "time", "self"])
        n = len(made) + 1
        if kind == "message":
            stim = _msg(f"messages:{n}", at=t)
        elif kind == "rollup":
            stim = _kind("rollup", f"colocation|p1,p2|{n}", at=t)
        elif kind == "time":
            stim = _kind("time", f"floor:{n}", at=t, due=t + rng.choice([0, 30_000, 600_000]))
        else:
            stim = _kind("self", f"ref{n}", at=t, due=t + rng.choice([0, 90_000, 900_000]))
        sid = await store.append(stimulus=stim, recorded_at_ms=t)
        assert sid is not None
        made.append(sid)

    async def tick() -> None:
        view = await store.view(unit=UNIT, mode="live", now_ms=t, limit=limit,
                                stale_before_ms=t - 1)
        assert len(view.pending) <= limit                      # bounded, in both stores
        verdict = decide(view, policy, t)
        if verdict.reason is None:
            return
        assert verdict.claimed_ids <= {s.id for s in view.pending}
        wid = await store.record_wake(wake=NewWake(
            unit=UNIT, mode="live", reason=verdict.reason, from_stimulus_id=0,
            through_stimulus_id=verdict.through_stimulus_id,
            stimulus_count=verdict.stimulus_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms, cut_cooldown=False, at_ms=t,
            unclaimed_ids=verdict.unclaimed_ids, tracked=True,
        ), recorded_at_ms=t, guard=WakeGuard(
            last_wake_id=None if view.last_wake is None else view.last_wake.id,
            last_outcome=None if view.last_session is None else view.last_session.outcome,
        ))
        assert wid is not None
        roll = rng.random()
        if roll < 0.10:
            return                                              # expired: no outcome row
        if roll < 0.40:
            await store.record_outcome(outcome=NewWakeOutcome(
                wake_id=wid, unit=UNIT, outcome="failed", at_ms=t + 1), recorded_at_ms=t + 1)
            return
        carries = []
        for sid in sorted(verdict.claimed_ids):
            if rng.random() < 0.2:
                carried_ever.add(sid)
                carries.append(NewCarry(unit=UNIT, stimulus_id=sid, strike=1, state="carried",
                                        waits=rng.random() < 0.5))
            elif sid in view.carry:
                carries.append(NewCarry(unit=UNIT, stimulus_id=sid, strike=view.carry[sid],
                                        state="released"))
        await store.record_outcome(outcome=NewWakeOutcome(
            wake_id=wid, unit=UNIT, outcome="closed", at_ms=t + 1), carries=carries,
            recorded_at_ms=t + 1)
        closed_claims.extend(sorted(verdict.claimed_ids))

    for _ in range(40):
        for _ in range(rng.randint(0, 3)):
            await arrive()
        t += rng.randint(5, 400) * 1000
        await tick()
    for _ in range(300):
        t += 3_000_000
        await tick()
    final = await store.view(unit=UNIT, mode="live", now_ms=t, limit=1000, stale_before_ms=t)
    lost = sorted(set(made) - set(closed_claims) - {s.id for s in final.pending})
    twice = sorted({x for x in closed_claims if closed_claims.count(x) > 1} - carried_ever)
    assert (lost, twice) == ([], [])


@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_a_truncated_pending_read_is_the_same_bounded_oldest_first_list_in_both_stores(
    store, limit,
):
    """Store parity: the range read, the carried and the unclaimed all sit under
    ONE bound, oldest first, in both stores, so a truncated read can never hide
    an older stimulus behind a newer one (cutting unclaimed ids with `[:limit]`
    in one store while the other reads past the limit would break this)."""
    from nora.domain.ports import NewCarry, NewWakeOutcome

    ids = [await store.append(stimulus=_msg(f"messages:{i}"), recorded_at_ms=NOW)
           for i in range(1, 6)]
    r6 = await store.append(stimulus=_kind("rollup", "colocation|p1,p2|6"), recorded_at_ms=NOW)
    wid = await store.record_wake(wake=NewWake(
        unit=UNIT, mode="live", reason="rollup", from_stimulus_id=0,
        through_stimulus_id=r6, stimulus_count=1, eligible_count=1, oldest_pending_ms=NOW,
        cut_cooldown=False, at_ms=NOW + 10, unclaimed_ids=tuple(ids), tracked=True,
    ), recorded_at_ms=NOW + 10, guard=WakeGuard(last_wake_id=None, last_outcome=None))
    await store.record_outcome(
        outcome=NewWakeOutcome(wake_id=wid, unit=UNIT, outcome="closed", at_ms=NOW + 11),
        carries=[NewCarry(unit=UNIT, stimulus_id=r6, strike=1, state="carried")],
        recorded_at_ms=NOW + 11,
    )
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 30, limit=limit)
    assert [s.id for s in view.pending] == [*ids, r6][:limit]


async def test_a_wakes_detail_is_a_jsonb_object_and_an_old_string_row_still_reads(store):
    """`record_wake` writes `detail` as a jsonb OBJECT, so `detail->'unclaimed'`
    is queryable; a row whose detail is a jsonb string still reads, in both
    stores."""
    m1 = await store.append(stimulus=_msg("messages:1"), recorded_at_ms=NOW)
    r2 = await store.append(stimulus=_kind("rollup", "colocation|p1,p2|1"), recorded_at_ms=NOW)
    await store.record_wake(wake=NewWake(
        unit=UNIT, mode="live", reason="rollup", from_stimulus_id=0,
        through_stimulus_id=r2, stimulus_count=1, eligible_count=1, oldest_pending_ms=NOW,
        cut_cooldown=False, at_ms=NOW + 10, detail={"k": "v"}, unclaimed_ids=(m1,),
    ), recorded_at_ms=NOW + 10)
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 30, limit=100)
    assert view.watermark.unclaimed == frozenset({m1})
    assert [s.id for s in view.pending] == [m1]
    if not hasattr(store, "_engine"):
        return
    async with store._engine.begin() as conn:
        typed = (await conn.execute(sa.text(
            "SELECT jsonb_typeof(detail) AS d, jsonb_typeof(detail->'unclaimed') AS u "
            "FROM cognition_wake ORDER BY id DESC LIMIT 1"
        ))).one()
        assert (typed.d, typed.u) == ("object", "array")
        # A legacy row: a jsonb string holding the JSON.
        await conn.execute(sa.text(
            "UPDATE cognition_wake SET detail = to_jsonb(detail::text) "
            "WHERE id = (SELECT max(id) FROM cognition_wake)"
        ))
        old = (await conn.execute(sa.text(
            "SELECT jsonb_typeof(detail) AS d FROM cognition_wake ORDER BY id DESC LIMIT 1"
        ))).one()
        assert old.d == "string"
    view = await store.view(unit=UNIT, mode="live", now_ms=NOW + 30, limit=100)
    assert view.watermark.unclaimed == frozenset({m1})
    assert view.last_wake is not None and view.last_wake.detail == {"k": "v"}


async def test_count_rollup_counts_a_persons_deviations_in_a_window_retracted_included(store):
    def dev(ref: str, person: str, at: int, item: str = "deviation_silence") -> NewStimulus:
        return NewStimulus(
            unit=UNIT, kind="rollup", item=item, ref=ref, at_ms=at, person_id=person,
        )

    a = await store.append(
        stimulus=dev("deviation|silence|p1|1", "p1", NOW + 10), recorded_at_ms=NOW,
    )
    await store.append(stimulus=dev("deviation|anomaly|p1|2", "p1", NOW + 20, "deviation_anomaly"),
                       recorded_at_ms=NOW)
    await store.append(stimulus=dev("deviation|silence|p2|3", "p2", NOW + 10), recorded_at_ms=NOW)
    await store.append(stimulus=dev("colocation|p1,p2|4", "p1", NOW + 10, "colocation_start"),
                       recorded_at_ms=NOW)
    await store.append(stimulus=dev("deviation|silence|p1|5", "p1", NOW + 500), recorded_at_ms=NOW)
    await store.retract(unit=UNIT, stimulus_id=a, reason="failed", recorded_at_ms=NOW)

    async def count(person: str, lo: int, hi: int) -> int:
        return await store.count_rollup(
            unit=UNIT, person_id=person, item_prefix="deviation_", from_ms=lo, to_ms=hi,
        )

    assert await count("p1", NOW, NOW + 100) == 2          # both kinds; a struck one still counts
    assert await count("p1", NOW, NOW + 600) == 3          # the window is half-open on its end
    assert await count("p2", NOW, NOW + 100) == 1
    assert await count("p1", NOW + 21, NOW + 100) == 0
    assert await count("p9", NOW, NOW + 600) == 0
