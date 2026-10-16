"""The daybook's `items_posted` on Postgres, through the build path: items and
polls seeded into the Record, `RollupRunner.run` writing the rows, the rows read
back. One unit, members `a`, `b` and `c`:

- `a` is observed from the 1st (a poll answered that day) and posts;
- `b` is observed too but has set `social = false`, so the store was built with
  them opted out;
- `c` has no account anywhere: nothing of theirs is polled or recorded.

Each member also sent one message on the 7th, so every one of them has a
daybook row that day whatever their items say.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import CODE_VERSION, RollupRunner, Subject
from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.schema import MESSAGES, ROLLUP_DAYBOOK, SOCIAL_ITEM, SOCIAL_POLL
from nora.infra.sql.social_stream import SqlSocialStreamStore

_HKT = ZoneInfo("Asia/Hong_Kong")
CFG = RollupConfig(
    tick_seconds=120, safety_lag=600, settled_seconds=900,
    max_age_seconds=86_400, batch_limit=200, backfill_batch_limit=20_000,
    backfill_threshold=4_000, grace_seconds=60, stay_radius_m=120,
    stay_min_dwell_seconds=600, cluster_eps_m=100, cluster_min_samples=2,
    min_nights=1, min_weekdays=1, label_freshness_days=45,
)
UNITS = UnitConfig()
ROOM = ("signal", "group-1")
UNIT = UnitRooms(
    unit="friends", rooms=(ROOM,), members=("a", "b", "c"),
    accounts={("signal", f"acct-{p}"): p for p in "abc"},
)
PEOPLE = {p: PersonScope(subject=p, circles=("c1",)) for p in "abc"}
SUBJECTS = tuple(Subject(p, (p,), ("c1",)) for p in "abc")


def _at(day: int, hour: float = 0, *, month: int = 10) -> datetime:
    """A moment on a Hong Kong calendar day of 2026."""
    return datetime(2026, month, day, tzinfo=_HKT).replace(
        hour=int(hour), minute=int(hour % 1 * 60),
    )


def _ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


# Watermarks: the first run reads to the 10th, a later one to the 12th.
FIRST = _ms(_at(10, 12))
LATER = _ms(_at(12, 12))


async def _item(engine, person, item_id, taken, *, kind="post", recorded=None, origin="poll"):
    taken_utc = taken.astimezone(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_ITEM).values(
            network="instagram", item_id=item_id, person=person, account=f"ig-{person}",
            kind=kind, taken_at=taken_utc, seen_at=taken_utc,
            expires_at=taken_utc + timedelta(days=1) if kind == "story_frame" else None,
            origin=origin, recorded_at=(recorded or taken).astimezone(UTC),
        ))


async def _poll(engine, person, recorded, *, outcome="unchanged"):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_POLL).values(
            network="instagram", person=person, stage="probe", outcome=outcome,
            recorded_at=recorded.astimezone(UTC),
        ))


async def _message(engine, person, taken):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES).values(
            platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
            sender_id=f"acct-{person}", person_id=person, ts=_ms(taken), body="…",
            is_nora=0, delivery_status="sent",
            created_at=taken.astimezone(UTC).isoformat(),
        ))


async def _seed(engine) -> None:
    """The Record every test starts from: members `a` and `b` observed since the
    1st, `c` never; each of the three wrote on the 7th."""
    for person in "ab":
        await _poll(engine, person, _at(1, 9))
    for person in "abc":
        await _message(engine, person, _at(7, 15))


def _runner(engine, *, opted_out=frozenset()) -> RollupRunner:
    rollups = SqlRollupStore(engine)
    return RollupRunner(
        mobility=SqlMobilityStore(engine), rollups=rollups, config=CFG,
        unit_rollups=UnitRollups(
            store=rollups, config=UNITS, units=(UNIT,), people=PEOPLE, clock=lambda: 0.0,
            social=SqlSocialStreamStore(engine, opted_out=opted_out),
        ),
        subjects=SUBJECTS, unit_config=UNITS,
    )


async def _build(engine, runner, through_ms: int) -> int:
    rollups = SqlRollupStore(engine)
    build = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    report = await runner.run(build_id=build, through_ms=through_ms, raise_stimuli=False)
    assert not report.failed
    return build


async def _posted(engine, build: int) -> dict[tuple[str, str], int | None]:
    """`(person, local date) -> items_posted` for every daybook row of a build."""
    rows = await SqlRollupStore(engine).rows(table=ROLLUP_DAYBOOK, build_id=build)
    return {(r["person_id"], r["local_date"]): r["items_posted"] for r in rows}


async def test_items_are_counted_on_the_local_day_they_were_taken(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    # 23:30 and 00:30 Hong Kong time are an hour apart and on two days; in UTC
    # they fall on the 5th (15:30) and the 5th (16:30), so a UTC day count
    # would put all three on one day.
    await _item(engine, "a", "p1", _at(5, 9), kind="post")
    await _item(engine, "a", "p2", _at(5, 23.5), kind="reel")
    await _item(engine, "a", "p3", _at(6, 0.5), kind="story_frame")
    posted = await _posted(engine, await _build(engine, _runner(engine), FIRST))
    assert posted[("a", "2026-10-05")] == 2
    assert posted[("a", "2026-10-06")] == 1
    await engine.dispose()


async def test_a_gone_or_hidden_item_is_not_counted(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    for item_id in ("kept", "gone", "hidden"):
        await _item(engine, "a", item_id, _at(5, 10))
    store = SqlSocialStreamStore(engine)
    await store.record_state(network="instagram", item_id="gone", state="gone")
    await store.record_state(network="instagram", item_id="hidden", state="hidden", reason="x")
    posted = await _posted(engine, await _build(engine, _runner(engine), FIRST))
    assert posted[("a", "2026-10-05")] == 1
    await engine.dispose()


async def test_an_opted_out_person_has_no_items_and_no_count(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    await _item(engine, "a", "mine", _at(5, 10))
    await _item(engine, "b", "theirs", _at(5, 10))
    await _item(engine, "b", "theirs-7", _at(7, 10))
    posted = await _posted(
        engine, await _build(engine, _runner(engine, opted_out=frozenset({"b"})), FIRST),
    )
    assert posted[("a", "2026-10-05")] == 1
    assert ("b", "2026-10-05") not in posted  # their item was the day's only input
    # A day `b` has a row for anyway: their polls exist, but opting out makes
    # them "not an input", never "looked, none".
    assert posted[("b", "2026-10-07")] is None
    await engine.dispose()


async def test_a_late_back_filled_item_lands_in_its_past_day(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    await _item(engine, "a", "fresh", _at(8, 10))
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    build = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=build, through_ms=FIRST, raise_stimuli=False)
    before = await _posted(engine, build)
    assert ("a", "2026-10-02") not in before and before[("a", "2026-10-07")] == 0
    # A back-fill page lands on the 11th with an item taken on the 2nd and one
    # taken on the 7th (a day the daybook already had a row for).
    await _item(engine, "a", "old", _at(2, 18), recorded=_at(11, 9), origin="backfill")
    await _item(engine, "a", "old-7", _at(7, 8), recorded=_at(11, 9), origin="backfill")
    await runner.run(build_id=build, through_ms=LATER, raise_stimuli=False)
    after = await _posted(engine, build)
    assert after[("a", "2026-10-02")] == 1
    assert after[("a", "2026-10-07")] == 1
    # The same watermark from the start of the Record reads the same rows.
    rebuilt = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=rebuilt, through_ms=LATER, raise_stimuli=False)
    assert await _posted(engine, rebuilt) == after
    await engine.dispose()


async def test_an_item_recorded_after_the_watermark_is_not_read(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    await _item(engine, "a", "late", _at(7, 8), recorded=_at(11, 9), origin="backfill")
    posted = await _posted(engine, await _build(engine, _runner(engine), FIRST))
    assert posted[("a", "2026-10-07")] == 0
    await engine.dispose()


async def test_none_is_not_an_input_and_zero_is_looked_and_none(pg_url):
    engine = create_sql_engine(pg_url)
    await _seed(engine)
    await _message(engine, "a", _at(30, 15, month=9))  # before a was first observed
    await _poll(engine, "c", _at(3, 9), outcome="error")  # no answer: not an observation
    await _item(engine, "a", "x", _at(5, 10))
    posted = await _posted(engine, await _build(engine, _runner(engine), FIRST))
    assert posted[("a", "2026-10-07")] == 0        # observed, no items
    assert posted[("a", "2026-09-30")] is None     # before the first answered poll
    assert posted[("a", "2026-10-05")] == 1
    assert posted[("c", "2026-10-07")] is None     # only a failed poll: never observed
    await engine.dispose()
