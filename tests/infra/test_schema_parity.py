"""The schema gate (DEC-11 / DEC-53), rebuilt on PostgreSQL catalogs.

`alembic check` guards column presence, column type and server defaults. It is
provably blind to the CHECK constraint and to the `ts DESC` index ordering, and
it reports permanent phantom drift on the expression index. This file is what
actually guarantees the schema, so it reads Postgres's own catalogues rather
than SQLAlchemy's opinion of them.

**Oracle scope, stated honestly (design/20 §2, both reviews).** The baseline is
a checked-in, normalised `pg_dump` (`pg_schema_baseline.sql`). A frozen dump
catches drift *from that baseline* — it is metadata+baseline parity. It does
**not** reach live production the way the old SQLite gate diffed against the
in-tree `.db`; the `nora` database publishes no host port (design/20 §3), so the
live-production half is a `docker exec nora-db pg_dump` job, named here rather
than silently dropped.

It still asserts, from the catalogue and independent of the baseline:
  - the CHECK constraint `conversation_type IN ('group','dm')` is present;
  - `idx_messages_conv_ts` is `(conversation_id, ts DESC)`, the descending flag
    read from the per-column `indoption` DESC **bit** (`indoption[i] & 1`), NOT
    equality to 1 (a `DESC NULLS FIRST` column is `1|2 = 3`, so `== 1` false-
    fails), and NOT `information_schema` (which does not expose index ordering).

Parameterised over every table in `nora`'s metadata, so when `presence_fixes`
(design/19) joins the same database its identically alembic-check-blind
`(circle_id, person_id, ts DESC)` index is covered with no new test.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from nora.infra.sql.migrations_runner import run_migrations, sync_url_for
from nora.infra.sql.schema import METADATA

BASELINE = Path(__file__).resolve().parent / "pg_schema_baseline.sql"

# Per (table, index): column -> is_descending. The gate asserts the live
# `indoption` DESC bit matches. `presence_fixes` adds its own row here the day it
# lands; the CHECK/existence checks below need no editing because they iterate
# `METADATA`.
EXPECTED_DESC_INDEXES = {
    "idx_messages_conv_ts": {"conversation_id": False, "ts": True},
    # presence_fixes (design/19 §8): the identically alembic-check-blind
    # `(circle_id, person_id, ts DESC)` index — covered here with no bespoke
    # test, exactly as this file's docstring promised it would be.
    "idx_presence_member_ts": {"circle_id": False, "person_id": False, "ts": True},
    # tool_call_log (tool-recall-log spec §2, §7): the identically
    # alembic-check-blind `(conversation_id, created_at DESC)` index.
    "idx_tool_call_log_conv_ts": {"conversation_id": False, "created_at": True},
    # presence_stays (presence analytics substrate design §2, DEC-115): the
    # identically alembic-check-blind `(subject_id, started_ts DESC)` index.
    "idx_presence_stays_subject_ts": {"subject_id": False, "started_ts": True},
    "idx_presence_derived_places_subject_kind": {"subject_id": False, "kind": False},
}


# ── catalogue helpers (reused by the drift probe, so the probe exercises the
#    exact reads the gate makes) ───────────────────────────────────────────────

def _check_constraint_defs(sync_url: str, table: str) -> list[str]:
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return list(conn.execute(sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"
            ), {"t": table}).scalars())
    finally:
        engine.dispose()


def _index_column_orders(sync_url: str, index: str) -> dict[str, bool]:
    """Column name -> is_descending, read from `pg_index.indoption`'s DESC bit."""
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT i.indkey, i.indoption FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :idx"
            ), {"idx": index}).one()
            indkey = [int(x) for x in str(row.indkey).split()]
            indoption = [int(x) for x in str(row.indoption).split()]
            table_oid = conn.execute(sa.text(
                "SELECT indrelid FROM pg_index i JOIN pg_class c "
                "ON c.oid = i.indexrelid WHERE c.relname = :idx"
            ), {"idx": index}).scalar_one()
            out: dict[str, bool] = {}
            for attnum, option in zip(indkey, indoption, strict=True):
                name = conn.execute(sa.text(
                    "SELECT attname FROM pg_attribute "
                    "WHERE attrelid = :oid AND attnum = :n"
                ), {"oid": table_oid, "n": attnum}).scalar_one()
                out[name] = bool(option & 1)   # bit 0 is DESC — NOT == 1
    finally:
        engine.dispose()
    return out


