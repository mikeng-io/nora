"""computer_events — the computer audit log, one row per computer tool call
on every return path (design/sandbox-runtime §12, DEC-243).

The operator's requirement is a COMPLETE trail with Postgres as the record
of truth: who (`actor`, `requester_person_id`, the room, the tag), when
(`started_at`, `finished_at`, `created_at`), how (`tool`, `path`, the OTel
`trace_id`/`span_id`, a linked debug view only) and what (`request`, the full
input; `result`; `output_ref`, the full output's keys in the blob store).

APPEND-ONLY IN THE DATABASE, not by convention: `computer_events_refuse_change`
raises on any UPDATE or DELETE, so no code path, operator session or future
bug can rewrite what ran. A TRUNCATE (which fires no row trigger) is refused
too, unless the session set `nora.row_history = 'off'` deliberately: the
same switch `0068_system_time_history`'s guard honours, so a test fixture
that resets every table keeps working and nothing else can.
`nora.infra.sql.history.APPEND_ONLY` names its system time, `created_at`.

Revision ID: 0080_computer_events
Revises: 0079_cognition_pace_remind
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0080_computer_events"
down_revision = "0079_cognition_pace_remind"
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
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
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
            "finished_at >= started_at",
            name="ck_computer_events_finished_after_started",
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
