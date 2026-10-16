"""The schema gate (DEC-11 / DEC-53), rebuilt on PostgreSQL catalogs.

`alembic check` guards column presence, column type and server defaults. It is
provably blind to the CHECK constraint and to the `ts DESC` index ordering, and
it reports permanent phantom drift on the expression index. This file is what
actually guarantees the schema, so it reads Postgres's own catalogues rather
than SQLAlchemy's opinion of them.

**Oracle scope.** The baseline is a checked-in, normalised `pg_dump`
(`pg_schema_baseline.sql`). A frozen dump catches drift *from that baseline* —
it is metadata+baseline parity. It does **not** reach live production; the
`nora` database publishes no host port, so the live-production half is a
`docker exec nora-db pg_dump` job, named here rather than silently dropped.

It still asserts, from the catalogue and independent of the baseline:
  - the CHECK constraint `conversation_type IN ('group','dm')` is present;
  - `idx_messages_conv_ts` is `(conversation_id, ts DESC)`, the descending flag
    read from the per-column `indoption` DESC **bit** (`indoption[i] & 1`), NOT
    equality to 1 (a `DESC NULLS FIRST` column is `1|2 = 3`, so `== 1` false-
    fails), and NOT `information_schema` (which does not expose index ordering);
  - **every** `sa.CheckConstraint` in `schema.py` is on the migrated table with
    the same predicate, and the table carries no CHECK `schema.py` is silent
    about — see `test_the_metadata_check_constraints_match_the_migrated_database`
    and the honesty note below.

**The CHECK constraints are enforced, not documentation.** The bullet above
covers one constraint by name; the rest are covered by the CHECK comparison
against `schema.py` (`_check_constraint_problems`).
`compare_metadata` is blind to CHECKs (this file's opening paragraph says so),
and the dump/baseline test compares the migrated database against a file
regenerated from that same database — `schema.py` is in neither loop, so
deleting a CHECK from `schema.py` alone, migrations untouched, would leave
everything else green.

Parameterised over every table in `nora`'s metadata, so a new table's CHECKs
and existence need no new test, and its alembic-check-blind DESC index needs
only a row in `EXPECTED_DESC_INDEXES`.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from nora.infra.sql.migrations_runner import run_migrations, sync_url_for
from nora.infra.sql.schema import METADATA
from tests.conftest import PG_IMAGE

BASELINE = Path(__file__).resolve().parent / "pg_schema_baseline.sql"

# Per (table, index): column -> is_descending. The gate asserts the live
# `indoption` DESC bit matches. A new table adds its own row here; the
# CHECK/existence checks below need no editing because they iterate
# `METADATA`.
EXPECTED_DESC_INDEXES = {
    "idx_messages_conv_ts": {"conversation_id": False, "ts": True},
    # presence_fixes: the alembic-check-blind `(circle_id, person_id, ts DESC)`
    # index.
    "idx_presence_member_ts": {"circle_id": False, "person_id": False, "ts": True},
    # tool_call_log: the alembic-check-blind `(conversation_id, created_at DESC)`
    # index.
    "idx_tool_call_log_conv_ts": {"conversation_id": False, "created_at": True},
    # presence_stays (DEC-115): the alembic-check-blind
    # `(subject_id, started_ts DESC)` index.
    "idx_presence_stays_subject_ts": {"subject_id": False, "started_ts": True},
    "idx_presence_derived_places_subject_kind": {"subject_id": False, "kind": False},
    # presence_trips: the alembic-check-blind `(subject_id, started_ts DESC)`
    # index.
    "idx_presence_trips_subject_started": {"subject_id": False, "started_ts": True},
    # standing_revision: `(platform, conversation_id, updated_at DESC,
    # id DESC)` — the backwards scan the as-of read makes. Two DESC columns
    # rather than one, so this is also the row that proves the `indoption` bit
    # check reads PER COLUMN rather than taking the index's first answer for
    # all of them.
    "idx_standing_revision_room_updated": {
        "platform": False, "conversation_id": False,
        "updated_at": True, "id": True,
    },
    # memory_node: the embedding worker's pick, fewest failures and
    # then newest, `(embed_attempts, id DESC)`, the order `embed_pick` asks.
    "idx_memory_node_unembedded": {"embed_attempts": False, "id": True},
}


# Per expression index: what `pg_get_indexdef` must say. Autogenerate cannot
# compare an expression index at all (it reports the reflected one as a
# phantom `remove_index` on every run), so these are excluded from it here and
# in `migrations/env.py`, and their definition is asserted from the catalogue
# instead. Both registrations or neither, as for the DESC indexes above.
EXPECTED_EXPRESSION_INDEXES = {
    # memory_node vectors: HNSW over the leading 256 of the 2048 dimensions,
    # cosine.
    "idx_memory_node_embedding_256": (
        "CREATE INDEX idx_memory_node_embedding_256 ON public.memory_node USING hnsw "
        "(((subvector(embedding, 1, 256))::halfvec(256)) halfvec_cosine_ops)"
    ),
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


# The constraint name the metadata-side probe borrows for the length of one
# rolled-back SAVEPOINT. Nothing is ever committed under it.
_PROBE_CONSTRAINT = "zz_schema_parity_probe"


def _actual_check_defs(conn: sa.Connection, table: str) -> dict[str, str]:
    """Postgres's own text for every CHECK on `table`, keyed by constraint name."""
    return dict(conn.execute(sa.text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"
    ), {"t": table}).all())


