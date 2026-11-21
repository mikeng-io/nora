"""Stored Record and Rollup registration on generated Postgres data."""

from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS, REGISTRY
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import MESSAGES, METADATA, SOURCE_HEALTH
from nora.memory.adapters.stored_row_registration import SqlStoredRows
from nora.memory.app.register_stored_rows import RegisterStoredRows
from nora.memory.domain.registration import AGGREGATE_FAMILIES, BACKFILL_ITEM_SOURCES
from tests.infra.test_aggregate_registration import (
    _CIRCLE,
    _UNITS,
    _build_rollup,
    _place,
    _record,
    _rollup_registration,
    _store,
)
from tests.infra.test_cognitive_graph_schema import PLANES as KIND_PLANES
from tests.infra.test_item_registration import _SECOND, _UNIT, _registration, _Writer

pytestmark = pytest.mark.fresh_database

TABLES = (*BACKFILL_ITEM_SOURCES, *AGGREGATE_FAMILIES)
PLANES = (KIND_PLANES - {"id"}) | {"graph_id"}
COMPARED = PLANES - {"asserted_at", *(name for name in PLANES if name.startswith("rev_"))}
AT = datetime(2026, 1, 1, tzinfo=UTC)
ROOM = f"room-{_UNITS[0]}"


@pytest.fixture
async def engine(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _snapshots(conn):
    return {
        name: {
            row["graph_id"]: {column: row[column] for column in COMPARED}
            for row in (await conn.execute(sa.select(METADATA.tables[name]))).mappings()
            if row["graph_id"] is not None
        }
        for name in TABLES
    }


async def _legacy_fixture(engine):
    reg = _rollup_registration()
    message = await _record(engine, reg, restricted=False)
    writer = _Writer(engine, reg, room=ROOM)
    for name in BACKFILL_ITEM_SOURCES:
        if name not in ("messages", "presence_fixes"):
            await writer.write(name)
    await _build_rollup(engine, reg, message)
    await _store(engine, _UNITS[:1]).replace_places(circle_id=_CIRCLE, places=[_place("one")])
    await _Writer(engine, reg, room="generated-outside").write("messages")
    async with engine.begin() as conn:
        expected = await _snapshots(conn)
        for name in TABLES:
            table = METADATA.tables[name]
            check = conn.dialect.identifier_preparer.quote_identifier(f"ck_{name}_graph_pair")
            actual = conn.dialect.identifier_preparer.quote_identifier(name)
            await conn.execute(sa.text(f"ALTER TABLE {actual} DROP CONSTRAINT {check}"))
            values = dict.fromkeys(PLANES.intersection(table.c.keys()) - COMPARED)
            values.pop("asserted_at")
            values["graph_id"] = None
            if not name.startswith("rollup_"):
                values["unit"] = None
            values.update(
                persons=sa.text("'{}'::text[]") if name not in (
                    "rollup_colocation", "rollup_outing",
                ) else table.c.persons,
                occurred_at=None, valid_from=None, valid_to=None, origin_room=None,
                excluded_persons=sa.text("'{}'::text[]"), lineage=sa.text("'{}'::jsonb"),
            )
            await conn.execute(sa.update(table).values(values))
            await conn.execute(sa.text(
                f"ALTER TABLE {actual} ADD CONSTRAINT {check} "
                "CHECK ((unit IS NULL) = (graph_id IS NULL)) NOT VALID",
            ))
        await conn.execute(sa.delete(GRAPH_STIMULUS))
        await conn.execute(sa.delete(REGISTRY))
    assert all(expected[name] for name in TABLES)
    return expected


async def _validated(conn):
    names = [f"ck_{name}_graph_pair" for name in TABLES]
    return dict((await conn.execute(sa.text(
        "SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(:names)"
    ), {"names": names})).all())


def _stored_assertion(name, row):
    column = {
        "messages": "created_at", "message_reaction": "created_at",
        "message_delete": "created_at", "fetched_pages": "fetched_at",
        "presence_places": "updated_ms", "platform_object_state": "updated_at",
    }.get(name, "recorded_at")
    stamp = row[column]
    if isinstance(stamp, str):
        return datetime.fromisoformat(stamp)
    if isinstance(stamp, int):
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stamp)
    return stamp


