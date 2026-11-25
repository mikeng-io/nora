from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from aiohttp import web
from aiohttp.test_utils import TestServer
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.graph_registration import identities_column
from nora.domain.presence import SavedPlace
from nora.infra.sql.cognitive_graph_schema import EDGE, REGISTRY
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import PRESENCE_PLACES
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.persons import Life360Membership, Persons
from nora.policy import Conversation, Policy

_CIRCLE = "test-circle"
_PERSON = "test-person"
_UPDATED = 1_795_000_000_127
_UNITS = ("test-primary", "test-affiliate")


class _FixedRevisions:
    def current(self) -> dict[str, str]:
        return {f"rev_{key}": f"test-{key}" for key in ("code", "config", "policy", "identity")}


def _store(engine: AsyncEngine, units: tuple[str, ...]) -> PresenceStore:
    members = (Life360Membership(_PERSON, "test-member", _CIRCLE),)
    conversations = tuple(
        Conversation("signal", frozenset({f"room-{unit}"}), frozenset({_PERSON}), unit=unit)
        for unit in units
    )
    resolver = RegistrationScope(Policy(conversations), Persons({}, {}, life360=members))
    return PresenceStore(
        engine,
        registration=SqlItemRegistration(resolver, _FixedRevisions()),
        aggregate_graph=SqlCognitiveGraph(engine),
    )


def _place(key: str, name: str = "Generated", *, updated: int = _UPDATED) -> SavedPlace:
    return SavedPlace(
        circle_id=_CIRCLE,
        place_id=key,
        name=name,
        lat=1.0,
        lng=2.0,
        radius_m=5.0,
        updated_ms=updated,
    )


@pytest.fixture
async def engine(clean_pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize("units", [_UNITS, ()], ids=["two-units", "no-unit"])
async def test_saved_places_register_once_per_observing_unit(
    engine: AsyncEngine,
    units: tuple[str, ...],
) -> None:
    await _store(engine, units).replace_places(circle_id=_CIRCLE, places=[_place("one")])
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(PRESENCE_PLACES))).mappings().all()
        registered = {(r.unit, r.id) for r in (await conn.execute(sa.select(REGISTRY))).all()}
    assert {row["unit"] for row in rows} == set(units or (None,))
    assert len(rows) == len(units or (None,))
    assert registered == {(r["unit"], r["graph_id"]) for r in rows if r["unit"] is not None}
    assert len({r["graph_id"] for r in rows}) == len(rows)
    time = datetime.fromtimestamp(_UPDATED / 1000, tz=UTC)
    for row in rows:
        assert (row["unit"] is None) == (row["graph_id"] is None)
        assert row["kind"] == "aggregate" and row["basis"] == "computed"
        assert row["identities"] == [_PERSON]
        assert row["writer"] == "PresenceStore"
        assert row["excluded_identities"] == []
        assert row["exposure"] == "internal"
        assert row["occurred_at"] == row["asserted_at"] == time
        assert row["valid_from"] is row["valid_to"] is row["origin_room"] is None


@pytest.mark.parametrize("partial", [True, False], ids=["malformed", "complete"])
async def test_partial_saved_places_snapshot_preserves_uncovered_heads_in_each_unit(
    engine: AsyncEngine, partial: bool,
) -> None:
    from nora.infra.life360 import Life360Client
    from nora.presence.collector import CircleSpec, PresenceCollector

    store = _store(engine, _UNITS)
    await store.replace_places(circle_id=_CIRCLE, places=[_place("one"), _place("two")])
    good = {"id": "one", "name": "Updated", "latitude": "1", "longitude": "2"}
    malformed = {"id": "two", "name": "Unparseable", "latitude": "unknown", "longitude": "2"}

    async def members(_request: web.Request) -> web.Response:
        return web.json_response({"members": [{"id": "unmapped-test-member"}]})

    async def places(_request: web.Request) -> web.Response:
        return web.json_response({"places": [good, malformed] if partial else [good]})

    http = web.Application()
    http.router.add_get("/members/{circle_id}", members)
    http.router.add_get("/places/{circle_id}", places)
    async with TestServer(http) as server:
        root = str(server.make_url("/")).rstrip("/")
        client = Life360Client(
            client_credential="generated", username="", password="",
            access_token="generated",  # noqa: S106 — synthetic local bearer
            members_url=root + "/members/{circle_id}",
            places_url=root + "/places/{circle_id}",
        )
        circle = CircleSpec(_CIRCLE, {})
        collector = PresenceCollector(
            client=client, store=store, circles=[circle], cadence_seconds=60,
        )
        await collector.collect_once(circle, now_ms=_UPDATED + 1000)

    table = PRESENCE_PLACES.c
    async with engine.connect() as conn:
        heads = (await conn.execute(sa.select(
            table.unit, table.place_id, table.name, table.state, REGISTRY.c.state,
        ).select_from(PRESENCE_PLACES.join(
            REGISTRY, (table.graph_id == REGISTRY.c.id) & (table.unit == REGISTRY.c.unit),
        )))).all()
    uncovered = ("Generated", "live", "live") if partial else (None, "tombstoned", "tombstoned")
    outcomes = (("one", "Updated", "live", "live"), ("two", *uncovered))
    assert set(heads) == {(unit, *outcome) for unit in _UNITS for outcome in outcomes}