def _declared_checks(table: str) -> list[sa.CheckConstraint]:
    """`schema.py`'s CHECKs for `table`, named ones first.

    The order is load-bearing. An UNNAMED `CheckConstraint` has no name to match
    on and can only claim a database constraint by predicate; doing the named
    ones first means every constraint that has a name has already claimed its
    own row, so the unnamed one cannot accidentally claim a namesake's.
    """
    return sorted(
        (c for c in METADATA.tables[table].constraints
         if isinstance(c, sa.CheckConstraint)),
        key=lambda c: (c.name is None, str(c.name)),
    )


def _as_postgres_renders_it(
    conn: sa.Connection, table: str, constraint: sa.CheckConstraint
) -> str:
    """What the database would store for a CHECK that `schema.py` declares.

    **Why a round-trip and not a string compare.** Postgres does not keep the
    predicate you wrote; it keeps a parsed one and prints it back its own way —
    `model IS NULL OR length(btrim(model)) > 0` returns as
    `CHECK (((model IS NULL) OR (length(btrim(model)) > 0)))`, and an `IN` list
    returns as `= ANY (ARRAY[...::text])`. Diffing `schema.py`'s spelling
    against that compares two renderings of the same predicate and calls them
    drift. Hand-normalising one side is worse: it is a small SQL parser written
    in a test, and every expression it gets wrong is a false failure that
    eventually gets the test deleted.

    So both sides are rendered by the same renderer. The declared constraint is
    added to the REAL table under a probe name inside a SAVEPOINT that is always
    rolled back, and read back with `pg_get_constraintdef` — the identical call
    the database side uses. What remains after that is a difference in the
    predicate, not in how it was typed.

    `exec_driver_sql`, not `sa.text`: a declared expression may legitimately
    contain a `::text` cast, which `sa.text` would parse as a `:text`
    bindparam and refuse to execute. No parameters are passed, so psycopg
    performs no `%` interpolation either.
    """
    savepoint = conn.begin_nested()
    try:
        conn.exec_driver_sql(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{_PROBE_CONSTRAINT}" '
            f"CHECK ({constraint.sqltext})"
        )
        return conn.execute(sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass) AND conname = :n"
        ), {"t": table, "n": _PROBE_CONSTRAINT}).scalar_one()
    finally:
        savepoint.rollback()


def _check_constraint_problems(
    conn: sa.Connection, table: str, declared: list[sa.CheckConstraint]
) -> list[str]:
    """Every way `declared` and `table`'s live CHECKs disagree, in plain words.

    A list rather than an assertion so the gate can report all of them at once,
    and so `test_the_check_constraint_comparison_reports_drift_in_both_directions`
    can run the comparison against a `declared` it doctors — proving the
    comparison fails when it should without editing `schema.py`.

    **The rule, both directions, no allow-list.** A declared CHECK the database
    does not have is drift: the ORM's model of the table claims an invariant
    nothing enforces. A CHECK the database has and `schema.py` does not declare
    is *also* drift, for the mirror reason — `schema.py` calls itself "the
    single source of truth for Nora's tables", so an invariant absent from it is
    one a contributor reading it cannot know they are writing against. There is
    no allow-list because there is nothing to allow. A constraint that
    genuinely belongs in a migration and not in the metadata should argue for
    itself in review, not slip past a list.

    Names are compared **as well as** predicates, for every constraint that
    declares one: migrations drop constraints BY NAME
    (`op.drop_constraint(f"{table}_model_check", ...)`), so a name that agrees
    nowhere is a downgrade that fails. The two CHECKs `schema.py` leaves unnamed
    are matched on predicate alone — Postgres generates their names, and
    asserting the generated form would encode a naming rule SQLAlchemy never
    applied.
    """
    actual = _actual_check_defs(conn, table)
    unclaimed = dict(actual)
    problems: list[str] = []

    for constraint in declared:
        rendered = _as_postgres_renders_it(conn, table, constraint)
        if constraint.name is None:
            claimed = next(
                (n for n, d in sorted(unclaimed.items()) if d == rendered), None
            )
            if claimed is None:
                problems.append(
                    f"{table}: schema.py declares an unnamed {rendered}, and no "
                    f"CHECK on the migrated table has that predicate — the "
                    f"migration never built it."
                )
            else:
                del unclaimed[claimed]
            continue
        if constraint.name not in actual:
            problems.append(
                f"{table}: schema.py declares CHECK {constraint.name!r}, but the "
                f"migrated table has no constraint of that name — nothing "
                f"enforces {rendered}. Add it in a migration, or delete the "
                f"declaration."
            )
            continue
        del unclaimed[constraint.name]
        if actual[constraint.name] != rendered:
            problems.append(
                f"{table}: CHECK {constraint.name!r} means something different "
                f"in each place.\n"
                f"        schema.py: {rendered}\n"
                f"        database : {actual[constraint.name]}"
            )

    problems.extend(
        f"{table}: the migrated table has CHECK {name!r} {definition}, which "
        f"schema.py does not declare — declare it in nora/infra/sql/schema.py, "
        f"or drop it in a migration."
        for name, definition in sorted(unclaimed.items())
    )
    return problems


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
    statements' lines — a stable, order-insensitive shape."""
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


