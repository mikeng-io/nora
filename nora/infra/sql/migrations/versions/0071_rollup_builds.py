"""rollup_builds -- the Rollup as versioned builds of a pure projection of the
Record (design/cognition-loop/25-rollup.md §2, 10-tiers.md §3/§8,
70-migration.md §2 row 2 and §5, the rows marked 2).

WHAT THIS ADDS, all additive (no table dropped, no column dropped):

1. `rollup_build`: one row per build of the whole Rollup (a code version and
   its parameters). Exactly one is `current` (a partial unique index), which
   is the pointer every reader resolves; a superseded build keeps its rows
   and is never truncated. `rollup_fold_state`: per build and person, where
   the presence fold stands in the Record (the last event it consumed, its
   logical clock, the fixes it still holds undecided), so an incremental run
   resumes exactly where the last one stopped.
2. On the six presence Rollup tables (stays, trips, derived places,
   occupancy, rhythm, corridors): `build_id` (which build computed the row),
   `lineage` (10 §3: the `(kind, subject, origin room)` set the row rests on;
   a presence row rests on one person's fixes, `[["presence", <subject>,
   null]]`), and `recorded_at` (system time). `presence_stays.address`: the
   mode Life360 address of the stay's fixes, persisted so a place's label is
   a function of its stays instead of the one cycle that happened to see
   their fixes.
3. Five unit Rollup tables: `rollup_colocation`, `rollup_outing`,
   `rollup_daybook`, `rollup_tie`, `rollup_response` (25 §3), each with its
   build, unit, key, typed content, `lineage`, `valid_from_ms`/`valid_to_ms`,
   `as_of_ms` and `recorded_at`.
4. History, 1b's way (`0068_system_time_history`): every table above, and
   `presence_places` (the collector's saved places, replaced on each fetch),
   gets the `nora_row_history()` trigger and the TRUNCATE guard. An
   incremental run changes rows of the current build in place (a relink, a
   label, an occupancy bin); the trigger is what keeps each version. The
   existing rows are backfilled into `row_history` at this migration (their
   earlier versions were overwritten and are not recoverable), dated at this
   migration, `presence_places` at its own `updated_ms`.
5. `uq_stimulus_rollup_ref`: a Rollup stimulus is unique on `(unit, ref)`, as
   a `time` one already is, so a stimulus raised again by a later run (or by
   a rebuild recomputing the same row) is written once.

THE EXISTING ROWS become one build, `code_version = 'legacy'`, current:
every reader keeps reading exactly what it read before this migration until
the analytics process rebuilds (it does on start, because its code version
is not `legacy`) and switches the pointer; the legacy build is then
superseded and kept. A database with no presence Rollup rows gets no legacy
build (orphan occupancy or rhythm rows, which cannot exist without a place,
would fail the NOT NULL and stop the migration rather than be guessed).

Every covered table is LOCKed while it is backfilled and its trigger
attached: the presence collector and the analytics process keep writing
while `nora` restarts, and a write in between would be in no history.

>>> RE-POINT AT MERGE TIME. <<< Cut on `0069_cognition_signal`, re-parented
onto `0070_stimulus_rename` (#617) when that merged first. Step 2b (lineage filter and
`record_query`) adds a migration on another branch; whichever merges second
re-points onto the other. `uv run alembic heads` must then print exactly ONE
line (AGENTS.md, "Never assume your migration's parent is unclaimed").

Revision ID: 0071_rollup_builds
Revises: 0070_stimulus_rename
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0071_rollup_builds"
down_revision = "0070_stimulus_rename"
branch_labels = None
depends_on = None

#: The presence Rollup tables gaining `build_id`, `lineage` and `recorded_at`,
#: with the SQL naming the subject their lineage rests on.
_PRESENCE_ROLLUP: dict[str, str] = {
    "presence_stays": "subject_id",
    "presence_trips": "subject_id",
    "presence_derived_places": "subject_id",
    "presence_occupancy": "subject_id",
    "presence_place_rhythm": "subject_id",
    "presence_corridors": "subject_id",
}

#: Every table this migration attaches the history trigger to, with the
#: columns that identify a row. Frozen here; `nora.infra.sql.history.
#: HISTORY_TABLES` is the live copy.
_HISTORY_TABLES: dict[str, tuple[str, ...]] = {
    "presence_places": ("circle_id", "place_id"),
    "presence_stays": ("id",),
    "presence_trips": ("id",),
    "presence_derived_places": ("id",),
    "presence_occupancy": ("id",),
    "presence_place_rhythm": ("subject_id", "place_id"),
    "presence_corridors": ("id",),
    "rollup_build": ("id",),
    "rollup_fold_state": ("build_id", "subject_id"),
    "rollup_colocation": ("id",),
    "rollup_outing": ("id",),
    "rollup_daybook": ("id",),
    "rollup_tie": ("id",),
    "rollup_response": ("id",),
}

#: When each backfilled row entered the store, as far as its columns say.
_BACKFILL_AT: dict[str, str] = {
    "presence_places": "to_timestamp(x.updated_ms / 1000.0)",
}


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def _rollup_columns() -> list[sa.Column[Any]]:
    """The columns every unit Rollup row carries (10 §3)."""
    return [
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("build_id", sa.BigInteger, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("lineage", pg.JSONB, nullable=False),
        sa.Column("valid_from_ms", sa.BigInteger, nullable=False),
        sa.Column("valid_to_ms", sa.BigInteger, nullable=False),
        sa.Column("as_of_ms", sa.BigInteger, nullable=False),
        _recorded_at(),
    ]


def _key_sql(columns: tuple[str, ...], alias: str = "x") -> str:
    pairs = ", ".join(f"'{c}', to_jsonb({alias}.\"{c}\")" for c in columns)
    return f"jsonb_build_object({pairs})"


def upgrade() -> None:
    existing = [t for t in _HISTORY_TABLES if t in _PRESENCE_ROLLUP or t == "presence_places"]
    op.execute(
        "LOCK TABLE " + ", ".join(f'"{t}"' for t in existing)
        + " IN SHARE ROW EXCLUSIVE MODE"
    )

    # 1. builds and the fold's resume point
    op.create_table(
        "rollup_build",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("code_version", sa.Text, nullable=False),
        sa.Column("params_sha256", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("through_ms", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.BigInteger, nullable=True),
        sa.Column("detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "status IN ('building', 'current', 'superseded', 'abandoned')",
            name="ck_rollup_build_status",
        ),
    )
    op.create_index(
        "uq_rollup_build_current", "rollup_build", ["status"], unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_table(
        "rollup_fold_state",
        sa.Column("build_id", sa.BigInteger, primary_key=True),
        sa.Column("subject_id", sa.Text, primary_key=True),
        sa.Column("pos_recorded_ms", sa.BigInteger, nullable=False),
        sa.Column("pos_rank", sa.Integer, nullable=False),
        sa.Column("pos_id", sa.BigInteger, nullable=False),
        sa.Column("logical_now_ms", sa.BigInteger, nullable=False),
        sa.Column("frontier_ts", sa.BigInteger, nullable=False),
        sa.Column(
            "window_fix_ids", pg.ARRAY(sa.Integer), nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
        sa.Column("tags_sha256", sa.Text, nullable=True),
        sa.Column("open_stay", pg.JSONB, nullable=True),
        sa.Column("late_fixes", sa.Integer, nullable=False, server_default=sa.text("0")),
        _recorded_at(),
    )
    # Only a database that HOLDS presence Rollup rows gets the legacy build: a
    # fresh one (a new deployment, an eval sandbox) starts with no build at
    # all, which is what `nora snapshot` requires of its target.
    op.execute(
        "INSERT INTO rollup_build (code_version, params_sha256, status, switched_at, detail) "
        "SELECT 'legacy', '', 'current', clock_timestamp(), "
        "'{\"note\": \"rows derived before migration 0071, by the wall-clock rollup\"}'::jsonb "
        "WHERE EXISTS (SELECT 1 FROM presence_stays) OR EXISTS (SELECT 1 FROM presence_trips) "
        "OR EXISTS (SELECT 1 FROM presence_derived_places)"
    )
    legacy = "(SELECT id FROM rollup_build WHERE code_version = 'legacy')"

    # 2. build, lineage and system time on the presence Rollup tables
    for table, subject in _PRESENCE_ROLLUP.items():
        op.add_column(table, sa.Column("build_id", sa.BigInteger, nullable=True))
        op.add_column(table, sa.Column("lineage", pg.JSONB, nullable=True))
        op.add_column(table, sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(
            f"UPDATE {table} SET build_id = {legacy}, "  # noqa: S608 — table and column names are module constants, never input
            f"lineage = jsonb_build_array(jsonb_build_array('presence', {subject}, NULL)), "
            "recorded_at = now()"
        )
        op.alter_column(table, "build_id", nullable=False)
        op.alter_column(table, "lineage", nullable=False)
        op.alter_column(
            table, "recorded_at", nullable=False,
            server_default=sa.text("clock_timestamp()"),
        )
    op.add_column("presence_stays", sa.Column("address", sa.Text, nullable=True))
    op.create_index(
        "idx_presence_stays_build_subject", "presence_stays",
        ["build_id", "subject_id", "started_ts"],
    )
    op.create_index(
        "idx_presence_trips_build_subject", "presence_trips",
        ["build_id", "subject_id", "started_ts"],
    )
    op.create_index(
        "idx_presence_derived_places_build_subject", "presence_derived_places",
        ["build_id", "subject_id"],
    )
    op.create_index(
        "idx_presence_corridors_build_subject", "presence_corridors",
        ["build_id", "subject_id"],
    )

    # 3. the unit rollups
    op.create_table(
        "rollup_colocation",
        *_rollup_columns(),
        sa.Column("persons", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("radius_m", sa.Float, nullable=False),
        sa.Column("place_label", sa.Text, nullable=True),
        sa.Column("saved_place_id", sa.Text, nullable=True),
        sa.Column(
            "at_home_of", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "homes_unknown", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("stay_count", sa.Integer, nullable=False),
        sa.Column("open", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "rollup_outing",
        *_rollup_columns(),
        sa.Column("persons", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("place_label", sa.Text, nullable=True),
        sa.Column("saved_place_id", sa.Text, nullable=True),
        sa.Column("open", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "rollup_daybook",
        *_rollup_columns(),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("local_date", sa.Text, nullable=False),
        sa.Column("stays", pg.JSONB, nullable=False),
        sa.Column("stay_ms", sa.BigInteger, nullable=False),
        sa.Column("home_ms", sa.BigInteger, nullable=False),
        sa.Column("colocation_ms", sa.BigInteger, nullable=False),
        sa.Column(
            "with_persons", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("outings", sa.Integer, nullable=False),
        sa.Column("rooms", pg.JSONB, nullable=False),
        sa.Column("messages", sa.Integer, nullable=False),
        sa.Column("reactions", sa.Integer, nullable=False),
        # Social items arrive at step 5; NULL is «not an input yet», never 0.
        sa.Column("items_posted", sa.Integer, nullable=True),
    )
    op.create_table(
        "rollup_tie",
        *_rollup_columns(),
        sa.Column("person_a", sa.Text, nullable=False),
        sa.Column("person_b", sa.Text, nullable=False),
        sa.Column("iso_week", sa.Text, nullable=False),
        sa.Column("replies_ab", sa.Integer, nullable=False),
        sa.Column("replies_ba", sa.Integer, nullable=False),
        sa.Column("mentions_ab", sa.Integer, nullable=False),
        sa.Column("mentions_ba", sa.Integer, nullable=False),
        sa.Column("reactions_ab", sa.Integer, nullable=False),
        sa.Column("reactions_ba", sa.Integer, nullable=False),
        sa.Column("colocation_ms", sa.BigInteger, nullable=False),
        sa.CheckConstraint("person_a < person_b", name="ck_rollup_tie_pair_order"),
    )
    op.create_table(
        "rollup_response",
        *_rollup_columns(),
        sa.Column("message_id", sa.Integer, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("sent_ts", sa.BigInteger, nullable=False),
        sa.Column("replies", sa.Integer, nullable=False),
        sa.Column("reactions", sa.Integer, nullable=False),
        sa.Column("edits", sa.Integer, nullable=False),
        sa.Column(
            "responders", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "named_persons", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        # The brief's intent (`tell`/`ask`) once step 3 records it on the
        # send; NULL is «not recorded», never «tell».
        sa.Column("intent", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.CheckConstraint(
            "outcome IN ('open', 'responded', 'silent', 'unanswered', 'unknown')",
            name="ck_rollup_response_outcome",
        ),
    )
    for table, key in (
        ("rollup_colocation", ["build_id", "unit", "valid_from_ms"]),
        ("rollup_outing", ["build_id", "unit", "valid_from_ms"]),
        ("rollup_daybook", ["build_id", "unit", "person_id", "local_date"]),
        ("rollup_tie", ["build_id", "unit", "iso_week"]),
        ("rollup_response", ["build_id", "unit", "message_id"]),
    ):
        op.create_index(f"idx_{table}_key", table, key)

    # 4. history for every table above
    for table, columns in _HISTORY_TABLES.items():
        at = _BACKFILL_AT.get(table, "now()")
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608 — table and column names are module constants, never input
            f"SELECT '{table}', {_key_sql(columns)}, 'backfill', to_jsonb(x), {at} "
            f"FROM \"{table}\" x"
        )
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON \"{table}\" FOR EACH ROW EXECUTE FUNCTION nora_row_history({args})"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"{table}\" "
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )

    # 5. a Rollup stimulus is written once per (unit, ref)
    op.create_index(
        "uq_stimulus_rollup_ref", "stimulus", ["unit", "ref"], unique=True,
        postgresql_where=sa.text("kind = 'rollup'"),
    )


def downgrade() -> None:
    # LOSSY, and said so: every build but the rows' own is dropped with its
    # tables, and the history of every write since this migration with them.
    op.drop_index("uq_stimulus_rollup_ref", table_name="stimulus")
    for table in _HISTORY_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS nora_row_history ON "{table}"')
        op.execute(f'DROP TRIGGER IF EXISTS nora_row_history_truncate ON "{table}"')
    op.execute(
        "DELETE FROM row_history WHERE table_name IN ("  # noqa: S608 — module constants, never input
        + ", ".join(f"'{t}'" for t in _HISTORY_TABLES) + ")"
    )
    for table in ("rollup_response", "rollup_tie", "rollup_daybook",
                  "rollup_outing", "rollup_colocation"):
        op.drop_index(f"idx_{table}_key", table_name=table)
        op.drop_table(table)
    for name, table in (
        ("idx_presence_corridors_build_subject", "presence_corridors"),
        ("idx_presence_derived_places_build_subject", "presence_derived_places"),
        ("idx_presence_trips_build_subject", "presence_trips"),
        ("idx_presence_stays_build_subject", "presence_stays"),
    ):
        op.drop_index(name, table_name=table)
    op.drop_column("presence_stays", "address")
    legacy = "(SELECT id FROM rollup_build WHERE code_version = 'legacy')"
    for table in _PRESENCE_ROLLUP:
        # Only the legacy build's rows existed before this migration.
        op.execute(f"DELETE FROM {table} WHERE build_id IS DISTINCT FROM {legacy}")  # noqa: S608 — table names are module constants, never input
        for column in ("recorded_at", "lineage", "build_id"):
            op.drop_column(table, column)
    op.drop_table("rollup_fold_state")
    op.drop_index("uq_rollup_build_current", table_name="rollup_build")
    op.drop_table("rollup_build")
