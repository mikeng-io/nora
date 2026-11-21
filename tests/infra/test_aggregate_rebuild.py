"""Compare every live aggregate column and its registry row across rebuilds.

The named exclusions per table are surrogate row/build ids and timestamps
stamped by the database at write time. Graph ids, semantic keys and
intervals, value columns, record-derived planes and registry state all
participate. Foreign surrogate ids are compared through the semantic ids
they resolve to. A saved-place head compares only live places, with an
explicit check that places missing from the last full snapshot are absent
on both sides. Earlier tombstones are history.
"""

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa

from nora.analytics.runner import CODE_VERSION
from nora.domain.observations import Observation, PollPayload, SourceScope
from nora.domain.presence import SavedPlace
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import METADATA, PRESENCE_CORRIDORS, PRESENCE_DERIVED_PLACES
from nora.memory.domain.registration import AGGREGATE_FAMILIES
from tests.infra.rollup_fixtures import RollupFixture

pytestmark = pytest.mark.asyncio(loop_scope="module")

_SYSTEM_COLUMNS = {
    "registry": {"registered_at", "tombstoned_at"},
    "presence_stays": {"id", "build_id", "asserted_at", "recorded_at"},
    "presence_trips": {"id", "build_id", "asserted_at", "recorded_at"},
    "presence_derived_places": {"id", "build_id", "asserted_at", "recorded_at"},
    "presence_occupancy": {"id", "build_id", "asserted_at", "recorded_at"},
    "presence_place_rhythm": {"build_id", "asserted_at", "recorded_at"},
    "presence_corridors": {"id", "build_id", "asserted_at", "recorded_at"},
    "rollup_colocation": {"id", "build_id", "asserted_at", "recorded_at"},
    "rollup_outing": {"id", "build_id", "asserted_at", "recorded_at"},
    "rollup_response": {"id", "build_id", "asserted_at", "recorded_at"},
    "rollup_tie": {"id", "build_id", "asserted_at", "recorded_at"},
    "rollup_daybook": {"id", "build_id", "asserted_at", "recorded_at"},
    "presence_places": set(),
    "platform_object_state": {"id", "asserted_at", "created_at", "updated_at"},
}
_REFERENCE_COLUMNS = {
    "presence_stays": {"place_id": PRESENCE_DERIVED_PLACES},
    "presence_trips": {"corridor_id": PRESENCE_CORRIDORS},
    "presence_occupancy": {"place_id": PRESENCE_DERIVED_PLACES},
    "presence_place_rhythm": {"place_id": PRESENCE_DERIVED_PLACES},
}

_MIN = 60_000
_HOUR = 60 * _MIN
_DAY = 24 * _HOUR
_START = 1_767_225_600_000 - 8 * _HOUR
_WATERMARK = _START + 4 * _DAY
_PEOPLE = ("generated-a", "generated-b")
_HOMES = ((22.40, 114.1), (22.35, 114.2))
_PUB = (22.28, 114.15)


def _stamp(row):
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


async def _snapshot(engine, table, *, build=None):
    filters = [table.c.state == "live"]
    if build is not None:
        filters.append(table.c.build_id == build)
    async with engine.connect() as conn:
        exclusions = _SYSTEM_COLUMNS[table.name]
        assert exclusions <= set(table.c.keys())
        sql = sa.select(table).where(*filters)
        rows = [dict(row) for row in (await conn.execute(sql)).mappings()]
        assert rows, f"{table.name} produced no live aggregate"
        for derived in rows:
            for name, target in _REFERENCE_COLUMNS.get(table.name, {}).items():
                if derived[name] is not None:
                    derived[name] = await conn.scalar(
                        sa.select(target.c.graph_id).where(
                            target.c.id == derived[name],
                        )
                    )
                    assert derived[name] is not None, (table.name, name)
        keys = {(row["graph_id"], row["unit"]) for row in rows}
        assert None not in {part for key in keys for part in key}, table.name
        assert len(keys) == len(rows), table.name
        refs = REGISTRY.c
        registry = (
            (
                await conn.execute(
                    sa.select(REGISTRY).where(
                        sa.tuple_(refs.id, refs.unit).in_(keys),
                    )
                )
            )
            .mappings()
            .all()
        )
    assert {(row["id"], row["unit"]) for row in registry} == keys
    assert all(row["kind"] == "aggregate" and row["state"] == "live" for row in registry)
    return (
        sorted(({k: v for k, v in row.items() if k not in exclusions} for row in rows), key=_stamp),
        sorted(
            ({k: v for k, v in row.items() if k not in _SYSTEM_COLUMNS["registry"]}
             for row in registry), key=_stamp,
        ),
    )


async def _rest(record, person, at, point, address):
    for i in range(7):
        await record.fix(person, at + i * 5 * _MIN, lat=point[0], lng=point[1], address=address)


async def _trip(record, person, at, origin, destination):
    for i in range(6):
        fraction = i / 5
        await record.fix(
            person,
            at + i * _MIN,
            lat=origin[0] + fraction * (destination[0] - origin[0]),
            lng=origin[1] + fraction * (destination[1] - origin[1]),
            address=f"Fixture road {i}",
            driving=True,
        )