def test_the_test_container_tracks_the_deployment():
    """The suite's Postgres MAJOR VERSION must match the one production runs.

    This file is the gate every migration passes through. A schema gate
    validating against a different major version than the one deployed is an
    instrument that cannot measure the thing it guards.

    Compares MAJOR versions only. A patch bump in compose is a deployment
    decision and must not turn the suite red; a MAJOR bump changes what
    `pg_dump` emits and what `compare_metadata` sees, and must.
    """
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    # Matches `postgres:18-alpine` AND `pgvector/pgvector:pg18`: a regex that
    # only knew the first would fail OPEN, asserting nothing while looking like
    # a gate. The major version is the group either way.
    deployed = re.search(
        r"image:\s*(?:postgres:(\d+)[\w.-]*|[\w./-]*pgvector[\w./-]*:pg(\d+))",
        compose,
    )
    assert deployed is not None, "no postgres image found in docker-compose.yml"
    deployed_major = deployed.group(1) or deployed.group(2)

    tested = re.search(r"(?:postgres:|:pg)(\d+)", PG_IMAGE)
    assert tested is not None, f"PG_IMAGE is not a postgres image: {PG_IMAGE!r}"

    assert tested.group(1) == deployed_major, (
        f"the test suite runs {PG_IMAGE} but the deployment runs major "
        f"version {deployed_major}. This gate's oracle must not be a major "
        f"version away from what it guards — bump tests/conftest.py's "
        f"PG_IMAGE and regenerate the baseline with "
        f"scripts/gen_pg_baseline.py."
    )


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
    """alembic check is blind to this, so it is asserted from pg_constraint
    directly."""
    defs = _check_constraint_defs(sync_url_for(migrated), "messages")
    assert any("conversation_type" in d for d in defs), defs


def test_the_metadata_check_constraints_match_the_migrated_database(migrated):
    """`schema.py`'s CHECKs are enforced, not annotated.

    THE GAP THIS CLOSES. `test_the_conversation_type_check_constraint_is_present`
    covers one CHECK by name. `compare_metadata` cannot see a CHECK, and the
    dump/baseline test compares the migrated database against a file
    regenerated from it, so `schema.py` is on neither side: without this test
    the declarations would be documentation of what the migrations built. A
    contributor who added a CHECK only to `schema.py` would be told nothing
    when it never reached the database; one who deleted a CHECK from
    `schema.py`, likewise. `METADATA` is what the ORM issues queries through,
    and an invariant it claims that the store does not hold is a premise, not a
    nit — the same reason `test_the_metadata_matches_the_migrated_database`
    exists for columns.

    Compares NAME and PREDICATE, in both directions, over every table in the
    metadata. See `_as_postgres_renders_it` for why the predicates are
    comparable at all, and `_check_constraint_problems` for the exact rule.
    """
    engine = sa.create_engine(sync_url_for(migrated))
    try:
        with engine.connect() as conn:
            problems = [
                problem
                for table in sorted(METADATA.tables)
                for problem in _check_constraint_problems(
                    conn, table, _declared_checks(table)
                )
            ]
    finally:
        engine.dispose()

    assert problems == [], (
        "nora/infra/sql/schema.py's CHECK constraints and the migrated database "
        "disagree. Fix the migration or the declaration; do NOT regenerate the "
        "baseline, which cannot see this.\n      - "
        + "\n      - ".join(problems)
    )