async def test_saved_place_rebuild_keeps_registry_and_tombstones_missing_head(
    engine: AsyncEngine,
) -> None:
    store = _store(engine, (_UNITS[0],))
    place = _place("one")
    await store.replace_places(circle_id=_CIRCLE, places=[place])
    async with engine.begin() as conn:
        before = (await conn.execute(sa.select(REGISTRY))).mappings().one()
    graph = SqlCognitiveGraph(engine)
    await graph.create(
        EDGE,
        {
            "id": "test-edge",
            "unit": before["unit"],
            "kind": "edge",
            "state": "live",
            "basis": "observed",
            "exposure": "internal",
            "writer": "test",
            "src_id": before["id"],
            "dst_id": before["id"],
            "type": "cites",
        },
    )
    await store.replace_places(circle_id=_CIRCLE, places=[place])
    async with engine.connect() as conn:
        same = (
            (await conn.execute(sa.select(REGISTRY).where(REGISTRY.c.id == before["id"])))
            .mappings()
            .one()
        )
    assert same == before
    await store.replace_places(circle_id=_CIRCLE, places=[])
    async with engine.connect() as conn:
        head = (await conn.execute(sa.select(PRESENCE_PLACES))).mappings().one()
        endpoint = (
            await conn.execute(
                sa.select(REGISTRY.c.id, REGISTRY.c.state).join(
                    EDGE,
                    (EDGE.c.unit == REGISTRY.c.unit) & (EDGE.c.dst_id == REGISTRY.c.id),
                )
            )
        ).one()
    assert endpoint == (before["id"], "tombstoned")
    assert head["graph_id"] == before["id"] and head["state"] == "tombstoned"
    assert all(head[key] is None for key in ("name", "lat", "lng", "radius_m", "address"))


async def test_saved_places_inherit_only_their_own_units_input_scope(
    engine: AsyncEngine,
) -> None:
    first, second = _UNITS
    store = _store(engine, _UNITS)
    await store.replace_places(
        circle_id=_CIRCLE,
        places=[_place("one")],
        inputs=[
            {
                "unit": first,
                "exposure": "operator",
                "excluded_identities": ["test-excluded"],
                "lineage": [["presence", _PERSON, None]],
            },
            {"unit": second, "exposure": "external", "excluded_identities": [], "lineage": {}},
        ],
    )
    async with engine.connect() as conn:
        planes = {
            r.unit: (r.exposure, r.excluded_identities)
            for r in (
                await conn.execute(
                    sa.select(
                        PRESENCE_PLACES.c.unit,
                        PRESENCE_PLACES.c.exposure,
                        PRESENCE_PLACES.c.excluded_identities,
                    )
                )
            ).all()
        }
    assert planes == {first: ("operator", ["test-excluded"]), second: ("internal", [])}


_BUILD_FAMILIES = (
    "presence_stays",
    "presence_trips",
    "presence_derived_places",
    "presence_corridors",
    "presence_occupancy",
    "presence_place_rhythm",
    "rollup_colocation",
    "rollup_outing",
    "rollup_daybook",
    "rollup_tie",
    "rollup_response",
)
_END = _UPDATED + 5_000