async def test_stored_rows_match_live_planes_leave_unscoped_out_and_validate(engine):
    expected = await _legacy_fixture(engine)
    async with engine.connect() as conn:
        checks = await _validated(conn)
        assert len(checks) == len(TABLES) and not any(checks.values())
    command = RegisterStoredRows(SqlStoredRows(engine, _rollup_registration().scope), batch_size=2)
    report = await command.run()
    async with engine.connect() as conn:
        assert await _snapshots(conn) == expected
        checks = await _validated(conn)
        assert len(checks) == len(TABLES) and all(checks.values())
        outside = (await conn.execute(sa.select(MESSAGES.c.unit, MESSAGES.c.graph_id).where(
            MESSAGES.c.conversation_id == "generated-outside",
        ))).one()
        assert outside == (None, None)
        for name in TABLES:
            table = METADATA.tables[name]
            rows = sa.select(table).where(table.c.graph_id.is_not(None))
            for row in (await conn.execute(rows)).mappings():
                assert row["asserted_at"] == _stored_assertion(name, row), name
                assert all(
                    row[revision] is None for revision in PLANES - COMPARED - {"asserted_at"}
                ), name
    assert {name: stats.registered for name, stats in report.items()} == {
        name: len(rows) for name, rows in expected.items()
    }
    assert report["messages"].unscoped == 1
    assert sum(stats.unscoped for stats in report.values()) == 1
    again = await command.run()
    assert sum(stats.registered for stats in again.values()) == 0


async def _probes(engine, number):
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            INSERT INTO source_health (source, scope, event, outcome, at_ms, recorded_at)
            SELECT 'presence', :circle, 'poll', 'ok', n, :at FROM generate_series(1, :n) n
        """), {"circle": _CIRCLE, "n": number, "at": AT})


async def test_interrupted_batch_rolls_back_and_rerun_finishes(engine):
    await _probes(engine, 5)
    command = RegisterStoredRows(SqlStoredRows(engine, _rollup_registration().scope), batch_size=2)
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE FUNCTION generated_break_batch() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN
                IF NEW.at_ms = 4 THEN RAISE EXCEPTION 'generated interruption'; END IF;
                RETURN NEW;
              END;
            $$
        """))
        await conn.execute(sa.text("""
            CREATE TRIGGER generated_stop BEFORE UPDATE ON source_health
            FOR EACH ROW EXECUTE FUNCTION generated_break_batch()
        """))
    with pytest.raises(sa.exc.DBAPIError, match="generated interruption"):
        await command.run()
    async with engine.begin() as conn:
        original = dict((await conn.execute(
            sa.select(SOURCE_HEALTH.c.at_ms, SOURCE_HEALTH.c.graph_id)
        )).all())
        entries = await conn.scalar(sa.select(sa.func.count()).select_from(REGISTRY))
        assert entries == 2
        assert [at for at, graph_id in sorted(original.items()) if graph_id is not None] == [1, 2]
        await conn.execute(sa.text("DROP TRIGGER generated_stop ON source_health"))
    report = await command.run()
    assert report["source_health"].registered == 3
    async with engine.connect() as conn:
        rows = await conn.execute(sa.select(SOURCE_HEALTH.c.at_ms, SOURCE_HEALTH.c.graph_id))
        final = dict(rows.all())
    assert all(final.values()) and len(set(final.values())) == 5
    assert all(final[at] == original[at] for at in (1, 2))
    again = await command.run()
    assert again["source_health"].registered == 0


async def test_registration_updates_each_probe_batch_in_one_statement(engine):
    await _probes(engine, 5)
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE generated_updates (rows bigint)"))
        await conn.execute(sa.text("""
            CREATE FUNCTION generated_measure_batch() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN
                INSERT INTO generated_updates SELECT count(*) FROM updated;
                RETURN NULL;
              END;
            $$
        """))
        await conn.execute(sa.text("""
            CREATE TRIGGER generated_batch AFTER UPDATE ON source_health
            REFERENCING NEW TABLE AS updated
            FOR EACH STATEMENT EXECUTE FUNCTION generated_measure_batch()
        """))
    command = RegisterStoredRows(SqlStoredRows(engine, _rollup_registration().scope), batch_size=2)
    assert (await command.run())["source_health"].registered == 5
    async with engine.connect() as conn:
        updates = await conn.scalars(sa.text("SELECT rows FROM generated_updates ORDER BY rows"))
        assert updates.all() == [1, 2, 2]


async def test_registers_200000_generated_probes_within_120_seconds(engine, record_property):
    await _probes(engine, 200_000)
    start = monotonic()
    report = await RegisterStoredRows(SqlStoredRows(engine, _rollup_registration().scope)).run()
    elapsed = monotonic() - start
    record_property("registration_seconds", f"{elapsed:.3f}")
    assert report["source_health"].registered == 200_000
    async with engine.connect() as conn:
        count = await conn.scalar(sa.select(sa.func.count()).select_from(SOURCE_HEALTH).where(
            SOURCE_HEALTH.c.graph_id.is_not(None), SOURCE_HEALTH.c.asserted_at == AT,
        ))
    assert count == 200_000
    assert elapsed < 120, f"registration took {elapsed:.1f}s"


