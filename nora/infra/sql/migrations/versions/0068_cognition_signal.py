"""cognition_signal -- signals, the shadow wake log, reactions, deletes and
source health (design/cognition-loop/70-migration.md §2 step 1, 20-observe.md).

Five new tables, all append-only (10-tiers.md §8); nothing existing changes:

* `signal` -- one row per signal per unit (20 §3), `recorded_at` its system
  time; a `time` signal unique on `(unit, ref)` (partial unique index) so a
  daily floor raised on every tick is written once.
* `cognition_wake` -- every (in step 1, would-)wake with its reason and the
  signal range it would consume; a unit's newest row in a mode is its
  watermark in that mode.
* `message_reaction`, `message_delete` -- a reaction and a delete/unsend as
  Record items attached to the message they touch (20 §1), CASCADE on the
  message for `message_edit`'s reason.
* `source_health` -- presence collector polls and platform adapter
  transitions (20 §1), so «every source healthy for the interval» is a query.

THE VOCABULARIES ARE WRITTEN OUT rather than read from `nora.domain.signals`,
following `0053_decision_log`: a migration is a frozen historical statement.

>>> RE-POINT AT MERGE TIME. <<< `down_revision` is the head of `origin/master`
when this branch was cut (`0067_summons_message_key`). Migration step 1b is
being built in parallel and adds its own revision on the same parent;
whichever merges second re-points onto the other and renames nothing else.
Then `uv run alembic heads` must print exactly ONE line (AGENTS.md, "Never
assume your migration's parent is unclaimed").

Revision ID: 0068_cognition_signal
Revises: 0067_summons_message_key
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0068_cognition_signal"
down_revision = "0067_summons_message_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_reaction",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "message_id", sa.Integer,
            sa.ForeignKey("messages.id", name="message_reaction_message_id_fkey",
                          ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_id", sa.Text, nullable=False),
        sa.Column("person_id", sa.Text),
        sa.Column("emoji", sa.Text, nullable=False),
        sa.Column("removed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("ts", sa.BigInteger, nullable=False),
        sa.Column("raw_json", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False,
                  server_default=sa.text("now()::text")),
    )
    op.create_index("idx_message_reaction_message", "message_reaction", ["message_id"])
    op.create_table(
        "message_delete",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "message_id", sa.Integer,
            sa.ForeignKey("messages.id", name="message_delete_message_id_fkey",
                          ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_id", sa.Text, nullable=False),
        sa.Column("person_id", sa.Text),
        sa.Column("ts", sa.BigInteger, nullable=False),
        sa.Column("raw_json", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False,
                  server_default=sa.text("now()::text")),
    )
    op.create_index("idx_message_delete_message", "message_delete", ["message_id"])

    op.create_table(
        "signal",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("item", sa.Text, nullable=False),
        sa.Column("ref", sa.Text, nullable=False),
        sa.Column("platform", sa.Text),
        sa.Column("conversation_id", sa.Text),
        sa.Column("person_id", sa.Text),
        sa.Column("cues", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("waits", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("at_ms", sa.BigInteger, nullable=False),
        sa.Column("due_ms", sa.BigInteger),
        sa.Column("recorded_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "kind IN ('message', 'social', 'play', 'rollup', 'time', 'self')",
            name="ck_signal_kind",
        ),
    )
    op.create_index("idx_signal_unit_id", "signal", ["unit", "id"])
    op.create_index(
        "uq_signal_time_ref", "signal", ["unit", "ref"], unique=True,
        postgresql_where=sa.text("kind = 'time'"),
    )

    op.create_table(
        "cognition_wake",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("from_signal_id", sa.BigInteger, nullable=False),
        sa.Column("through_signal_id", sa.BigInteger, nullable=False),
        sa.Column("signal_count", sa.Integer, nullable=False),
        sa.Column("eligible_count", sa.Integer, nullable=False),
        sa.Column("oldest_pending_ms", sa.BigInteger),
        sa.Column("cut_cooldown", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("detail", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("at_ms", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("mode IN ('shadow')", name="ck_cognition_wake_mode"),
        sa.CheckConstraint(
            "reason IN ('settled', 'strong_cue', 'rollup', 'batch', 'max_age', "
            "'time', 'self')",
            name="ck_cognition_wake_reason",
        ),
    )
    op.create_index(
        "idx_cognition_wake_unit_mode_id", "cognition_wake", ["unit", "mode", "id"],
    )

    op.create_table(
        "source_health",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("at_ms", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("event IN ('poll', 'transition')", name="ck_source_health_event"),
    )
    op.create_index(
        "idx_source_health_source_at", "source_health", ["source", "at_ms"],
    )


def downgrade() -> None:
    op.drop_index("idx_source_health_source_at", table_name="source_health")
    op.drop_table("source_health")
    op.drop_index("idx_cognition_wake_unit_mode_id", table_name="cognition_wake")
    op.drop_table("cognition_wake")
    op.drop_index("uq_signal_time_ref", table_name="signal")
    op.drop_index("idx_signal_unit_id", table_name="signal")
    op.drop_table("signal")
    for table in ("message_delete", "message_reaction"):
        op.drop_index(f"idx_{table}_message", table_name=table)
        op.drop_table(table)