async def _record(
    engine: AsyncEngine, registration: SqlItemRegistration, *,
    restricted: bool, reaction: bool = False,
) -> int:
    from nora.domain.models import NormalizedMessage
    from nora.domain.presence import PresenceFix, WireState
    from nora.infra.sql.audit import SqlRecordStore
    from nora.infra.sql.schema import MESSAGE_REACTION, MESSAGES

    await PresenceStore(engine, registration=registration).record_fix(
        fix=PresenceFix(
            _CIRCLE,
            _PERSON,
            _UPDATED,
            1.0,
            2.0,
            WireState.LIVE,
            _UPDATED,
        )
    )
    store = SqlRecordStore(engine, registration=registration)
    source = NormalizedMessage(
        "signal", f"room-{_UNITS[0]}", "group", "test-author", None, _UPDATED, "Generated", {},
    )
    message = await store.append(message=source, person_id=_PERSON)
    if reaction:
        await store.append_reaction(message_id=message, message=source, person_id=_PERSON)
    if restricted:
        inputs = (MESSAGE_REACTION,) if reaction else (MESSAGES,)
        async with engine.begin() as conn:
            for tab in inputs:
                await conn.execute(
                    sa.update(tab).values(
                        exposure="operator",
                        excluded_identities=["test-excluded"],
                    )
                )
    return message


async def _build_mobility(engine: AsyncEngine, build: int, reg: SqlItemRegistration) -> None:
    from nora.domain.mobility import Stay, Trip
    from nora.domain.rollup import FoldState
    from nora.infra.sql.mobility import SqlMobilityStore

    store = SqlMobilityStore(engine, build_id=build, registration=reg)
    await store.commit_fold(
        subject_id=_PERSON,
        state=FoldState(),
        stays=[Stay(_PERSON, _PERSON, _CIRCLE, _UPDATED, _END, 1.0, 2.0, 5.0, 2)],
        trips=[Trip(_PERSON, _PERSON, _CIRCLE, _UPDATED, _END, 10.0, 5.0, 2, ("Road",))],
    )
    place = await store.save_place(
        subject_id=_PERSON,
        circle_id=_CIRCLE,
        place_id=None,
        lat=1.0,
        lng=2.0,
        radius_m=5.0,
        kind="home",
        confidence=0.8,
        label="A place",
        saved_place_id=None,
        first_seen_ts=_UPDATED,
        last_seen_ts=_END,
    )
    await store.relink_stays(subject_id=_PERSON, links={_UPDATED: place})
    await store.save_corridor(
        subject_id=_PERSON,
        corridor_id=None,
        sequence=("Road",),
        sample_count=1,
        first_seen_ts=_UPDATED,
        last_seen_ts=_END,
    )
    await store.replace_occupancy(
        subject_id=_PERSON,
        place_id=place,
        bins={5: 5000},
        now_ms=_END,
    )
    await store.upsert_rhythm(
        subject_id=_PERSON,
        place_id=place,
        mean_arrival_minute=10,
        mean_departure_minute=11,
        duration_median_ms=5000,
        duration_mad_ms=10,
        sample_count=1,
        now_ms=_END,
    )


