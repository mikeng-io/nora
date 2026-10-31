"""Durable platform-bridge inbox: event mapping, work obligations, actions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0141_bridge_inbox"
down_revision = "0140_notebook_entry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bridge_event",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_key", sa.Text, nullable=False),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("source_digest", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("stream", sa.Text, nullable=False),
        sa.Column("stream_id", sa.Text, nullable=False),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("sender_id", sa.Text, nullable=False),
        sa.Column("received_ms", sa.BigInteger, nullable=False),
        sa.Column("queued_ms", sa.BigInteger, nullable=False),
        sa.Column("time_quality", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("admission_rev", sa.Integer),
        sa.Column("admission", pg.JSONB),
        sa.Column("tag_id", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="open"),
        sa.Column("outcome_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "account_key", "source_digest",
                            name="uq_bridge_event_source"),
        sa.CheckConstraint(
            "state IN ('open','done','intentional_skip','failed_retained','uncertain')",
            name="ck_bridge_event_state"),
    )
    op.create_table(
        "bridge_event_alias",
        sa.Column("event_id", sa.Text, primary_key=True),
        sa.Column("event_ref", sa.Text, sa.ForeignKey("bridge_event.id"),
                  nullable=False),
    )
    op.create_index("idx_bridge_event_conv", "bridge_event",
                    ["platform", "account_key", "conversation_id", "seq"])
    op.create_index("idx_bridge_event_stream", "bridge_event",
                    ["stream", "stream_id"], unique=True)

    op.create_table(
        "bridge_work",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("event_id", sa.Text, sa.ForeignKey("bridge_event.id")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_key", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("result", pg.JSONB),
        sa.Column("lease_owner", sa.Text),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", "kind", "ordinal",
                            name="uq_bridge_work_obligation"),
        sa.CheckConstraint(
            "state IN ('pending','running','done','intentional_skip',"
            "'retryable_failure','failed_retained','uncertain','cancelled')",
            name="ck_bridge_work_state"),
    )
    op.create_index("idx_bridge_work_claim", "bridge_work", ["state", "due_at"])
    op.create_index("idx_bridge_work_conv", "bridge_work",
                    ["platform", "account_key", "conversation_id", "ordinal"])

    op.create_table(
        "bridge_action",
        sa.Column("action_id", sa.Text, primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_key", sa.Text, nullable=False),
        sa.Column("owner_kind", sa.Text, nullable=False),
        sa.Column("owner_id", sa.Text, nullable=False),
        sa.Column("owner_generation", sa.Integer),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("request", pg.JSONB, nullable=False),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="prepared"),
        sa.Column("platform_ref", sa.Text),
        sa.Column("platform_ms", sa.BigInteger),
        sa.Column("error", pg.JSONB),
        sa.Column("message_id", sa.BigInteger,
                  sa.ForeignKey("messages.id",
                                name="bridge_action_message_id_fkey")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('prepared','started','confirmed','rejected','uncertain')",
            name="ck_bridge_action_state"),
        sa.CheckConstraint(
            "owner_kind IN ('work','event_receipt','otp_challenge','live_invocation')",
            name="ck_bridge_action_owner"),
    )
    op.create_index("idx_bridge_action_owner", "bridge_action",
                    ["owner_kind", "owner_id"])


def downgrade() -> None:
    op.drop_index("idx_bridge_action_owner", table_name="bridge_action")
    op.drop_table("bridge_action")
    op.drop_index("idx_bridge_work_conv", table_name="bridge_work")
    op.drop_index("idx_bridge_work_claim", table_name="bridge_work")
    op.drop_table("bridge_work")
    op.drop_index("idx_bridge_event_stream", table_name="bridge_event")
    op.drop_index("idx_bridge_event_conv", table_name="bridge_event")
    op.drop_table("bridge_event_alias")
    op.drop_table("bridge_event")