def test_the_check_constraint_comparison_reports_drift_in_both_directions(migrated):
    """The instrument check for the test above — a gate never shown to fail is
    not evidence, and this one has three ways to fail.

    The drift the gate guards against lives in a Python file, so it cannot be
    injected into a scratch database the way `test_a_deliberate_drift_is_caught`
    injects its own. It is injected into the DECLARED side instead: the same
    comparison, run against a `declared` list doctored three ways, must report
    each one.
    """
    engine = sa.create_engine(sync_url_for(migrated))
    try:
        with engine.connect() as conn:
            # Precondition: undrifted, `messages` is silent. Without this the
            # test could pass on a table that was never in agreement.
            assert _check_constraint_problems(
                conn, "messages", _declared_checks("messages")
            ) == []

            # 1. A CHECK deleted from `schema.py`. The database keeps it;
            #    nothing declares it.
            deleted = _check_constraint_problems(conn, "messages", [])
            assert len(deleted) == 1, deleted
            assert "schema.py does not declare" in deleted[0], deleted

            # 2. A CHECK added to `schema.py` that no migration ever built.
            invented = _check_constraint_problems(
                conn,
                "messages",
                [*_declared_checks("messages"),
                 sa.CheckConstraint("body IS NULL OR length(body) > 0",
                                    name="messages_invented_check")],
            )
            assert len(invented) == 1, invented
            assert "no constraint of that name" in invented[0], invented

            # 3. The subtle one: the right NAME over a different PREDICATE.
            #    Only the round-trip rendering can see this, and it is the case
            #    a names-only comparison would wave through.
            narrowed = _check_constraint_problems(
                conn,
                "room_lexeme",
                [c for c in _declared_checks("room_lexeme")
                 if c.name != "room_lexeme_model_check"]
                + [sa.CheckConstraint("model IS NULL OR length(model) > 2",
                                      name="room_lexeme_model_check")],
            )
            assert len(narrowed) == 1, narrowed
            assert "means something different in each place" in narrowed[0], narrowed
    finally:
        engine.dispose()


@pytest.mark.parametrize("index,expected", sorted(EXPECTED_DESC_INDEXES.items()))
def test_desc_index_ordering_from_indoption(migrated, index, expected):
    """`ts DESC` read from the indoption DESC bit, not information_schema (which
    cannot see it) and not `== 1` (a DESC NULLS FIRST column is 3)."""
    orders = _index_column_orders(sync_url_for(migrated), index)
    assert orders == expected


@pytest.mark.parametrize("index,expected", sorted(EXPECTED_EXPRESSION_INDEXES.items()))
def test_expression_index_definition_from_the_catalogue(migrated, index, expected):
    """An expression index autogenerate skips is asserted here, from the
    definition Postgres stores, and the metadata declares the same name."""
    engine = sa.create_engine(sync_url_for(migrated))
    try:
        with engine.connect() as conn:
            actual = conn.execute(sa.text(
                "SELECT pg_get_indexdef(c.oid) FROM pg_class c WHERE c.relname = :i"
            ), {"i": index}).scalar_one()
    finally:
        engine.dispose()
    assert actual == expected
    declared = {i.name for t in METADATA.tables.values() for i in t.indexes}
    assert index in declared


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
        return not (
            type_ == "index"
            and (name in EXPECTED_DESC_INDEXES or name in EXPECTED_EXPRESSION_INDEXES)
        )

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

    Its return shape is not flat, which is easy to get wrong: table-level
    operations arrive as bare tuples (`('add_table', Table(...))`), while
    COLUMN-level ones arrive grouped one list per table (`[[('modify_type',
    None, 'sticker_index', 'size', …)]]`). Iterating naively yields a `list`
    where a tuple was expected and the operation name is never seen — which
    reads as "no drift detected" against a database that has drifted. Flatten
    one level before looking.
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
    appears in neither loop, so a `sa.Column("size", sa.Integer)` in the
    metadata against a `TEXT` column in the migration would be invisible.

    That matters because `METADATA` is what the ORM issues queries through at
    runtime. A type it is wrong about is a query built on a false premise, not
    a documentation nit.

    `alembic check` is the tool for exactly this ("guards column presence,
    column type and server defaults"), but it is blind to the CHECK constraint
    and the DESC ordering, which the tests above cover. The two are
    complementary: this runs the same autogenerate comparison in-process,
    without the phantom-drift problem that makes the CLI unusable here.
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
    """The red-on-revert probe: drop the CHECK and flip the index to ASC,
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