async def _seed_rollup(record):
    store = PresenceStore(
        record.engine,
        registration=record.registration,
        aggregate_graph=SqlCognitiveGraph(record.engine),
    )
    await store.replace_places(
        circle_id=record.circle,
        observed_persons=_PEOPLE,
        places=[
            SavedPlace(record.circle, f"home-{i}", f"Home {i}", *point, _START, radius_m=80)
            for i, point in enumerate(_HOMES)
        ],
    )
    await record.health("signal", "", "transition", "up", _START - _MIN)
    for day in range(4):
        start = _START + day * _DAY
        for person, home in zip(_PEOPLE, _HOMES, strict=True):
            await _rest(record, person, start + _HOUR, home, f"{person} home")
            await _trip(record, person, start + 8 * _HOUR, home, _PUB)
            await _rest(record, person, start + 9 * _HOUR, _PUB, "Fixture pub")
            await _trip(record, person, start + 10 * _HOUR, _PUB, home)
            await _rest(record, person, start + 23 * _HOUR, home, f"{person} home")
        first = start + 11 * _HOUR
        await record.message("signal", first, person=_PEOPLE[0], body="Fixture conversation")
        await record.message("signal", first + _MIN, person=_PEOPLE[1], quoted_ts=first)
        sent = first + 2 * _MIN
        await record.message(
            "signal", sent, is_nora=1, body="Fixture question", delivery_status="sent"
        )
        await record.message("signal", sent + _MIN, person=_PEOPLE[0], quoted_ts=sent)
    await record.health("presence", record.circle, "poll", "ok", _WATERMARK)


@pytest.fixture(scope="module")
async def builds(pg_module_url):
    # One immutable record and three builds are shared by the read-only
    # interval/bucket cases within this module.
    engine = create_sql_engine(pg_module_url)
    record = RollupFixture(engine, people=_PEOPLE)
    try:
        await _seed_rollup(record)
        incremental = []
        for schedule in (
            (_START + 12 * _HOUR, _START + 2 * _DAY, _START + 3 * _DAY, _WATERMARK),
            (_START + _DAY + 11 * _HOUR, _START + 3 * _DAY + 5 * _HOUR, _WATERMARK),
        ):
            build = await record.store.create_build(
                code_version=CODE_VERSION, params_sha256=record.runner.params
            )
            for through in schedule:
                report = await record.runner.run(build_id=build, through_ms=through)
                assert report.failed == ()
            incremental.append(build)
        rebuilt = await record.runner.rebuild(through_ms=_WATERMARK, switch=False)
        yield engine, (*incremental, rebuilt)
    finally:
        await engine.dispose()


async def _reset_head(engine, table):
    async with engine.begin() as conn:
        keys = (
            await conn.execute(sa.delete(table).returning(table.c.graph_id, table.c.unit))
        ).all()
        await conn.execute(
            sa.delete(REGISTRY).where(sa.tuple_(REGISTRY.c.id, REGISTRY.c.unit).in_(keys))
        )


async def _platform_heads(record, table):
    store = SqlObservationStore(record.engine, registration=record.registration)
    scope = SourceScope("signal", "generated-adapter", dict(record.rooms)["signal"])
    newest = None
    for i, state in enumerate(("open", "closed", "withdrawn"), start=1):
        obs = Observation(
            "poll",
            scope,
            "generated-poll",
            "create" if i == 1 else "revise",
            PollPayload(options=(("a", f"Fixture choice {i}"),), state=state),
            occurred_ms=_START + i * _MIN,
            received_ms=_START + i * _MIN,
            time_quality="upstream",
            actor_id=_PEOPLE[i % 2],
        )
        newest = await store.append(
            observation=obs, event_id=f"event-{i}", source_digest=f"src-{i}"
        )
    assert newest is not None
    folded = await _snapshot(record.engine, table)
    await _reset_head(record.engine, table)
    await store.rebuild_heads(scope=scope, through_id=newest)
    return folded, await _snapshot(record.engine, table)


async def _saved_places(record, table):
    store = PresenceStore(
        record.engine,
        registration=record.registration,
        aggregate_graph=SqlCognitiveGraph(record.engine),
    )
    original = SavedPlace(
        record.circle, "kept", "Fixture old name", *_HOMES[0], _START, radius_m=80
    )
    removed = replace(original, place_id="removed")
    final = replace(original, name="Fixture current name", updated_ms=_START + _MIN)
    await store.replace_places(
        circle_id=record.circle, places=[original, removed], observed_persons=_PEOPLE
    )
    await store.replace_places(circle_id=record.circle, places=[final], observed_persons=_PEOPLE)
    folded = await _snapshot(record.engine, table)
    await _reset_head(record.engine, table)
    await store.replace_places(circle_id=record.circle, places=[final], observed_persons=_PEOPLE)
    rebuilt = await _snapshot(record.engine, table)
    assert {row["place_id"] for row in folded[0]} == {"kept"}
    assert {row["place_id"] for row in rebuilt[0]} == {"kept"}
    return folded, rebuilt


@pytest.fixture
def aggregate_db(request, family):
    fixture = "clean_pg_url" if AGGREGATE_FAMILIES[family] == "head" else "builds"
    return request.getfixturevalue(fixture)


@pytest.mark.parametrize(
    "family",
    [
        pytest.param(name, marks=pytest.mark.fresh_database) if kind == "head" else name
        for name, kind in AGGREGATE_FAMILIES.items()
    ],
)
async def test_each_aggregate_rebuilds_the_same_live_rows_and_registry(family, aggregate_db):
    table = METADATA.tables[family]
    if AGGREGATE_FAMILIES[family] == "head":
        # Each head family owns a fresh Record and registry.
        engine = create_sql_engine(aggregate_db)
        try:
            record = RollupFixture(engine, people=_PEOPLE)
            builders = {"platform_object_state": _platform_heads, "presence_places": _saved_places}
            incremental, rebuilt = await builders[family](record, table)
            assert incremental == rebuilt
        finally:
            await engine.dispose()
        return

    engine, (first, second, rebuild) = aggregate_db
    reference = await _snapshot(engine, table, build=rebuild)
    for build in (first, second):
        assert await _snapshot(engine, table, build=build) == reference
