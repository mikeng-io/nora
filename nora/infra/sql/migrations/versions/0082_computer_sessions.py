"""computer_sessions — one task in progress on Nora's computer per room,
derived by code (design/sandbox-runtime §15.2, decision 5); and the
`computer_events` changes sessions need.

1. `computer_sessions`: opened by the first computer call in a room with no
   open session, continued by every later one, expired by the sweeper (a
   compare-and-set on `last_activity_at`). `status` is `open` or `expired`
   only: what a reader is shown (`active`, `idle`, `lapsed`) is derived.
   Progress is never stored; it is derived from the session's own audit rows.
   `uq_computer_sessions_open_scope` allows ONE open session per scope, which
   is what makes a call opening a session exactly-once under concurrency.
   It changes in place, so it gets `nora_row_history` (0068's function), and
   nothing deletes a row.
2. `computer_events.session_id`, nullable: every row written before this
   carries none. No foreign key: a fixture that resets every table
   truncates the two tables independently, and an append-only row must
   never be refused because of the other table's state.
3. `ck_computer_events_actor` gains `sweeper`, the actor of an expiry's
   audit row. `0081` is live and is not edited; the constraint is dropped
   and recreated here.

Revision ID: 0082_computer_sessions
Revises: 0081_computer_events
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0082_computer_sessions"
down_revision = "0081_computer_events"
branch_labels = None
depends_on = None

_ACTOR_TAIL = "OR (starts_with(actor, 'dreamer:') AND length(actor) > 8)"
_ACTORS_0081 = f"actor IN ('cognition', 'fire', 'operator', 'tag') {_ACTOR_TAIL}"
_ACTORS_0082 = f"actor IN ('cognition', 'fire', 'operator', 'sweeper', 'tag') {_ACTOR_TAIL}"


def upgrade() -> None:
    op.create_table(
        "computer_sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "stopped_at_detail", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_reason", sa.Text, nullable=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("created_by_tag", sa.Text, nullable=False),
        sa.CheckConstraint(
            "status IN ('expired', 'open')", name="ck_computer_sessions_status",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND expired_at IS NULL AND expired_reason IS NULL) OR "
            "(status = 'expired' AND expired_at IS NOT NULL AND expired_reason IS NOT NULL)",
            name="ck_computer_sessions_expiry",
        ),
        sa.CheckConstraint(
            "expired_reason IS NULL OR expired_reason IN "
            "('idle', 'jobs_ended', 'running_past_limit')",
            name="ck_computer_sessions_expired_reason",
        ),
        sa.CheckConstraint(_ACTORS_0082, name="ck_computer_sessions_actor"),
    )
    op.create_index(
        "uq_computer_sessions_open_scope", "computer_sessions", ["scope"],
        unique=True, postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "idx_computer_sessions_room", "computer_sessions",
        ["conversation_id", "last_activity_at"],
    )
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"computer_sessions\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"computer_sessions\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )

    op.add_column(
        "computer_events", sa.Column("session_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "idx_computer_events_session_ts", "computer_events", ["session_id", "created_at"],
    )
    op.drop_constraint("ck_computer_events_actor", "computer_events", type_="check")
    op.create_check_constraint("ck_computer_events_actor", "computer_events", _ACTORS_0082)


def downgrade() -> None:
    op.drop_constraint("ck_computer_events_actor", "computer_events", type_="check")
    op.create_check_constraint("ck_computer_events_actor", "computer_events", _ACTORS_0081)
    op.drop_index("idx_computer_events_session_ts", table_name="computer_events")
    op.drop_column("computer_events", "session_id")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"computer_sessions\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"computer_sessions\"")
    op.execute("DELETE FROM row_history WHERE table_name = 'computer_sessions'")
    op.drop_index("idx_computer_sessions_room", table_name="computer_sessions")
    op.drop_index("uq_computer_sessions_open_scope", table_name="computer_sessions")
    op.drop_table("computer_sessions")
