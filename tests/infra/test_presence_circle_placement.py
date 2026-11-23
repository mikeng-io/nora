"""A presence aggregate reaches only the units its inputs' circles cover."""

import pytest
import sqlalchemy as sa

from nora.domain.mobility import Stay, Trip
from nora.domain.rollup import FoldState
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.schema import (
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_OCCUPANCY,
    PRESENCE_PLACE_RHYTHM,
)
from nora.memory.adapters.stored_row_registration import SqlStoredRows
from nora.memory.app.register_stored_rows import RegisterStoredRows
from nora.persons import Life360Membership, Persons
from nora.policy import Conversation, Policy
from tests.infra.test_aggregate_registration import _FixedRevisions

pytestmark = pytest.mark.fresh_database

_PERSON, _FIRST, _SECOND = "test-person", "test-circle-first", "test-circle-second"
_START, _END = 1_795_000_000_000, 1_795_000_005_000
_PLACED = (PRESENCE_DERIVED_PLACES, PRESENCE_OCCUPANCY, PRESENCE_PLACE_RHYTHM)


def _registration() -> SqlItemRegistration:
    # Each circle holds the person and one unit's peer, so it covers exactly that unit.
    rooms = (("test-primary", "test-peer-a"), ("test-affiliate", "test-peer-b"))
    policy = Policy(tuple(
        Conversation("signal", frozenset({f"room-{unit}"}), frozenset({_PERSON, peer}), unit=unit)
        for unit, peer in rooms
    ))
    life360 = tuple(
        Life360Membership(person, f"member-{person}-{circle}", circle)
        for person, circle in (
            (_PERSON, _FIRST), ("test-peer-a", _FIRST),
            (_PERSON, _SECOND), ("test-peer-b", _SECOND),
        )
    )
    scope = RegistrationScope(policy, Persons({}, {}, life360=life360))
    return SqlItemRegistration(scope, _FixedRevisions())


async def _build(engine, registration, trip_circles):
    rollups = SqlRollupStore(engine, registration=registration)
    build = await rollups.create_build(code_version="test", params_sha256="test")
    store = SqlMobilityStore(engine, build_id=build, registration=registration)
    await store.commit_fold(
        subject_id=_PERSON,
        state=FoldState(),
        stays=[Stay(_PERSON, _PERSON, _FIRST, _START, _END, 1.0, 2.0, 5.0, 2)],
        trips=[
            Trip(_PERSON, _PERSON, circle, _START + n, _END, 10.0, 5.0, 2, ("Road",))
            for n, circle in enumerate(trip_circles)
        ],
    )
    place = await store.save_place(
        subject_id=_PERSON, circle_id=_FIRST, place_id=None, lat=1.0, lng=2.0, radius_m=5.0,
        kind="home", confidence=0.8, label="A place", saved_place_id=None,
        first_seen_ts=_START, last_seen_ts=_END,
    )
    await store.save_corridor(
        subject_id=_PERSON, corridor_id=None, sequence=("Road",), sample_count=1,
        first_seen_ts=_START, last_seen_ts=_END,
    )
    await store.replace_occupancy(subject_id=_PERSON, place_id=place, bins={5: 5000}, now_ms=_END)
    await store.upsert_rhythm(
        subject_id=_PERSON, place_id=place, mean_arrival_minute=10, mean_departure_minute=11,
        duration_median_ms=5000, duration_mad_ms=10, sample_count=1, now_ms=_END,
    )


async def _units(engine):
    async with engine.connect() as conn:
        return {
            table.name: sorted(
                (unit for unit in await conn.scalars(sa.select(table.c.unit))), key=str,
            )
            for table in (*_PLACED, PRESENCE_CORRIDORS)
        }


@pytest.mark.parametrize("stored", [False, True], ids=["live", "stored"])
@pytest.mark.parametrize(("trip_circles", "corridor"), [
    ((_FIRST,), ["test-primary"]),
    ((_FIRST, _SECOND), [None]),
], ids=["one-circle", "two-circles"])
async def test_a_presence_aggregate_reaches_only_units_its_inputs_circles_cover(
    clean_pg_url, stored, trip_circles, corridor,
):
    from nora.infra.sql.engine import create_sql_engine

    engine = create_sql_engine(clean_pg_url)
    try:
        registration = _registration()
        await _build(engine, None if stored else registration, trip_circles)
        if stored:
            await RegisterStoredRows(SqlStoredRows(engine, registration.scope)).run()
        units = await _units(engine)
    finally:
        await engine.dispose()
    # The place's circle covers only the first unit; the second unit's peer is not in it.
    assert {table.name: units[table.name] for table in _PLACED} == {
        table.name: ["test-primary"] for table in _PLACED
    }
    assert units["presence_corridors"] == corridor
