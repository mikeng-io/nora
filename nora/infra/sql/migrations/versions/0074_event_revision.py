"""event_revision -- the event migration: lifecycle statuses, per-field
lineage, `event_revision`, RSVP transition rows (design/cognition-loop/
70-migration.md §2 row 2b, §5 "Events."; 10-tiers.md §3, §8;
12-schemas.md §3; migration step 2b, DEC-237).

WHAT THIS ADDS, all additive:

1. `events` (the head, still updated in place — the compare-and-swap cache
   10-tiers.md §8 allows) gains:

   - `unit_id`: text, default `''`. Existing rows cannot be resolved to a
     unit from inside a migration (that needs `policy.toml`, which the
     database does not hold), so they backfill to `''` — a genuine gap this
     row records rather than hides: `nora.infra.sql.history`'s restore of a
     pre-2b event names no unit until an operator backfills one out of band.
     Every NEW event is unit-keyed from the write path (`_unit()`,
     `nora.agent.tools`, the same resolver notes already share).
   - `end`, `people`: 12-schemas.md §3's remaining event columns
     (`what`/`start` are `title`/`when`, already there; `place` is the
     existing `venue_*` group; `status` already there).
   - `reasoning`, `confidence`, `lineage`, `as_of`: the same four columns
     `0073_lineage` added to note/standing/culture, and the same
     simplification (row-level, NULL until a write verb states one) —
     10-tiers.md §3 asks for these PER FIELD; `lineage` gets that from day
     one (`nora.domain.lineage.Lineage` already sections by an arbitrary
     name, here the field: `what`, `start`, `end`, `place`, `people`,
     `status`), `reasoning`/`confidence` do not, matching DEC-236's own
     note that per-section reasoning/confidence waits on a writer that
     states one per section — no writer does yet; step 3 is the first.
   - `exclude_persons`: text[], same shape as every other derived row.
   - `head_revision_id`: the current `event_revision.id` (nullable only
     because it is added before that table exists; backfilled below, never
     null after this migration completes for a row that existed before it).
   - The status CHECK widens from `proposed`/`confirmed`/`cancelled` to the
     full lifecycle (40-register.md §3): `staged`, `proposed`, `confirmed`,
     `happened`, `cancelled`, `lapsed`, `evicted`.

2. `event_revision` (new): the append-only chain `events` is a
   compare-and-swap cache over, one full snapshot per revision, exactly
   `note_revision`'s shape (`parent_revision_id` chains back;
   `base_revision_id`, 10 §3's column, IS `parent_revision_id` read from the
   write side — the event verbs pass it as the revision they replace, which
   this migration's backfill sets to NULL for every row's first revision).

3. `event_rsvps` becomes append-only ("RSVP transition rows", 70 §5): its
   primary key moves from `(event_id, person_id)` to a new `id`, and it gains
   `recorded_at`. A repeat RSVP is now an INSERT, never an UPDATE — the
   CURRENT response is the newest row per `(event_id, person_id)`, which
   `nora.infra.sql.gatherings.SqlGatheringStore.rsvps_for` projects; storage
   itself no longer decides "current".

4. `events` moves from `nora.infra.sql.history.DEFERRED` into
   `HISTORY_TABLES` (this migration installs its `nora_row_history` trigger,
   same function `0068_system_time_history` created); `event_revision` and
   `event_rsvps` move into `APPEND_ONLY` (`recorded_at`).
   `tests/infra/test_row_history.py` is the guard: every table must be
   classified exactly once, and the migrated triggers must equal
   `HISTORY_TABLES` exactly — this migration is what makes that hold for
   `events`, which is why 70 §5's inventory marked it "2b" rather than
   folding it into `0068`.

THE BACKFILL. For every existing `events` row: one `event_revision`
(`parent_revision_id` NULL, `written_by='backfill'`, `written_at` =
`created_at`, the only time a pre-migration event proves) mirroring its
current columns, `reasoning`/`confidence`/`lineage`/`as_of` all left NULL/
empty (nothing before this migration ever stated one); then
`events.head_revision_id` is set to that revision's id. `event_rsvps.
recorded_at` backfills from `responded_at`, the only time a pre-migration
RSVP proves — a person's earlier RSVPs, upserted away before this migration,
are gone; only the row that survived the last upsert is here to backfill
from, same as `0068`'s "a row DELETED before today... cannot be restored"
for every in-place table's REPLACED value.

**Not built here** (recorded as this step's explicit scope, `70 §5`
"Events." leaves the how open): the cross-room de-duplication of legacy
per-room events into one unit-wide row ("rows in different rooms of one unit
with overlapping times and the same place merge... other candidates are
listed for the operator, never merged by guess") is a one-time historical
reconciliation this repo has no unit key to run yet (see `unit_id` above);
it is deferred to an operator-run backfill once units are populated, named
in DEC-237.

>>> RE-POINT AT MERGE. <<< `down_revision` is `origin/master`'s head when
this branch was cut (`0073_lineage`). `uv run alembic heads` must print
exactly ONE line (AGENTS.md, "Never assume your migration's parent is
unclaimed").

Revision ID: 0074_event_revision
Revises: 0073_lineage
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0074_event_revision"
down_revision = "0073_lineage"
branch_labels = None
depends_on = None

#: 40-register.md §3, frozen here (a migration is a frozen historical
#: statement, `0053_decision_log`'s rule); `nora.infra.sql.schema` is the
#: live copy.
_LIFECYCLE = (
    "staged", "proposed", "confirmed", "happened", "cancelled", "lapsed", "evicted",
)
_OLD_LIFECYCLE = ("proposed", "confirmed", "cancelled")
_MODES = ("told", "deduced", "abduced")
_CONFIDENCES = ("low", "medium", "high")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute('LOCK TABLE "events", "event_rsvps" IN SHARE ROW EXCLUSIVE MODE')

    # 1. events: widen the lifecycle, add the 2b columns.
    op.drop_constraint("ck_events_status", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status", "events", f"status IN ({_in(_LIFECYCLE)})",
    )
    op.add_column(
        "events", sa.Column("unit_id", sa.Text, nullable=False, server_default=""),
    )
    op.add_column("events", sa.Column("end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("events", sa.Column(
        "people", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"),
    ))
    op.add_column("events", sa.Column("reasoning", sa.Text, nullable=True))
    op.add_column("events", sa.Column("confidence", sa.Text, nullable=True))
    op.add_column("events", sa.Column(
        "lineage", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
    ))
    op.add_column("events", sa.Column("as_of", sa.BigInteger, nullable=True))
    op.add_column("events", sa.Column(
        "exclude_persons", pg.ARRAY(sa.Text), nullable=False,
        server_default=sa.text("'{}'"),
    ))
    # No FK, deliberately (`note.head_revision_id`'s precedent): `create`
    # mints and inserts `events` and `event_revision` in one transaction,
    # each naming the other's id, so a hard FK either direction makes one of
    # the two inserts fail on the row the other has not written yet.
    op.add_column(
        "events", sa.Column("head_revision_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_events_reasoning_is_a_mode", "events",
        f"reasoning is null or reasoning in ({_in(_MODES)})",
    )
    op.create_check_constraint(
        "ck_events_confidence_is_a_level", "events",
        f"confidence is null or confidence in ({_in(_CONFIDENCES)})",
    )
    op.create_check_constraint(
        "ck_events_lineage_shape", "events", "jsonb_typeof(lineage) = 'object'",
    )
    op.create_index("idx_events_unit", "events", ["unit_id", "status"])

    # 2. event_revision: the chain.
    op.create_table(
        "event_revision",
        sa.Column(
            "id", pg.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "event_id", sa.Uuid, sa.ForeignKey("events.id"), nullable=False,
        ),
        sa.Column(
            "parent_revision_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("event_revision.id"), nullable=True,
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("when", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("venue_text", sa.Text, nullable=True),
        sa.Column("venue_place_id", sa.Text, nullable=True),
        sa.Column("venue_name", sa.Text, nullable=True),
        sa.Column("venue_address", sa.Text, nullable=True),
        sa.Column("venue_rating", sa.Float, nullable=True),
        sa.Column("venue_price_level", sa.Text, nullable=False, server_default=""),
        sa.Column("venue_maps_url", sa.Text, nullable=True),
        sa.Column(
            "people", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"),
        ),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("confidence", sa.Text, nullable=True),
        sa.Column(
            "lineage", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("as_of", sa.BigInteger, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("written_by", sa.Text, nullable=False, server_default="tag"),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(_LIFECYCLE)})", name="ck_event_revision_status",
        ),
        sa.CheckConstraint(
            f"reasoning is null or reasoning in ({_in(_MODES)})",
            name="ck_event_revision_reasoning_is_a_mode",
        ),
        sa.CheckConstraint(
            f"confidence is null or confidence in ({_in(_CONFIDENCES)})",
            name="ck_event_revision_confidence_is_a_level",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(lineage) = 'object'", name="ck_event_revision_lineage_shape",
        ),
    )
    op.create_index(
        "idx_event_revision_event", "event_revision", ["event_id", "recorded_at"],
    )

    # 3. the backfill: one revision per existing event, the head pointed at it.
    op.execute("""
        WITH inserted AS (
            INSERT INTO event_revision (
                event_id, parent_revision_id, status, title, "when", "end",
                venue_text, venue_place_id, venue_name, venue_address,
                venue_rating, venue_price_level, venue_maps_url,
                written_by, written_at, recorded_at
            )
            SELECT
                id, NULL, status, title, "when", "end",
                venue_text, venue_place_id, venue_name, venue_address,
                venue_rating, venue_price_level, venue_maps_url,
                'backfill', created_at, created_at
            FROM events
            RETURNING id, event_id
        )
        UPDATE events e SET head_revision_id = i.id
        FROM inserted i WHERE i.event_id = e.id
    """)

    # 4. event_rsvps: append-only. Old upsert PK -> a surrogate id; the
    # composite stays as a non-unique lookup index.
    op.add_column("event_rsvps", sa.Column(
        "id", pg.UUID(as_uuid=True), nullable=True,
        server_default=sa.text("gen_random_uuid()"),
    ))
    op.execute("UPDATE event_rsvps SET id = gen_random_uuid() WHERE id IS NULL")
    op.alter_column("event_rsvps", "id", nullable=False)
    op.add_column("event_rsvps", sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.execute("UPDATE event_rsvps SET recorded_at = responded_at")
    op.alter_column(
        "event_rsvps", "recorded_at", nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )
    op.drop_constraint("event_rsvps_pkey", "event_rsvps", type_="primary")
    op.create_primary_key("event_rsvps_pkey", "event_rsvps", ["id"])
    op.create_index(
        "idx_event_rsvps_event_person", "event_rsvps",
        ["event_id", "person_id", "recorded_at"],
    )

    # 5. history: `events` gains the trigger (it moves out of DEFERRED);
    # event_revision/event_rsvps are append-only, no trigger needed.
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"events\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"events\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
    # `events`' own history starts here, at what its columns prove now (the
    # `0068` backfill pattern): one version per row, dated at `created_at`.
    op.execute("""
        INSERT INTO row_history (table_name, row_key, op, image, recorded_at)
        SELECT 'events', jsonb_build_object('id', to_jsonb(x.id)),
               'backfill', to_jsonb(x), x.created_at
        FROM "events" x
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"events\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"events\"")
    op.execute("DELETE FROM row_history WHERE table_name = 'events'")

    op.drop_index("idx_event_rsvps_event_person", table_name="event_rsvps")
    op.drop_constraint("event_rsvps_pkey", "event_rsvps", type_="primary")
    op.create_primary_key(
        "event_rsvps_pkey", "event_rsvps", ["event_id", "person_id"],
    )
    op.drop_column("event_rsvps", "recorded_at")
    op.drop_column("event_rsvps", "id")

    op.drop_index("idx_event_revision_event", table_name="event_revision")
    op.drop_table("event_revision")

    op.drop_index("idx_events_unit", table_name="events")
    for check in (
        "ck_events_lineage_shape", "ck_events_confidence_is_a_level",
        "ck_events_reasoning_is_a_mode",
    ):
        op.drop_constraint(check, "events", type_="check")
    for column in (
        "head_revision_id", "exclude_persons", "as_of", "lineage", "confidence",
        "reasoning", "people", "end", "unit_id",
    ):
        op.drop_column("events", column)
    op.drop_constraint("ck_events_status", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status", "events", f"status IN ({_in(_OLD_LIFECYCLE)})",
    )
