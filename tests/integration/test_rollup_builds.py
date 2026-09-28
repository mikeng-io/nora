"""The Rollup on Postgres, end to end (design/cognition-loop/25-rollup.md §2-§4,
70-migration.md §2 row 2): the step-2 gate for every rollup, citation by
overlap across a rebuild, the pointer, and the stimuli with D3's rule.

One Record is seeded — two members' fixes and the collector's polls, their
unit's messages, replies, mentions, reactions and edits, Nora's sends, and
the Signal adapter's recorded health — with rows recorded AFTER the
watermark too, so a run that read past its watermark would show. Build A is
folded by the daemon's path (`RollupRunner.run`, tick after tick); build B
is one run straight to the watermark (a rebuild). Every row of every rollup
must agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import CODE_VERSION, RollupRunner, Subject
from nora.analytics.units import EventPlace, PersonScope, UnitConfig, UnitRollups
from nora.domain.models import mentions_json
from nora.domain.rollup import Citation
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.schema import (
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    ROLLUP_COLOCATION,
    ROLLUP_DAYBOOK,
    ROLLUP_OUTING,
    ROLLUP_RESPONSE,
    ROLLUP_TIE,
    SOURCE_HEALTH,
)
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.testing.stimuli import InMemoryStimulusStore

_HKT = ZoneInfo("Asia/Hong_Kong")
_MIN = 60_000
_H = 3_600_000
_START = int(datetime(2026, 9, 21, 0, 0, tzinfo=_HKT).timestamp() * 1000)
_WATERMARK = _START + 2 * 86_400_000 - 30 * _MIN  # day 2, 23:30 HKT

CFG = RollupConfig(
    tick_seconds=120, safety_lag=600, settled_seconds=900,
    max_age_seconds=86_400, batch_limit=200, backfill_batch_limit=20_000,
    backfill_threshold=4_000, grace_seconds=60, stay_radius_m=120,
    stay_min_dwell_seconds=600, cluster_eps_m=100, cluster_min_samples=2,
    min_nights=1, min_weekdays=1, label_freshness_days=45,
)
UNITS = UnitConfig(
    colocation_radius_m=150.0, colocation_min_overlap_seconds=600.0,
    response_window_seconds=3600.0, stimulus_horizon_seconds=3 * 3600.0,
    deviation_silence_days=1, health_max_poll_gap_seconds=900.0,
)
HOME = {"a": (22.4000, 114.1000), "b": (22.3500, 114.2000)}
PUB = (22.2800, 114.1500)
OFFICE = {"a": (22.3000, 114.1700), "b": (22.3100, 114.1900)}
ROOM = ("signal", "group-1")
UNIT = UnitRooms(
    unit="friends", rooms=(ROOM,), members=("a", "b"),
    accounts={("signal", "acct-a"): "a", ("signal", "acct-b"): "b"},
)
PEOPLE = {"a": PersonScope(subject="a", circles=("c1",)),
          "b": PersonScope(subject="b", circles=("c1",))}
SUBJECTS = (Subject("a", ("a",), ("c1",)), Subject("b", ("b",), ("c1",)))
TABLES = (ROLLUP_COLOCATION, ROLLUP_OUTING, ROLLUP_DAYBOOK, ROLLUP_TIE, ROLLUP_RESPONSE)


def _t(day: int, hour: float) -> int:
    return _START + day * 86_400_000 + int(hour * _H)


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms // 1000, tz=UTC) + timedelta(milliseconds=ms % 1000)


async def _seed(engine, mobility: SqlMobilityStore, *, polls_fail_at: int | None = None) -> None:
    fid = 1

    async def fix(person, ts, at, address=None):
        nonlocal fid
        await mobility._seed_fix_for_test(
            id=fid, person_id=person, ts=ts, lat=at[0], lng=at[1], accuracy_m=50.0,
            wire_state="live", recorded_at=ts + 5_000, address=address,
        )
        fid += 1

    async def rest(person, start, end, at, address):
        ts = start
        while ts <= end:
            await fix(person, ts, at, address)
            ts += 10 * _MIN

    async def move(person, start, a, b):
        for i in range(8):
            f = i / 7
            at = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            await fix(person, start + i * 30_000, at, f"{person} Road {i}")

    for day in range(3):  # the third day is after the watermark
        for p in ("a", "b"):
            # Nights at home and days at the office, so each has a place
            # labelled home (an outing needs every member's home known).
            await rest(p, _t(day, 0), _t(day, 7), HOME[p], f"{p} Home Road")
            await move(p, _t(day, 7.5), HOME[p], OFFICE[p])
            await rest(p, _t(day, 8), _t(day, 18), OFFICE[p], "Office Tower")
            await move(p, _t(day, 18.5), OFFICE[p], PUB)
            await rest(p, _t(day, 19), _t(day, 22), PUB, "Pub Street")
            await move(p, _t(day, 22.5), PUB, HOME[p])
            await rest(p, _t(day, 23), _t(day, 23.9), HOME[p], f"{p} Home Road")
    at = _START
    while at < _t(3, 0):
        outcome = "ok"
        if polls_fail_at is not None and polls_fail_at <= at < polls_fail_at + 2 * _H:
            outcome = "challenge"
        await mobility._seed_poll_for_test(at_ms=at, outcome=outcome)
        at += 5 * _MIN
    async with engine.begin() as conn:
        await conn.execute(sa.insert(SOURCE_HEALTH).values(
            source="signal", scope="", event="transition", outcome="up",
            at_ms=_START - _H, recorded_at=_ts(_START - _H),
        ))

        async def message(ts, *, person=None, nora=False, quoted=None, mentions=frozenset()):
            result = await conn.execute(sa.insert(MESSAGES).values(
                platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
                sender_id="nora" if nora else f"acct-{person}", person_id=person,
                ts=ts, body="…", is_nora=int(nora), delivery_status="sent",
                quoted_ts=quoted, mentions_json=mentions_json(frozenset(mentions)),
                created_at=_ts(ts + 1_000).isoformat(),
            ).returning(MESSAGES.c.id))
            return int(result.scalar_one())

        for day in range(3):
            m1 = await message(_t(day, 9), person="a")
            await message(_t(day, 9.1), person="b", quoted=_t(day, 9), mentions={"acct-a"})
            send = await message(_t(day, 10), nora=True, mentions={"acct-b"})
            reply = await message(_t(day, 10.2), person="b", quoted=_t(day, 10))
            await conn.execute(sa.insert(MESSAGE_REACTION).values(
                message_id=m1, sender_id="acct-b", person_id="b", emoji="👍",
                ts=_t(day, 9.2), created_at=_ts(_t(day, 9.2)).isoformat(),
            ))
            await conn.execute(sa.insert(MESSAGE_REACTION).values(
                message_id=send, sender_id="acct-a", person_id="a", emoji="❤",
                ts=_t(day, 10.1), created_at=_ts(_t(day, 10.1)).isoformat(),
            ))
            await conn.execute(sa.insert(MESSAGE_EDIT).values(
                message_id=reply, ts=_t(day, 10.3), body="…",
                recorded_at=_ts(_t(day, 10.3)),
            ))
            await message(_t(day, 12), nora=True, mentions={"acct-a"})  # nobody answers


def _runner(engine, stimuli=None, events=None) -> RollupRunner:
    rollups = SqlRollupStore(engine)
    return RollupRunner(
        mobility=SqlMobilityStore(engine), rollups=rollups, config=CFG,
        unit_rollups=UnitRollups(
            store=rollups, config=UNITS, units=(UNIT,), people=PEOPLE,
            stimuli=stimuli, events=events, clock=lambda: 0.0,
        ),
        subjects=SUBJECTS, unit_config=UNITS,
    )


async def _presence_rows(engine, build_id: int) -> dict:
    """Every presence row of a build by content (places named by content, so
    two builds with different ids compare)."""
    out = {}
    async with engine.connect() as conn:
        for table, cols in (
            ("presence_stays", "subject_id, started_ts, ended_ts, round(lat::numeric, 9), "
                               "round(lng::numeric, 9), fix_count, address, lineage"),
            ("presence_trips", "subject_id, started_ts, ended_ts, fix_count, sequence, lineage"),
            ("presence_derived_places", "subject_id, round(lat::numeric, 9), "
                                        "round(lng::numeric, 9), kind, label, first_seen_ts, "
                                        "last_seen_ts"),
        ):
            rows = (await conn.execute(sa.text(
                f"SELECT {cols} FROM {table} WHERE build_id = :b ORDER BY 1, 2, 3"  # noqa: S608 — test constants
            ), {"b": build_id})).all()
            out[table] = [tuple(r) for r in rows]
    return out


async def test_every_rollup_recomputes_identically_from_the_record(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    a = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    ticks = [*range(_START + 6 * _H, _WATERMARK, 47 * _MIN), _WATERMARK]
    for through in ticks:
        report = await runner.run(build_id=a, through_ms=through)
        assert not report.failed
    b = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=b, through_ms=_WATERMARK)

    for table in TABLES:
        rows_a = sorted(map(repr, await rollups.rows(table=table, build_id=a)))
        rows_b = sorted(map(repr, await rollups.rows(table=table, build_id=b)))
        assert rows_a, f"{table.name}: no rows — the fixture must produce every rollup"
        assert rows_a == rows_b, table.name
    presence = await _presence_rows(engine, b)
    assert await _presence_rows(engine, a) == presence
    # Nothing recorded after the watermark was folded (day 3 exists in the
    # Record): a run is a function of the Record UP TO its watermark.
    assert presence["presence_stays"] and max(r[2] for r in presence["presence_stays"]) < _WATERMARK

    # What the rows say, on this Record: they were together at the pub each
    # evening (an outing: away from both homes), each other's ties counted,
    # and the send naming someone that nobody answered is silent (the room
    # was proved up), the other responded.
    outs = await rollups.rows(table=ROLLUP_OUTING, build_id=b)
    assert len(outs) == 2 and all(o["persons"] == ["a", "b"] for o in outs)
    responses = {r["sent_ts"]: r for r in await rollups.rows(table=ROLLUP_RESPONSE, build_id=b)}
    assert responses[_t(0, 10)]["outcome"] == "responded"
    assert (responses[_t(0, 10)]["replies"], responses[_t(0, 10)]["reactions"],
            responses[_t(0, 10)]["edits"]) == (1, 1, 1)
    assert responses[_t(0, 12)]["outcome"] == "silent"
    assert _t(2, 10) not in responses  # recorded after the watermark: not read
    ties = await rollups.rows(table=ROLLUP_TIE, build_id=b)
    assert sum(t["replies_ba"] for t in ties) == 2 and sum(t["colocation_ms"] for t in ties) > 0
    books = await rollups.rows(table=ROLLUP_DAYBOOK, build_id=b)
    assert {d["local_date"] for d in books} == {"2026-09-21", "2026-09-22"}
    assert all(["message", None, "signal:group-1"] in d["lineage"] for d in books)
    await engine.dispose()


async def test_a_citation_resolves_by_overlap_after_a_rebuild_switches_the_pointer(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    first = await runner.rebuild(through_ms=_t(1, 12))
    assert (await rollups.current_build()).id == first
    outing = Citation("outing", "friends|a,b", _t(0, 20), _t(0, 20))
    stay = Citation("stay", "a", _t(0, 20), _t(0, 21))
    cited = {c: await rollups.resolve(c) for c in (outing, stay)}
    assert all(not r.unsupported for r in cited.values())

    second = await runner.rebuild(through_ms=_WATERMARK)
    current = await rollups.current_build()
    assert current.id == second != first and current.code_version == CODE_VERSION
    for citation, before in cited.items():
        after = await rollups.resolve(citation)
        # The rows were recomputed: new ids, the same happening.
        assert not after.unsupported and set(after.row_ids).isdisjoint(before.row_ids)
    assert (await rollups.resolve(Citation("stay", "a", _t(5, 0), _t(5, 1)))).unsupported
    # The superseded build keeps its rows.
    assert await rollups.rows(table=ROLLUP_OUTING, build_id=first)
    # ...and every unbound reader follows the pointer.
    stays = await mobility.stays_for_subject(subject_id="a")
    assert stays == await mobility.scoped(second).stays_for_subject(subject_id="a")
    await engine.dispose()


async def test_ensure_build_rebuilds_a_build_of_other_code_and_keeps_its_own(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    # A build the previous code made (on a deployment, migration 0071's
    # `legacy` build holding the wall-clock rows).
    await mobility._seed_build_for_test(current=True)
    legacy = await rollups.current_build()
    assert legacy is not None and legacy.code_version != CODE_VERSION
    built = await runner.ensure_build(through_ms=_t(0, 12))
    assert built != legacy.id
    assert await runner.ensure_build(through_ms=_t(0, 13)) == built
    async with engine.connect() as conn:
        status = (await conn.execute(sa.text(
            "SELECT status FROM rollup_build WHERE id = :i"), {"i": legacy.id})).scalar_one()
    assert status == "superseded"
    await engine.dispose()


async def _stimuli_at(engine, *, through: int, polls_fail_at: int | None = None,
                      events=None) -> InMemoryStimulusStore:
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility, polls_fail_at=polls_fail_at)
    sink = InMemoryStimulusStore()
    runner = _runner(engine, stimuli=sink, events=events)
    build = await runner.ensure_build(through_ms=_START)
    for t in range(_START + 6 * _H, through + 1, 30 * _MIN):
        await runner.run(build_id=build, through_ms=t)
    return sink


class _Pub:
    async def open_event_places(self, *, unit, at_ms):
        return [EventPlace(ref="event:7", lat=PUB[0], lng=PUB[1], radius_m=200.0)]


async def test_a_colocation_start_and_an_arrival_are_raised_once(pg_url):
    """Through the REAL stimulus store: its `(unit, ref)` conflict target must
    match migration 0071's partial index, which an in-memory sink never
    checks (the first measured run on the deployment's data failed here)."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine, stimuli=SqlStimulusStore(engine), events=_Pub())
    build = await runner.ensure_build(through_ms=_START)
    for t in range(_START + 6 * _H, _t(0, 21) + 1, 30 * _MIN):
        await runner.run(build_id=build, through_ms=t)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT unit, item, ref, kind FROM stimulus ORDER BY id"
        ))).all()
    items = [(r.unit, r.item, r.ref) for r in rows]
    assert all(r.kind == "rollup" for r in rows)
    starts = [i for i in items if i[1] == "colocation_start"]
    arrivals = [i for i in items if i[1] == "arrival"]
    assert len(starts) == 1 and starts[0][0] == "friends"
    assert len(arrivals) == 2  # each member's stay at the event's place
    await engine.dispose()