async def _build_rollup(
    engine: AsyncEngine,
    reg: SqlItemRegistration,
    message_id: int,
    *,
    empty: bool = False,
    reaction: bool = False,
) -> int:
    from nora.domain.rollup import Colocation, Daybook, Outing, Response, Tie
    from nora.infra.sql.rollups import SqlRollupStore

    unit = _UNITS[0]
    room = f"room-{unit}"
    store = SqlRollupStore(engine, registration=reg)
    build = await store.create_build(code_version="test", params_sha256="test")
    if not empty:
        await _build_mobility(engine, build, reg)
        common = {"unit": unit, "valid_from_ms": _UPDATED, "valid_to_ms": _END, "as_of_ms": _END}
        await store.apply(
            build_id=build,
            unit=unit,
            colocations=[
                Colocation(
                    **common,
                    persons=(_PERSON,),
                    lat=1,
                    lng=2,
                    radius_m=5,
                    place_label=None,
                    saved_place_id=None,
                    at_home_of=(),
                    homes_unknown=(),
                    stay_count=1,
                    open=False,
                )
            ],
            outings=[
                Outing(
                    **common,
                    persons=(_PERSON,),
                    lat=1,
                    lng=2,
                    place_label=None,
                    saved_place_id=None,
                    open=False,
                )
            ],
            daybooks=[
                Daybook(
                    **common,
                    person_id=_PERSON,
                    local_date="2026-11-01",
                    stays=({"start": _UPDATED, "end": _END},),
                    stay_ms=5000,
                    home_ms=0,
                    colocation_ms=0,
                    with_persons=(),
                    outings=0,
                    rooms={f"signal:{room}": 1},
                    messages=1,
                    reactions=0,
                )
            ],
            ties=[
                Tie(
                    **common,
                    person_a=_PERSON,
                    person_b="test-z",
                    iso_week="2026-W44",
                    replies_ab=1,
                    replies_ba=0,
                    mentions_ab=0,
                    mentions_ba=0,
                    reactions_ab=0,
                    reactions_ba=0,
                    colocation_ms=5000,
                    rooms=(f"signal:{room}",),
                )
            ],
            responses=[
                Response(
                    **common,
                    message_id=message_id,
                    platform="signal",
                    conversation_id=room,
                    sent_ts=_UPDATED,
                    replies=int(not reaction),
                    reactions=int(reaction),
                    edits=0,
                    responders=(_PERSON,),
                    named_persons=(),
                    intent=None,
                    outcome="responded",
                )
            ],
        )
    await store.advance_build(build_id=build, through_ms=_END)
    await store.switch_build(build_id=build)
    return build


def _rollup_registration() -> SqlItemRegistration:
    from nora.domain.models import PersonRef

    unit = _UNITS[0]
    room = f"room-{unit}"
    person = PersonRef(_PERSON, "Generated")
    member = Life360Membership(_PERSON, "test-member", _CIRCLE)
    conversation = Conversation("signal", frozenset({room}), frozenset({_PERSON}), unit=unit)
    policy = Policy((conversation,), _by_id={("signal", room): conversation})
    people = Persons({_PERSON: person}, {("signal", "test-author"): _PERSON}, life360=(member,))
    return SqlItemRegistration(RegistrationScope(policy, people), _FixedRevisions())


@pytest.mark.parametrize("family", _BUILD_FAMILIES)
@pytest.mark.parametrize("restricted", [False, True], ids=["unrestricted", "restricted"])
async def test_rollup_registers_and_inherits_actual_input_scope(
    engine: AsyncEngine,
    family: str,
    restricted: bool,
) -> None:
    from nora.infra.sql.schema import METADATA

    registration = _rollup_registration()
    reaction = family == "rollup_response"
    message = await _record(engine, registration, restricted=restricted, reaction=reaction)
    build = await _build_rollup(engine, registration, message, reaction=reaction)
    table = METADATA.tables[family]
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(table).where(table.c.build_id == build),
                )
            )
            .mappings()
            .one()
        )
        entry = (
            (
                await conn.execute(
                    sa.select(REGISTRY).where(
                        REGISTRY.c.id == row["graph_id"],
                        REGISTRY.c.unit == row["unit"],
                    )
                )
            )
            .mappings()
            .one()
        )
    assert entry["state"] == row["state"] == "live"
    assert entry["kind"] == row["kind"] == "aggregate" and row["basis"] == "computed"
    assert row["unit"] == _UNITS[0] and _PERSON in row[identities_column(family)]
    assert row["asserted_at"] == row["recorded_at"]
    start = datetime.fromtimestamp(_UPDATED / 1000, tz=UTC)
    end = datetime.fromtimestamp(_END / 1000, tz=UTC)
    if family in ("presence_occupancy", "presence_place_rhythm"):
        assert row["occurred_at"] == end and row["valid_from"] is row["valid_to"] is None
    else:
        assert row["occurred_at"] == row["valid_from"] == start and row["valid_to"] == end
    restricted_graph_input = restricted and family in {
        "rollup_daybook", "rollup_tie", "rollup_response",
    }
    assert row["excluded_identities"] == (["test-excluded"] if restricted_graph_input else [])
    assert row["exposure"] == (
        "operator" if restricted_graph_input
        else "external" if family == "rollup_response" else "internal"
    )
    assert row["rev_config"] == "test-config"
    assert all(row[name] is None for name in ("replaces", "occasion", "pass", "model", "trace"))


