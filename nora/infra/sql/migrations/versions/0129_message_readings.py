"""Write-only Record message readings, durable arrival queue and token attempts.

No historical sources are queued; no memory index projection or reader is added.
Revision ID: 0129_message_readings
Revises: 0128_event_platform
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0129_message_readings"
down_revision = "0128_event_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_reading",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("origin_room", sa.Text, nullable=False),
        sa.Column("source_ids", pg.ARRAY(sa.Integer), nullable=False),
        sa.Column("context_ids", pg.ARRAY(sa.Integer), nullable=False),
        sa.Column("snapshot", pg.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "due_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("built_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('pending', 'done', 'retired')", name="ck_message_reading_state"
        ),
    )
    op.create_table(
        "message_reading_queue",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("origin_room", sa.Text, nullable=False),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("is_source", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("message_id", name="uq_message_reading_queue_message"),
        sa.Column("reading_id", sa.BigInteger, sa.ForeignKey("message_reading.id")),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "platform", "origin_room", "source_key", name="uq_message_reading_arrival"
        ),
    )
    op.create_table(
        "message_reading_claim",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("reading_id", sa.BigInteger, sa.ForeignKey("message_reading.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("origin_room", sa.Text, nullable=False),
        sa.Column("source_ids", pg.ARRAY(sa.Integer), nullable=False),
        sa.Column("content", pg.JSONB, nullable=False),
        sa.Column(
            "built_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reading_id", "ordinal", name="uq_message_reading_claim"),
    )
    op.create_table(
        "message_reading_attempt",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("reading_id", sa.BigInteger, sa.ForeignKey("message_reading.id"), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("provider_model", sa.Text),
        sa.Column("trace_id", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_tokens", sa.BigInteger),
        sa.Column("output_tokens", sa.BigInteger),
        sa.Column("outcome", sa.Text, nullable=False, server_default=sa.text("'started'")),
        sa.UniqueConstraint("reading_id", "attempt", name="uq_message_reading_attempt"),
    )
    op.create_index("idx_message_reading_due", "message_reading", ["state", "due_at"])
    op.create_index(
        "idx_message_reading_queue_pending", "message_reading_queue", ["reading_id", "queued_at"]
    )
    # All changing Record rows participate in the existing system-time history.
    for table in ("message_reading", "message_reading_claim", "message_reading_attempt"):
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON message_reading_queue FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON message_reading_queue "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
    op.execute("""
        CREATE FUNCTION message_readings_retire(source integer) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE retired bigint[];
        BEGIN
            WITH changed AS (
                UPDATE message_reading SET state = 'retired', retired_at = now()
                WHERE state <> 'retired'
                  AND (source = ANY(source_ids) OR source = ANY(context_ids))
                RETURNING id
            ) SELECT array_agg(id) INTO retired FROM changed;
            UPDATE message_reading_claim SET retired_at = now()
              WHERE reading_id = ANY(retired) AND retired_at IS NULL;
            UPDATE message_reading_queue SET reading_id = NULL
              WHERE reading_id = ANY(retired);
        END $$
    """)
    op.execute("""
        CREATE FUNCTION message_readings_source_changed() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'messages' THEN
                IF TG_OP = 'DELETE' THEN
                    PERFORM message_readings_retire(OLD.id);
                    RETURN OLD;
                END IF;
                IF NEW.hidden_at IS DISTINCT FROM OLD.hidden_at
                   OR NEW.body IS DISTINCT FROM OLD.body
                   OR NEW.person_id IS DISTINCT FROM OLD.person_id
                   OR NEW.ts IS DISTINCT FROM OLD.ts
                   OR NEW.mention_persons_json IS DISTINCT FROM OLD.mention_persons_json THEN
                    PERFORM message_readings_retire(NEW.id);
                END IF;
            ELSE
                PERFORM id FROM messages WHERE id = NEW.message_id FOR UPDATE;
                PERFORM message_readings_retire(NEW.message_id);
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER message_readings_changed BEFORE UPDATE OR DELETE "
        "ON messages FOR EACH ROW EXECUTE FUNCTION message_readings_source_changed()"
    )
    for table in ("message_edit", "message_delete"):
        op.execute(
            f"CREATE TRIGGER message_readings_changed BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION message_readings_source_changed()"
        )


def downgrade() -> None:
    for table in ("messages", "message_edit", "message_delete"):
        op.execute(f"DROP TRIGGER message_readings_changed ON {table}")
    op.execute("DROP FUNCTION message_readings_source_changed()")
    op.execute("DROP FUNCTION message_readings_retire(integer)")
    for table in (
        "message_reading_queue",
        "message_reading_claim",
        "message_reading_attempt",
        "message_reading",
    ):
        op.drop_table(table)