@pytest.mark.parametrize("outage", [False, True])
async def test_a_deviation_is_raised_only_on_healthy_sources(pg_url, outage):
    """D3: a silent day (nothing posted, never at home) raises a deviation
    only when the collector's polls were proved healthy for the span; a poll
    outage inside it suppresses the stimulus (R-27: missing data is not
    absence). Silence here is a synthetic third day with no fixes at home
    and no message, so the span is day 2."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility, polls_fail_at=_t(1, 3) if outage else None)
    # Day 1 without messages or home: delete them from the Record the unit reads.
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
        await conn.execute(sa.delete(MESSAGE_REACTION))
        await conn.execute(sa.delete(MESSAGE_EDIT))
        await conn.execute(sa.delete(MESSAGES).where(MESSAGES.c.ts >= _t(1, 0)))
        await conn.execute(sa.text(
            "DELETE FROM presence_fixes WHERE person_id = 'a' AND ts >= :a AND ts < :b"
        ), {"a": _t(1, 0), "b": _t(2, 0)})
    sink = InMemoryStimulusStore()
    runner = _runner(engine, stimuli=sink)
    build = await runner.ensure_build(through_ms=_START)
    await runner.run(build_id=build, through_ms=_t(2, 6))
    deviations = [s for s in sink.stimuli if s.item == "deviation_silence"]
    if outage:
        assert deviations == []
    else:
        assert [(s.person_id, s.ref) for s in deviations] == [
            ("a", f"deviation|silence|a|{_t(1, 0)}"),
        ]
    await engine.dispose()


async def test_a_run_with_nothing_new_rewrites_no_settled_row(pg_url):
    """Write amplification (measured on a copy of the deployment's data: the
    unit rollups rewrote EVERY row EVERY tick, ~45,000 row_history versions
    an hour, because each row's `as_of_ms` was the unit's clock). A row's
    `as_of_ms` is its own newest evidence, so a later run whose Record adds
    only polls changes nothing that has settled."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    build = await runner.rebuild(through_ms=_WATERMARK)
    count = (
        "SELECT count(*) FROM row_history WHERE table_name IN ("
        "'rollup_colocation', 'rollup_outing', 'rollup_daybook', 'rollup_tie', "
        "'rollup_response')"
    )
    async with engine.connect() as conn:
        before = (await conn.execute(sa.text(count))).scalar_one()
    settled = {
        t.name: sorted(map(repr, (
            r for r in await rollups.rows(table=t, build_id=build)
            if r["valid_to_ms"] < _t(1, 0)
        )))
        for t in TABLES
    }
    report = await runner.run(build_id=build, through_ms=_WATERMARK + 10 * _MIN)
    changed = sum(n for u in report.units for n in u.changes.values())
    # Only the day still running moves (each member's stay in progress grows):
    # at most one daybook per member, never the history.
    assert changed <= len(UNIT.members), report.units
    async with engine.connect() as conn:
        after = (await conn.execute(sa.text(count))).scalar_one()
    assert after - before == changed
    assert settled == {
        t.name: sorted(map(repr, (
            r for r in await rollups.rows(table=t, build_id=build)
            if r["valid_to_ms"] < _t(1, 0)
        )))
        for t in TABLES
    }
    assert any(settled.values())  # positive control: there were settled rows
    await engine.dispose()