@pytest.mark.parametrize("family", _BUILD_FAMILIES)
async def test_rollup_rebuild_keeps_identity_and_edge_then_tombstones_retired_key(
    engine: AsyncEngine,
    family: str,
) -> None:
    from nora.infra.sql.schema import METADATA

    registration = _rollup_registration()
    message = await _record(engine, registration, restricted=False)
    first = await _build_rollup(engine, registration, message)
    table = METADATA.tables[family]
    async with engine.connect() as conn:
        entry = (
            (
                await conn.execute(
                    sa.select(REGISTRY)
                    .join(
                        table,
                        (REGISTRY.c.id == table.c.graph_id) & (REGISTRY.c.unit == table.c.unit),
                    )
                    .where(table.c.build_id == first)
                )
            )
            .mappings()
            .one()
        )
    await SqlCognitiveGraph(engine).create(
        EDGE,
        {
            "id": "test-edge",
            "unit": entry["unit"],
            "kind": "edge",
            "state": "live",
            "basis": "computed",
            "exposure": "internal",
            "writer": "test",
            "src_id": entry["id"],
            "dst_id": entry["id"],
            "type": "cites",
        },
    )
    second = await _build_rollup(engine, registration, message)
    async with engine.connect() as conn:
        same = (
            (
                await conn.execute(
                    sa.select(REGISTRY)
                    .join(
                        table,
                        (REGISTRY.c.id == table.c.graph_id) & (REGISTRY.c.unit == table.c.unit),
                    )
                    .where(table.c.build_id == second)
                )
            )
            .mappings()
            .one()
        )
    assert same == entry
    await _build_rollup(engine, registration, message, empty=True)
    async with engine.connect() as conn:
        endpoint = (
            await conn.execute(
                sa.select(REGISTRY.c.id, REGISTRY.c.state).join(
                    EDGE,
                    (EDGE.c.unit == REGISTRY.c.unit) & (EDGE.c.dst_id == REGISTRY.c.id),
                )
            )
        ).one()
        retained = (
            await conn.execute(
                sa.select(table.c.graph_id, table.c.state).where(
                    table.c.build_id == second,
                )
            )
        ).one()
    assert endpoint == retained == (entry["id"], "tombstoned")


async def test_item_tombstone_keeps_the_preceding_version_for_as_of(
    engine: AsyncEngine,
) -> None:
    from nora.infra.sql.history import as_of
    from nora.infra.sql.schema import SOURCE_HEALTH
    from nora.infra.sql.source_health import SqlSourceHealthLog

    store = SqlSourceHealthLog(engine, registration=_rollup_registration())
    assert await store.record(
        source="presence", scope=_CIRCLE, event="poll", outcome="ok",
        at_ms=_UPDATED, detail="generated observation",
    )
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(SOURCE_HEALTH))).mappings().one()
        before = await conn.scalar(sa.select(sa.func.clock_timestamp()))
    await SqlCognitiveGraph(engine).tombstone(
        SOURCE_HEALTH, row_id=row["graph_id"], unit=row["unit"], content_hash="generated-hash",
    )
    async with engine.connect() as conn:
        after = await conn.scalar(sa.select(sa.func.clock_timestamp()))
        preceding = [item async for batch in as_of(conn, "source_health", before) for item in batch]
        latest = [item async for batch in as_of(conn, "source_health", after) for item in batch]
    assert len(preceding) == len(latest) == 1
    assert preceding[0]["graph_id"] == latest[0]["graph_id"] == row["graph_id"]
    assert preceding[0]["state"] == "live" and preceding[0]["detail"] == "generated observation"
    assert latest[0]["state"] == "tombstoned" and latest[0]["detail"] is None


