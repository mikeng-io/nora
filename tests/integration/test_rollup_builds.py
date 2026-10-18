"""The Rollup on Postgres, end to end: the gate for every rollup, citation by
overlap across a rebuild, the pointer, and the stimuli.

One Record is seeded — two members' fixes and the collector's polls, their
unit's messages, replies, mentions, reactions and edits, Nora's sends, and
the Signal adapter's recorded health — with rows recorded AFTER the
watermark too, so a run that read past its watermark would show. Build A is
folded by the daemon's path (`RollupRunner.run`, tick after tick); build B
is one run straight to the watermark (a rebuild). Every row of every rollup
must agree.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import CODE_VERSION, RollupRunner, Subject
from nora.analytics.units import EventPlace, PersonScope, UnitConfig, UnitRollups
from nora.domain.mobility import TaggedPlace
from nora.domain.models import mentions_json
from nora.domain.ports import PlaceArea
from nora.domain.rollup import Citation, episode_key
from nora.infra.sql import memory_index
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PRESENCE_STAYS,
    ROLLUP_BUILD,
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

        async def message(ts, *, person=None, nora=False, quoted=None, mentions=frozenset(),
                          raw=None):
            result = await conn.execute(sa.insert(MESSAGES).values(
                platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
                sender_id="nora" if nora else f"acct-{person}", person_id=person,
                ts=ts, body="…", is_nora=int(nora), delivery_status="sent",
                quoted_ts=quoted, mentions_json=mentions_json(frozenset(mentions)),
                raw_json=raw, created_at=_ts(ts + 1_000).isoformat(),
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
            if day == 0:
                # Two sends naming nobody that nobody answers: only the one whose brief asked is
                # `silent`. Inside the day loop so their ids stay below the post-watermark rows.
                # The `ask` turn sends two parts, both carrying the intent as
                # the Deliverer writes it; only the turn's last part keeps it.
                await message(_t(0, 14), nora=True, raw='{"tag_id": "t", "intent": "ask"}')
                await message(_t(0, 14.5), nora=True, raw='{"tag_id": "t", "intent": "ask"}')
                await message(_t(0, 15), nora=True, raw='{"tag_id": "u"}')


def _runner(engine, stimuli=None, events=None, areas=None) -> RollupRunner:
    rollups = SqlRollupStore(engine)
    return RollupRunner(
        mobility=SqlMobilityStore(engine), rollups=rollups, config=CFG,
        unit_rollups=UnitRollups(
            store=rollups, config=UNITS, units=(UNIT,), people=PEOPLE,
            stimuli=stimuli, events=events, clock=lambda: 0.0, areas=areas,
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
    # The brief's intent rides on the send's own Record row (L0): `ask` expected an answer.
    # One expectation per turn: the last part of a two-part `ask` is silent, the first is not.
    assert (responses[_t(0, 14.5)]["intent"], responses[_t(0, 14.5)]["outcome"]) == (
        "ask", "silent",
    )
    assert (responses[_t(0, 14)]["intent"], responses[_t(0, 14)]["outcome"]) == (
        None, "unanswered",
    )
    assert (responses[_t(0, 15)]["intent"], responses[_t(0, 15)]["outcome"]) == (
        None, "unanswered",
    )
    assert _t(2, 10) not in responses  # recorded after the watermark: not read
    ties = await rollups.rows(table=ROLLUP_TIE, build_id=b)
    assert sum(t["replies_ba"] for t in ties) == 2 and sum(t["colocation_ms"] for t in ties) > 0
    books = await rollups.rows(table=ROLLUP_DAYBOOK, build_id=b)
    assert {d["local_date"] for d in books} == {"2026-09-21", "2026-09-22"}
    assert all(["message", None, "signal:group-1"] in d["lineage"] for d in books)
    await engine.dispose()


async def test_unquoted_answers_are_counted_as_follows_on_the_right_send(pg_url):
    """A member's message that quotes nothing answers her latest send in
    its room when it comes within 120 s and mentions no one else, or at any time
    inside the response window when it names her; a multi-part turn is answered
    at its last part; a quoted reply is a reply, counted once."""
    engine = create_sql_engine(pg_url)
    base = _START

    async with engine.begin() as conn:
        async def message(at_s, *, person=None, body="…", quoted=None, mentions=(), tag=None,
                          nora=False):
            await conn.execute(sa.insert(MESSAGES).values(
                platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
                sender_id="nora" if nora else f"acct-{person}", person_id=person,
                ts=base + at_s * 1000, body=body, is_nora=int(nora), delivery_status="sent",
                quoted_ts=None if quoted is None else base + quoted * 1000,
                mentions_json=mentions_json(frozenset(mentions)),
                raw_json=None if tag is None else f'{{"tag_id": "{tag}"}}',
                created_at=_ts(base + at_s * 1000 + 1_000).isoformat(),
            ))

        await message(0, nora=True, tag="t1")
        await message(30, person="a", body="sure")                      # 30 s: follows t1
        await message(1000, nora=True, tag="t2")
        await message(1300, person="b", body="sure")                    # 300 s, unnamed: not
        await message(2000, nora=True, tag="t3")
        await message(2300, person="a", body="ok NORA thanks")          # 300 s, names her
        await message(3000, nora=True, tag="t4")
        await message(3030, person="b", body="sure", mentions={"acct-a"})  # names another
        await message(4000, nora=True, tag="t5")
        await message(4050, nora=True, tag="t6")                        # a different turn
        await message(4060, person="a", body="sure")                    # answers t6 only
        await message(5000, nora=True, tag="t7")
        await message(5005, nora=True, tag="t7")                        # a two-part turn
        await message(5030, person="b", body="sure")                    # answers its last part
        await message(6000, nora=True, tag="t9")
        await message(6020, person="a", body="sure", quoted=6000)       # a quote: a reply
        await message(6500, person="b", body="anyone?")                 # 500 s: no send's follow
        await message(7000, nora=True, tag="t10")
        await message(7030, person="a", body="yes", quoted=6500)        # a reply to b: not a follow
        await message(8000, nora=True, tag="t11")
        await message(8030, person="a", body="thanks nora", quoted=6500)  # quotes b, names her
        await message(9000, nora=True, tag="t12")
        await message(9500, person="b", body="hi", mentions={"nora"})   # 500 s, her account
        await message(4 * 3600, person="b", body="later")               # the Record's clock

    rollups = SqlRollupStore(engine)
    unit_rollups = UnitRollups(
        store=rollups, config=UNITS, units=(UNIT,), people=PEOPLE, clock=lambda: 0.0,
    )
    build = await rollups.create_build(code_version=CODE_VERSION, params_sha256="x")
    mark = await rollups.mark(through_ms=base + 5 * _H)
    await unit_rollups.run(
        build_id=build, mark=mark, logical_now={}, raise_stimuli=False,
    )
    rows = {
        (r["sent_ts"] - base) // 1000: r
        for r in await rollups.rows(table=ROLLUP_RESPONSE, build_id=build)
    }
    got = {at: (r["outcome"], r["replies"], r["follows"], r["responders"])
           for at, r in rows.items()}
    assert got == {
        0: ("responded", 0, 1, ["a"]),
        1000: ("unanswered", 0, 0, []),
        2000: ("responded", 0, 1, ["a"]),
        3000: ("unanswered", 0, 0, []),
        4000: ("unanswered", 0, 0, []),
        4050: ("responded", 0, 1, ["a"]),
        5000: ("unanswered", 0, 0, []),
        5005: ("responded", 0, 1, ["b"]),
        6000: ("responded", 1, 0, ["a"]),
        7000: ("unanswered", 0, 0, []),
        8000: ("responded", 0, 1, ["a"]),
        9000: ("responded", 0, 1, ["b"]),
    }
    await engine.dispose()


async def test_a_forgotten_persons_message_is_no_longer_a_reply_target_in_the_tie_rollup(pg_url):
    """NEGATIVE: `message_facts`'s `quoted` join must filter on `hidden_at`, so
    a `nora person forget` (removed)-hidden message a reply quotes does not
    count as a reply relationship feeding `rollup_tie` (and, on the main select,
    a hidden message's own reactions/mentions do not feed
    `rollup_daybook`/`rollup_response`). Build once as seeded — b's reply to a's
    day-0 and day-1 message registers twice, the same positive control
    `test_every_rollup_recomputes_identically_from_the_record` pins — hide every
    message person "a" sent, and rebuild fresh: the replies that quoted them
    must be GONE from the tie rollup, not merely relabelled."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)

    before = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=before, through_ms=_WATERMARK)
    ties_before = await rollups.rows(table=ROLLUP_TIE, build_id=before)
    books_before = await rollups.rows(table=ROLLUP_DAYBOOK, build_id=before)
    assert sum(t["replies_ba"] for t in ties_before) == 2  # positive control
    assert sum(d["messages"] for d in books_before if d["person_id"] == "a") > 0
    # b's only reaction is on a's message (m1): a positive control for the
    # reaction-target `hidden_at` filter too.
    assert sum(d["reactions"] for d in books_before if d["person_id"] == "b") > 0

    async with engine.begin() as conn:
        await conn.execute(
            sa.update(MESSAGES).where(MESSAGES.c.person_id == "a").values(hidden_at=1)
        )

    after = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=after, through_ms=_WATERMARK)
    ties_after = await rollups.rows(table=ROLLUP_TIE, build_id=after)
    books_after = await rollups.rows(table=ROLLUP_DAYBOOK, build_id=after)
    assert sum(t["replies_ba"] for t in ties_after) == 0
    assert sum(d["messages"] for d in books_after if d["person_id"] == "a") == 0
    assert sum(d["reactions"] for d in books_after if d["person_id"] == "b") == 0
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
    checks."""
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
    """A silent day (nothing posted, never at home) raises a deviation
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
    """Write amplification: if each row's `as_of_ms` were the unit's clock, the
    unit rollups would rewrite EVERY row EVERY tick, bloating row_history. A
    row's `as_of_ms` is its own newest evidence, so a later run whose Record
    adds only polls changes nothing that has settled."""
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


