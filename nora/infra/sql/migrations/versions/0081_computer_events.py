"""computer_events — the computer audit log, two rows (started, finished) per
computer tool call, on every path (design/sandbox-runtime §12, DEC-243).

The operator's requirement is a COMPLETE trail with Postgres as the record
of truth: who (`actor`, `requester_person_id`, the room, the tag), when
(`started_at`, `finished_at`, `created_at`), how (`tool`, `path`, the OTel
`trace_id`/`span_id`, a linked debug view only) and what (`request`, the full
input; `result`; `output_ref`, the full output's keys in the blob store).

TWO ROWS PER OPERATION, linked by `operation_id`: a `started` row written
BEFORE the call reaches the computer (the full request, who, when, the
trace), and a `finished` row after it (result, output_ref, `finished_at`),
written even when the call is cancelled (status `cancelled`). The tools
refuse to run a call whose `started` row could not be written. A `started`
row with no `finished` row is therefore the visible mark of a crash or a
restart mid-call. `(operation_id, phase)` is unique.

APPEND-ONLY AGAINST BUGS, NOT AGAINST THE OWNER. `computer_events_refuse_change`
raises on any UPDATE or DELETE, and on a TRUNCATE (which fires no row
trigger) unless the session set `nora.row_history = 'off'`, the switch
`0068_system_time_history`'s guard honours so a test fixture that resets
every table keeps working. That stops a code path or a mistaken statement
from rewriting what ran. It does NOT stop a deliberate actor: any role can
set `nora.row_history`, and the table's owner (the role Nora connects as)
can drop the trigger. Tamper-proofing against the owner would need a
separate role or an external copy; that is not built.
`nora.infra.sql.history.APPEND_ONLY` names its system time, `created_at`.

Revision ID: 0081_computer_events
Revises: 0080_cognition_outcome_cause
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0081_computer_events"
down_revision = "0080_cognition_outcome_cause"
branch_labels = None
depends_on = None

_REFUSE_CHANGE = """
CREATE FUNCTION computer_events_refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'TRUNCATE' AND current_setting('nora.row_history', true) = 'off' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION USING
        MESSAGE = 'computer_events is append-only: ' || TG_OP || ' refused',
        ERRCODE = 'insufficient_privilege';
END
$fn$
"""


def upgrade() -> None:
    op.create_table(
        "computer_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("requester_person_id", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("trace_id", sa.Text, nullable=False),
        sa.Column("span_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "request", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_ref", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "actor IN ('cognition', 'fire', 'operator', 'tag') "
            "OR (starts_with(actor, 'dreamer:') AND length(actor) > 8)",
            name="ck_computer_events_actor",
        ),
        sa.CheckConstraint(
            "path IN ('direct', 'subagent')", name="ck_computer_events_path",
        ),
        sa.CheckConstraint(
            "phase IN ('finished', 'started')", name="ck_computer_events_phase",
        ),
        sa.CheckConstraint(
            "(phase = 'started' AND finished_at IS NULL) OR "
            "(phase = 'finished' AND finished_at IS NOT NULL "
            "AND finished_at >= started_at)",
            name="ck_computer_events_phase_times",
        ),
        sa.UniqueConstraint(
            "operation_id", "phase", name="uq_computer_events_operation_phase",
        ),
    )
    op.create_index(
        "idx_computer_events_conv_ts", "computer_events",
        ["conversation_id", "created_at"],
    )
    op.execute(_REFUSE_CHANGE)
    op.execute(
        "CREATE TRIGGER computer_events_append_only BEFORE UPDATE OR DELETE "
        "ON computer_events FOR EACH ROW EXECUTE FUNCTION computer_events_refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER computer_events_no_truncate BEFORE TRUNCATE "
        "ON computer_events FOR EACH STATEMENT "
        "EXECUTE FUNCTION computer_events_refuse_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS computer_events_no_truncate ON computer_events")
    op.execute("DROP TRIGGER IF EXISTS computer_events_append_only ON computer_events")
    op.execute("DROP FUNCTION IF EXISTS computer_events_refuse_change()")
    op.drop_index("idx_computer_events_conv_ts", table_name="computer_events")
    op.drop_table("computer_events")