@pytest.mark.parametrize("restricted", [False, True], ids=["unrestricted", "restricted"])
async def test_platform_head_inherits_scope_folds_partial_and_survives_replay(
    engine: AsyncEngine, restricted: bool,
) -> None:
    from nora.domain.models import EditTarget
    from nora.domain.observations import Coverage, Observation, PinPayload, SourceScope
    from nora.infra.sql.observations import SqlObservationStore
    from nora.infra.sql.schema import PLATFORM_OBJECT_STATE, PLATFORM_OBSERVATION

    room = f"room-{_UNITS[0]}"
    scope = SourceScope("signal", "generated-account", room)
    store = SqlObservationStore(engine, registration=_rollup_registration())
    older = EditTarget(message_id="generated-pin-a")
    newer = EditTarget(message_id="generated-pin-b")
    row_id = await store.append(
        observation=Observation(
            "pin", scope, "generated-pins", "snapshot", PinPayload(pins=(older,)),
            occurred_ms=_UPDATED, received_ms=_UPDATED, time_quality="upstream",
        ),
        event_id="generated-snapshot", source_digest="generated-full-digest",
    )
    if restricted:
        async with engine.begin() as conn:
            await conn.execute(sa.update(PLATFORM_OBSERVATION).where(
                PLATFORM_OBSERVATION.c.id == row_id,
            ).values(exposure="internal", excluded_identities=["test-excluded"]))
        await store.rebuild_heads(scope=scope, through_id=row_id)
    watermark = await store.append(
        observation=Observation(
            "pin", scope, "generated-pins", "snapshot", PinPayload(pins=(newer,)),
            occurred_ms=_END, received_ms=_END, time_quality="upstream",
            coverage=Coverage(complete=False, covered="pin_set"),
        ),
        event_id="generated-partial", source_digest="generated-partial-digest",
    )
    assert watermark is not None
    async with engine.connect() as conn:
        before = (await conn.execute(sa.select(PLATFORM_OBJECT_STATE))).mappings().one()
        reg = (await conn.execute(sa.select(REGISTRY).where(
            REGISTRY.c.kind == "aggregate",
        ))).mappings().one()
    assert before["state"] == reg["state"] == "live"
    assert before["kind"] == reg["kind"] == "aggregate" and before["basis"] == "computed"
    assert before["graph_id"] == reg["id"] and before["unit"] == reg["unit"] == _UNITS[0]
    assert before["occurred_at"] == datetime.fromtimestamp(_END / 1000, tz=UTC)
    assert before["asserted_at"] == before["updated_at"]
    assert before["origin_room"] == room and before["valid_from"] is before["valid_to"] is None
    assert before["excluded_identities"] == (["test-excluded"] if restricted else [])
    assert before["exposure"] == ("internal" if restricted else "external")
    assert len(before["source_state"]["pins"]) == 2
    graph = SqlCognitiveGraph(engine)
    await graph.create(EDGE, {
        "id": "generated-head-edge", "unit": reg["unit"], "kind": "edge", "type": "cites",
        "basis": "computed", "writer": "test", "state": "live", "exposure": "internal",
        "src_id": reg["id"], "dst_id": reg["id"],
    })
    await store.rebuild_heads(scope=scope, through_id=watermark)
    async with engine.connect() as conn:
        after = (await conn.execute(sa.select(PLATFORM_OBJECT_STATE))).mappings().one()
        current = (await conn.execute(sa.select(REGISTRY).where(
            REGISTRY.c.id == reg["id"],
        ))).mappings().one()
    assert reg == current
    for column in ("graph_id", "source_state", "exposure", "excluded_identities"):
        assert before[column] == after[column]
    await store.rebuild_heads(scope=scope, through_id=0)
    async with engine.connect() as conn:
        retired = (await conn.execute(sa.select(PLATFORM_OBJECT_STATE))).mappings().one()
        tip = (await conn.execute(sa.select(REGISTRY).join(
            EDGE, (REGISTRY.c.id == EDGE.c.dst_id) & (REGISTRY.c.unit == EDGE.c.unit),
        ))).mappings().one()
    assert tip["id"] == retired["graph_id"] == before["graph_id"]
    assert retired["state"] == tip["state"] == "tombstoned" and retired["source_state"] is None