def _dump_schema(pg_admin, dbname: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, trusted testcontainer id
        ["docker", "exec", "-e", f"PGPASSWORD={pg_admin.password}",
         pg_admin.container_id, "pg_dump", "-U", pg_admin.user, "-d", dbname,
         "--schema-only", "--no-owner", "--no-privileges"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _normalise(dump: str) -> str:
    """Strip the SET/version-comment/`\\connect` preamble and sort the remaining
    statements' lines — a stable, order-insensitive shape (design/20 §2)."""
    keep = []
    for line in dump.splitlines():
        s = line.strip()
        if not s or s.startswith("--") or s.startswith("SET ") \
                or s.startswith("SELECT pg_catalog.set_config") or s.startswith("\\"):
            continue
        keep.append(s)
    return "\n".join(sorted(keep))


def _dbname_of(pg_url: str) -> str:
    return sa.engine.make_url(pg_url).database or ""


# ── the gate ─────────────────────────────────────────────────────────────────

@pytest.fixture
def migrated(pg_url) -> str:
    run_migrations(pg_url)
    return pg_url


@pytest.mark.parametrize("table", sorted(METADATA.tables))
def test_every_table_in_the_metadata_exists(migrated, table):
    engine = sa.create_engine(sync_url_for(migrated))
    try:
        with engine.connect() as conn:
            present = conn.execute(sa.text(
                "SELECT to_regclass(:t) IS NOT NULL"
            ), {"t": table}).scalar_one()
    finally:
        engine.dispose()
    assert present, f"{table} missing from the migrated database"


def test_the_conversation_type_check_constraint_is_present(migrated):
    """alembic check is blind to this — proven for the SQLite gate, and the
    Postgres one asserts it from pg_constraint directly."""
    defs = _check_constraint_defs(sync_url_for(migrated), "messages")
    assert any("conversation_type" in d for d in defs), defs


@pytest.mark.parametrize("index,expected", sorted(EXPECTED_DESC_INDEXES.items()))
def test_desc_index_ordering_from_indoption(migrated, index, expected):
    """`ts DESC` read from the indoption DESC bit, not information_schema (which
    cannot see it) and not `== 1` (a DESC NULLS FIRST column is 3)."""
    orders = _index_column_orders(sync_url_for(migrated), index)
    assert orders == expected


def _metadata_diffs(sync: str) -> list:
    """`compare_metadata` between `schema.py`'s METADATA and a live database.

    The `include_object` filter mirrors `migrations/env.py`'s, but derives its
    exclusion set from `EXPECTED_DESC_INDEXES` above rather than importing
    env.py — that module reads `context.config` at import time and cannot be
    imported outside an alembic run. Deriving it from the dict is better than a
    second hardcoded copy anyway: the indexes this comparison skips are exactly
    the ones `test_desc_index_ordering_from_indoption` asserts the ordering of,
    so nothing is dropped, only checked by a sibling. If env.py's set and this
    dict ever disagree, the extra index shows up here as phantom drift — a loud
    failure saying the two lists parted company, which is the right way to find
    out.
    """
    def include_object(obj, name, type_, reflected, compare_to) -> bool:
        return not (type_ == "index" and name in EXPECTED_DESC_INDEXES)

    engine = sa.create_engine(sync)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "target_metadata": METADATA,
                    "include_object": include_object,
                    # Both default to False. Without them this degrades to
                    # presence-only and would not catch the very drift it is
                    # here for.
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            return list(compare_metadata(ctx, METADATA))
    finally:
        engine.dispose()


def _diff_kinds(diffs: list) -> set[str]:
    """The operation names in a `compare_metadata` result.

    Its return shape is not flat, which is easy to get wrong and did get wrong
    here first: table-level operations arrive as bare tuples
    (`('add_table', Table(...))`), while COLUMN-level ones arrive grouped one
    list per table (`[[('modify_type', None, 'sticker_index', 'size', …)]]`).
    Iterating naively yields a `list` where a tuple was expected and the
    operation name is never seen — which reads as "no drift detected" against
    a database that has drifted. Flatten one level before looking.
    """
    kinds: set[str] = set()
    for entry in diffs:
        for op in (entry if isinstance(entry, list) else [entry]):
            if isinstance(op, tuple) and op:
                kinds.add(str(op[0]))
    return kinds


def test_the_metadata_matches_the_migrated_database(migrated):
    """`schema.py` and the migrations agree, column for column.

    THE GAP THIS CLOSES. Every other assertion in this file compares the live
    database against something else derived from the migrations —
    `test_every_table_in_the_metadata_exists` checks a table EXISTS and never
    looks at its columns, and the dump/baseline test compares a `pg_dump`
    against a checked-in file regenerated from that same dump. `schema.py`
    appears in neither loop. So a `sa.Column("size", sa.Integer)` in the
    metadata against a `TEXT` column in the migration was invisible: four tests
    blind to it, and the fifth satisfied by regenerating the file it compares
    against.

    That mattered because `METADATA` is what the ORM issues queries through at
    runtime. A type it is wrong about is a query built on a false premise, not
    a documentation nit.

    `alembic check` is the tool for exactly this — CONVENTIONS.md says so in as
    many words ("guards column presence, column type and server defaults") —
    and it was dropped from the gate because it is blind to the CHECK
    constraint and the DESC ordering, which the tests above now cover. The two
    were always complementary; only one half got kept. This restores the other
    half using the same autogenerate comparison, in-process, without reopening
    the phantom-drift problem that made the CLI unusable here.
    """
    assert _metadata_diffs(sync_url_for(migrated)) == [], (
        "nora/infra/sql/schema.py disagrees with the migrated database — the "
        "ORM would issue queries against a shape the database does not have. "
        "Fix the migration or the metadata; do NOT regenerate the baseline, "
        "which cannot see this."
    )


def test_normalised_pg_dump_matches_the_checked_in_baseline(migrated, pg_admin):
    """metadata+baseline parity. The baseline is regenerated with
    `scripts/gen_pg_baseline` if the schema legitimately changes."""
    live = _normalise(_dump_schema(pg_admin, _dbname_of(migrated)))
    assert live == BASELINE.read_text().rstrip("\n"), (
        "schema drifted from tests/infra/pg_schema_baseline.sql — regenerate it "
        "deliberately if this change is intended"
    )


def test_a_deliberate_column_type_drift_is_caught(pg_url, pg_admin):
    """The instrument check for the test above, in this file's own idiom.

    `test_a_deliberate_drift_is_caught` exists because a gate that has never
    been shown to fail is not evidence. The metadata comparison needs the same
    proof, and it needs it on the specific axis the dump/baseline test cannot
    see: a column whose TYPE diverges from `schema.py`.

    `sticker_index.size` is `sa.Integer` in the metadata. Alter it to `text` in
    a scratch database and the comparison must report a `modify_type` for it —
    if it reports nothing, `compare_type` is not doing what the opts claim and
    the new test is decoration.
    """
    dbname = "t_" + uuid.uuid4().hex
    import psycopg
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    sync = (
        f"postgresql+psycopg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    async_url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    try:
        run_migrations(async_url)
        # Precondition: undrifted, the comparison is silent. Without this the
        # test could pass on a database that was never in agreement.
        assert _metadata_diffs(sync) == []

        engine = sa.create_engine(sync)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "ALTER TABLE sticker_index ALTER COLUMN size TYPE text"
                ))
        finally:
            engine.dispose()

        diffs = _metadata_diffs(sync)
        assert "modify_type" in _diff_kinds(diffs), (
            f"compare_type did not report the integer->text drift; got {diffs!r}"
        )
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


def test_a_deliberate_drift_is_caught(pg_url, pg_admin):
    """The probe design/20 §2 demands: drop the CHECK and flip the index to ASC,
    then confirm the two catalogue reads the gate relies on both report the
    drift — i.e. reverting the schema this way turns the gate red."""
    dbname = "t_" + uuid.uuid4().hex
    import psycopg
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    sync = (
        f"postgresql+psycopg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    async_url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    try:
        run_migrations(async_url)
        # Precondition: undrifted, the gate's two assertions hold.
        assert any("conversation_type" in d
                   for d in _check_constraint_defs(sync, "messages"))
        assert _index_column_orders(sync, "idx_messages_conv_ts")["ts"] is True

        engine = sa.create_engine(sync)
        try:
            with engine.begin() as conn:
                name = conn.execute(sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'messages'::regclass AND contype = 'c'"
                )).scalar_one()
                conn.execute(sa.text(f'ALTER TABLE messages DROP CONSTRAINT "{name}"'))
                conn.execute(sa.text("DROP INDEX idx_messages_conv_ts"))
                conn.execute(sa.text(
                    "CREATE INDEX idx_messages_conv_ts "
                    "ON messages (conversation_id, ts ASC)"
                ))
        finally:
            engine.dispose()

        # Both gate reads now report the drift → the gate would fail.
        assert not any("conversation_type" in d
                       for d in _check_constraint_defs(sync, "messages"))
        assert _index_column_orders(sync, "idx_messages_conv_ts")["ts"] is False
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