# --- the memory index's episodes ----------------------------------------

_SAVED_NAME = "Hidden Den"  # a saved place's name: its existence is the secret (R-35)


async def _episode_nodes(engine, kind: str) -> dict[str, dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(MEMORY_NODE).where(MEMORY_NODE.c.kind == kind)
        )).mappings().all()
    return {r["source_id"]: dict(r) for r in rows}


async def _edges(engine, **where) -> set[tuple]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ).where(*(getattr(MEMORY_EDGE.c, k) == v for k, v in where.items())))).all()
    return {tuple(r) for r in rows}


def _outing_keys(rows) -> set[str]:
    return {
        episode_key(f"{r['unit']}|{','.join(r['persons'])}", r["valid_from_ms"], r["valid_to_ms"])
        for r in rows
    }


async def _seed_with_outing_talk(engine, mobility) -> int:
    """The shared Record, a saved place where the pub is, and one message by
    a at the pub, inserted FIRST so its id sits below every post-watermark
    row (`mark` takes the newest id recorded in time)."""
    async with engine.begin() as conn:
        talk = (await conn.execute(sa.insert(MESSAGES).values(
            platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
            sender_id="acct-a", person_id="a", ts=_t(0, 20), body="…", is_nora=0,
            delivery_status="sent", created_at=_ts(_t(0, 20) + 1_000).isoformat(),
        ).returning(MESSAGES.c.id))).scalar_one()
    await mobility._seed_tagged_place_for_test(TaggedPlace(
        circle_id="c1", place_id="p-1", name=_SAVED_NAME, lat=PUB[0], lng=PUB[1], radius_m=200.0,
    ))
    await _seed(engine, mobility)
    return int(talk)