async def test_room_unit_change_tombstones_old_head_without_copying_its_pins(
    engine: AsyncEngine,
) -> None:
    from nora.domain.models import EditTarget
    from nora.domain.observations import Coverage, Observation, PinPayload, SourceScope
    from nora.infra.sql.observations import SqlObservationStore
    from nora.infra.sql.schema import PLATFORM_OBJECT_STATE

    room = "generated-moving-room"
    scope = SourceScope("signal", "generated-account", room)
    graph = SqlCognitiveGraph(engine)
    for index, unit in enumerate(_UNITS):
        convo = Conversation("signal", frozenset({room}), frozenset(), unit=unit)
        policy = Policy((convo,), _by_id={("signal", room): convo})
        registration = SqlItemRegistration(
            RegistrationScope(policy, Persons({}, {})), _FixedRevisions(),
        )
        store = SqlObservationStore(engine, registration=registration)
        row_id = await store.append(
            observation=Observation(
                "pin", scope, "generated-pin-set", "snapshot",
                PinPayload(pins=(EditTarget(message_id=f"generated-pin-{index}"),)),
                occurred_ms=_UPDATED + index, received_ms=_UPDATED + index,
                coverage=Coverage(complete=index == 0, covered="pin_set"),
            ),
            event_id=f"generated-event-{index}", source_digest=f"generated-digest-{index}",
        )
        assert row_id is not None
        async with engine.connect() as conn:
            head_id = await conn.scalar(sa.select(PLATFORM_OBJECT_STATE.c.graph_id).where(
                PLATFORM_OBJECT_STATE.c.unit == unit,
            ))
        if index == 0:
            await graph.create(EDGE, {
                "id": "generated-old-pin-edge", "unit": unit, "kind": "edge", "type": "cites",
                "basis": "computed", "writer": "test", "state": "live", "exposure": "internal",
                "src_id": head_id, "dst_id": head_id,
            })
    await store.rebuild_heads(scope=scope, through_id=row_id)
    current = await store.current(scope=scope, kind="pin", object_ref="generated-pin-set")
    assert current is not None and set(current.state["pins"]) == {"id:generated-pin-1"}
    async with engine.connect() as conn:
        t = PLATFORM_OBJECT_STATE.c
        retired = (await conn.execute(sa.select(
            t.state, t.source_state, REGISTRY.c.state.label("registry_state"),
        ).select_from(PLATFORM_OBJECT_STATE.join(
            REGISTRY, (t.graph_id == REGISTRY.c.id) & (t.unit == REGISTRY.c.unit),
        ).join(EDGE, (REGISTRY.c.id == EDGE.c.dst_id) & (REGISTRY.c.unit == EDGE.c.unit)))
        )).one()
    assert retired == ("tombstoned", None, "tombstoned")


@pytest.mark.parametrize("family", ["presence_derived_places", "presence_corridors"])
async def test_growing_presence_span_keeps_the_first_registered_identity(
    engine: AsyncEngine, family: str,
) -> None:
    from nora.infra.sql.mobility import SqlMobilityStore
    from nora.infra.sql.rollups import SqlRollupStore
    from nora.infra.sql.schema import METADATA

    registration = _rollup_registration()
    rollups = SqlRollupStore(engine, registration=registration)
    table = METADATA.tables[family]

    async def save(build: int, id_: int | None, end: int) -> int:
        mobility = SqlMobilityStore(engine, build_id=build, registration=registration)
        if family == "presence_corridors":
            return await mobility.save_corridor(
                subject_id=_PERSON, corridor_id=id_, sequence=("Generated way",),
                sample_count=2, first_seen_ts=_UPDATED, last_seen_ts=end,
            )
        return await mobility.save_place(
            subject_id=_PERSON, circle_id=_CIRCLE, place_id=id_,
            lat=1, lng=2, radius_m=3, kind="unlabeled", confidence=0.5,
            label=None, saved_place_id=None, first_seen_ts=_UPDATED, last_seen_ts=end,
        )

    first = await rollups.create_build(code_version="fixture", params_sha256="fixture")
    row_id = await save(first, None, _UPDATED + 100)
    await rollups.switch_build(build_id=first)
    async with engine.connect() as conn:
        graph_id = await conn.scalar(sa.select(table.c.graph_id).where(table.c.id == row_id))
    assert graph_id is not None

    assert await save(first, row_id, _END) == row_id
    rebuilt = await rollups.create_build(code_version="fixture", params_sha256="fixture")
    new_row_id = await save(rebuilt, None, _END)
    await rollups.switch_build(build_id=rebuilt)
    async with engine.connect() as conn:
        ids = (await conn.execute(sa.select(
            table.c.graph_id, table.c.valid_to, table.c.state,
        ).where(table.c.id.in_((row_id, new_row_id))))).all()
        state = await conn.scalar(sa.select(REGISTRY.c.state).where(REGISTRY.c.id == graph_id))
    assert ids == [(graph_id, datetime.fromtimestamp(_END / 1000, tz=UTC), "live")] * 2
    assert state == "live"