async def test_unregistered_source_is_backfilled_once_per_observing_unit(engine):
    from nora.domain.presence import PresenceFix, WireState
    from nora.infra.sql.item_registration import item_graph_id
    from nora.infra.sql.presence import PresenceStore
    from nora.infra.sql.schema import PRESENCE_FIXES, PRESENCE_PLACES
    from tests.infra.test_aggregate_registration import _PERSON, _UPDATED

    legacy = PresenceStore(engine)
    await legacy.record_fix(
        fix=PresenceFix(_CIRCLE, _PERSON, _UPDATED, 1.0, 2.0, WireState.LIVE, _UPDATED),
    )
    await legacy.replace_places(circle_id=_CIRCLE, places=[_place("one")])
    await _probes(engine, 1)
    command = RegisterStoredRows(SqlStoredRows(engine, _registration(multiple=True).scope))
    report = await command.run()

    async with engine.connect() as conn:
        for table in (PRESENCE_FIXES, PRESENCE_PLACES, SOURCE_HEALTH):
            rows = (await conn.execute(sa.select(table))).mappings().all()
            assert {row["unit"] for row in rows} == {_UNIT, _SECOND}
            assert len({row["graph_id"] for row in rows}) == len(rows) == 2
            assert report[table.name].registered == 2
            if table is not PRESENCE_PLACES:
                assert all(
                    row["graph_id"] == item_graph_id(table.name, (row["id"],), row["unit"])
                    for row in rows
                )
    assert not any(result.registered for result in (await command.run()).values())


async def test_discord_backfill_uses_the_offline_directory_and_refuses_missing_guilds(
    engine, tmp_path,
):
    import json

    from nora.domain.models import NormalizedMessage
    from nora.infra.sql.audit import SqlRecordStore
    from nora.memory.adapters.register_stored import register_with_policy
    from nora.persons import Persons

    guild = "100000000000000101"
    admitted = "100000000000000102"
    ignored = "100000000000000103"
    unit = "generated-discord"
    policy = tmp_path / "policy.toml"
    policy.write_text(
        f'default = "deny"\n[[conversations]]\nplatform = "discord"\nguild = "{guild}"\n'
        f'unit = "{unit}"\nlabel = "Generated Guild"\nignore = ["Skip"]\npersons = "*"\n',
        encoding="utf-8",
    )
    snapshot = tmp_path / "channels.json"
    snapshot.write_text("{}", encoding="utf-8")
    store = SqlRecordStore(engine)
    for channel in (admitted, ignored):
        await store.append(message=NormalizedMessage(
            "discord", f"{guild}/{channel}", "group", "generated-account", None,
            1795000000127, "Generated", {},
        ))

    with pytest.raises(SystemExit, match=f"unavailable: .*{guild}"):
        await register_with_policy(
            engine, policy_path=policy, persons=Persons({}, {}), channel_directory=snapshot,
        )
    async with engine.connect() as conn:
        pending = await conn.scalar(sa.select(sa.func.count()).select_from(REGISTRY))
        assert pending == 0
        assert not any((await _validated(conn)).values())

    snapshot.write_text(json.dumps({guild: {admitted: "Keep", ignored: "Skip"}}), encoding="utf-8")
    report = await register_with_policy(
        engine, policy_path=policy, persons=Persons({}, {}), channel_directory=snapshot,
    )
    assert report["messages"].registered == 1 and report["messages"].unscoped == 1
    async with engine.connect() as conn:
        rows = await conn.execute(sa.select(MESSAGES.c.conversation_id, MESSAGES.c.unit))
        assert dict(rows.all()) == {f"{guild}/{admitted}": unit, f"{guild}/{ignored}": None}
        assert all((await _validated(conn)).values())


async def test_backfill_registers_probes_and_keeps_unscoped_acts(engine):
    from nora.infra.sql.cognitive_graph_schema import NORA_ACT, NORA_ACT_OUTCOME
    from nora.memory.adapters.act_store import SqlActStore
    from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome

    registration = _rollup_registration()
    store = SqlActStore(engine, registration)
    act = Act(ActIdentity("fixture", "generated-act", 0), "effect",
              "generated-destination", ActAuthority(None), "unknown")
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _probes(engine, 1)

    report = await RegisterStoredRows(SqlStoredRows(engine, registration.scope)).run()
    assert report["source_health"].registered == 1
    assert not {"nora_act", "nora_act_outcome"} & report.keys()
    async with engine.connect() as conn:
        for table in (NORA_ACT, NORA_ACT_OUTCOME):
            row = (await conn.execute(sa.select(table.c.unit, table.c.graph_id))).one()
            assert row == (None, None)
        registered = await conn.scalar(sa.select(sa.func.count()).select_from(REGISTRY))
    assert registered == 1