async def test_a_switch_replaces_the_episode_nodes_and_keeps_what_cites_them(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    talk = await _seed_with_outing_talk(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    first = await runner.rebuild(through_ms=_t(1, 12))

    outings = await _episode_nodes(engine, "outing")
    first_rows = await rollups.rows(table=ROLLUP_OUTING, build_id=first)
    assert set(outings) == _outing_keys(first_rows) and len(outings) == 1
    [(key, node)] = outings.items()
    # The narrowest scope the row supports: its unit AND both its people, with
    # the presence lineage a reader must pass (R-31).
    assert (node["unit"], node["origin_room"], node["persons"]) == ("friends", None, ["a", "b"])
    assert node["lineage"] == [["presence", "a", None], ["presence", "b", None]]
    # Positive control: the row does name the saved place, so the text could leak it.
    assert first_rows[0]["place_label"] == _SAVED_NAME and first_rows[0]["saved_place_id"]
    texts = [
        n["text"] for kind in ("outing", "stay", "trip")
        for n in (await _episode_nodes(engine, kind)).values()
    ]
    assert texts
    for text in texts:
        assert _SAVED_NAME not in text and "Street" not in text and "Road" not in text
        assert str(PUB[0]) not in text and str(HOME["a"][0]) not in text
    # Nor in a key: a node's source_id and an edge's ends are read as text too.
    async with engine.connect() as conn:
        ids = [*(await conn.execute(sa.select(MEMORY_NODE.c.source_id))).scalars(),
               *(await conn.execute(sa.select(MEMORY_EDGE.c.src_id))).scalars(),
               *(await conn.execute(sa.select(MEMORY_EDGE.c.dst_id))).scalars()]
    assert ids
    for ident in ids:
        assert _SAVED_NAME not in ident and "Street" not in ident and "Road" not in ident
        assert str(PUB[0]) not in ident and str(HOME["a"][0]) not in ident
    assert await _edges(engine, rel="during") == {("message", str(talk), "during", "outing", key)}
    assert await _edges(engine, src_kind="outing") == {
        ("outing", key, "about", "person", "a"), ("outing", key, "about", "person", "b"),
    }

    stays = await _episode_nodes(engine, "stay")
    assert stays and all(
        (n["unit"], n["origin_room"], n["persons"], n["lineage"])
        == (None, None, [k.split("|")[0]], [["presence", k.split("|")[0], None]])
        for k, n in stays.items()
    ), "a stay is its person's alone: no unit, no room"
    assert await _episode_nodes(engine, "trip")

    # A memo cites the outing by the key `rollup_read` showed it under.
    memo = await SqlMemoStore(engine).create(
        unit="friends", platform=ROOM[0], conversation_id=ROOM[1], title="pub night",
        body="…", citations=[f"rollup(outing|{key})"], mode="told",
        established_by="a", model=None, now_ms=_t(1, 12),
    )
    cites = ("memo", str(memo.id), "cites", "outing", key)
    assert cites in await _edges(engine, rel="cites")

    second = await runner.rebuild(through_ms=_WATERMARK)
    second_rows = await rollups.rows(table=ROLLUP_OUTING, build_id=second)
    assert set(await _episode_nodes(engine, "outing")) == _outing_keys(second_rows)
    assert len(second_rows) == 2
    async with engine.connect() as conn:
        stays_now = (await conn.execute(sa.select(sa.func.count()).select_from(
            sa.table("presence_stays", sa.column("build_id"))
        ).where(sa.column("build_id") == second))).scalar_one()
    assert len(await _episode_nodes(engine, "stay")) == stays_now
    # The same happening recomputed keeps its key, so the memo still reaches it.
    assert key in await _episode_nodes(engine, "outing")
    assert cites in await _edges(engine, rel="cites")
    await engine.dispose()


async def test_a_build_still_building_has_no_episode_nodes(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    building = await rollups.create_build(code_version=CODE_VERSION, params_sha256=runner.params)
    await runner.run(build_id=building, through_ms=_WATERMARK)
    assert await rollups.rows(table=ROLLUP_OUTING, build_id=building)  # positive control
    for kind in ("outing", "stay", "trip"):
        assert await _episode_nodes(engine, kind) == {}, kind
    await rollups.switch_build(build_id=building)
    for kind in ("outing", "stay", "trip"):
        assert await _episode_nodes(engine, kind), f"{kind}: the switch projects the build"
    await engine.dispose()


async def test_the_delete_half_of_the_diff_drops_the_outing_node(pg_url):
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed_with_outing_talk(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    build = await runner.rebuild(through_ms=_WATERMARK)
    assert len(await _episode_nodes(engine, "outing")) == 2
    assert await _edges(engine, rel="during")
    await rollups.apply(
        build_id=build, unit="friends", colocations=[], outings=[], daybooks=[],
        ties=[], responses=[],
    )
    assert await _episode_nodes(engine, "outing") == {}
    assert await _edges(engine, rel="during") == set()
    assert await _episode_nodes(engine, "stay")  # the person-scoped kinds are not the unit's
    await engine.dispose()


async def test_a_tick_on_the_current_build_projects_what_it_folds(pg_url):
    """The daemon's path, not a switch: each run on the current build adds its
    new stays, trips and outings to the index as `commit_fold` and `apply`
    write them."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)
    rollups = SqlRollupStore(engine)
    build = await runner.rebuild(through_ms=_t(1, 12))
    before = {k: set(await _episode_nodes(engine, k)) for k in ("outing", "stay", "trip")}
    async with engine.begin() as conn:  # a projection some process queued
        await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE).values(
            kind="stay", source_id="a|gone", last_error="RuntimeError",
        ))
    await runner.run(build_id=build, through_ms=_WATERMARK)
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
        )).scalar_one() == 0, "the run drained the queue"
    async with engine.connect() as conn:
        held = {
            kind: {
                episode_key(r.subject_id, r.started_ts, r.ended_ts)
                for r in await conn.execute(sa.text(
                    f"SELECT subject_id, started_ts, ended_ts FROM {table} "  # noqa: S608 — test constants
                    "WHERE build_id = :b"
                ), {"b": build})
            }
            for kind, table in (("stay", "presence_stays"), ("trip", "presence_trips"))
        }
    for kind in ("stay", "trip"):
        now = set(await _episode_nodes(engine, kind))
        assert now == held[kind] and now > before[kind], kind
    outings = set(await _episode_nodes(engine, "outing"))
    assert outings == _outing_keys(await rollups.rows(table=ROLLUP_OUTING, build_id=build))
    assert len(outings) == 2 and len(before["outing"]) == 1
    await engine.dispose()


async def test_a_drain_waiting_on_a_switch_keeps_the_new_builds_episode(pg_url):
    """A drain that blocks on the build row while a switch commits must read
    the NEW current build after it, never «no build»: under READ COMMITTED the
    locked re-check sees only the old row, now superseded, and acting on it
    would drop the node the switch had just projected and clear its queue row."""
    engine = create_sql_engine(pg_url)
    key = episode_key("a", _t(0, 19), _t(0, 20))
    async with engine.begin() as conn:
        old = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
            code_version="t", params_sha256="", status="current",
        ).returning(ROLLUP_BUILD.c.id))).scalar_one()
        new = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
            code_version="t", params_sha256="", status="building",
        ).returning(ROLLUP_BUILD.c.id))).scalar_one()
        await conn.execute(sa.insert(PRESENCE_STAYS).values(
            subject_id="a", person_id="a", circle_id="c1", started_ts=_t(0, 19),
            ended_ts=_t(0, 20), lat=0.0, lng=0.0, radius_m=10.0, fix_count=3,
            build_id=new, lineage=[],
        ))
        await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE).values(
            kind="stay", source_id=key, last_error="RuntimeError",
        ))
    rollups = SqlRollupStore(engine)
    # `switch_build`'s statements, held open so the drain meets them mid-switch.
    switching = await engine.connect()
    tx = await switching.begin()
    await switching.execute(
        sa.update(ROLLUP_BUILD).where(ROLLUP_BUILD.c.id == old).values(status="superseded")
    )
    await switching.execute(
        sa.update(ROLLUP_BUILD).where(ROLLUP_BUILD.c.id == new).values(status="current")
    )
    await memory_index.reproject_episodes(switching)
    drain = asyncio.create_task(rollups.drain_index_queue())
    await asyncio.sleep(1.0)
    assert not drain.done(), "the drain waits on the build row the switch holds"
    await tx.commit()
    await switching.close()
    await drain
    assert key in await _episode_nodes(engine, "stay")
    await engine.dispose()


async def test_a_switch_that_fails_abandons_the_build(pg_url, monkeypatch):
    """A rebuild whose switch raises leaves no build `building` forever: the
    switch rolled back whole, and the build is abandoned."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    runner = _runner(engine)

    async def _refused(*, build_id: int) -> None:
        raise RuntimeError("switch refused")

    monkeypatch.setattr(runner._rollups, "switch_build", _refused)
    with pytest.raises(RuntimeError, match="switch refused"):
        await runner.rebuild(through_ms=_t(1, 12))
    async with engine.connect() as conn:
        statuses = (await conn.execute(sa.select(ROLLUP_BUILD.c.status))).scalars().all()
    assert statuses == ["abandoned"]
    await engine.dispose()


_MK = "district:旺角 / Mong Kok"
_KLN = "city:九龍 / Kowloon"


async def _cache_an_area_at_the_pub(engine) -> SqlGeocodeCacheStore:
    cache = SqlGeocodeCacheStore(engine)
    await cache.put(
        lat_rounded=PUB[0], lng_rounded=PUB[1], address="Pub Street",
        area=PlaceArea(district="旺角 / Mong Kok", city="九龍 / Kowloon"),
    )
    return cache


async def test_an_outing_is_located_in_the_cached_area_of_its_place(pg_url):
    """Step 3a: an outing at no saved place takes the district and city of
    the nearest cached geocode, the cache its place labels come from, and the
    index places it there: outing -> district -> city. The area is a public
    name and never a street (the cached address is not copied)."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed(engine, mobility)
    cache = await _cache_an_area_at_the_pub(engine)
    build = await _runner(engine, areas=cache).rebuild(through_ms=_WATERMARK)
    rows = await SqlRollupStore(engine).rows(table=ROLLUP_OUTING, build_id=build)
    assert rows and all(r["saved_place_id"] is None for r in rows)
    assert {(r["district"], r["city"]) for r in rows} == {("旺角 / Mong Kok", "九龍 / Kowloon")}
    keys = _outing_keys(rows)
    assert await _edges(engine, rel="located_in") == {
        *(("outing", k, "located_in", "place", _MK) for k in keys),
        ("place", _MK, "located_in", "place", _KLN),
    }
    async with engine.connect() as conn:
        texts = set((await conn.execute(
            sa.select(MEMORY_NODE.c.text).where(MEMORY_NODE.c.kind == "place")
        )).scalars())
    assert texts == {"旺角 / Mong Kok", "九龍 / Kowloon"}
    live = await _edges(engine, rel="located_in")
    async with engine.begin() as conn:
        await conn.execute(sa.delete(MEMORY_EDGE))
        await conn.execute(sa.delete(MEMORY_NODE))
    await memory_index.rebuild(engine)
    assert await _edges(engine, rel="located_in") == live
    await engine.dispose()


async def test_an_outing_at_a_saved_place_has_no_area_even_where_one_is_cached(pg_url):
    """R-35: a saved place's existence is the secret, so an outing there is
    never placed, though the cache holds an area within reach (the positive
    control) and the same outing elsewhere would be."""
    engine = create_sql_engine(pg_url)
    mobility = SqlMobilityStore(engine)
    await _seed_with_outing_talk(engine, mobility)
    cache = await _cache_an_area_at_the_pub(engine)
    assert await cache.area_within(lat=PUB[0], lng=PUB[1], radius_m=UNITS.colocation_radius_m)
    build = await _runner(engine, areas=cache).rebuild(through_ms=_WATERMARK)
    rows = await SqlRollupStore(engine).rows(table=ROLLUP_OUTING, build_id=build)
    assert rows and all(r["saved_place_id"] for r in rows)
    assert {(r["district"], r["city"]) for r in rows} == {(None, None)}
    assert await _edges(engine, rel="located_in") == set()
    assert await _episode_nodes(engine, "outing")
    await engine.dispose()
