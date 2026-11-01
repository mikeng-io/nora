"""Typed platform observations: the history table and the fold's head.

design/platform-observations 02. `platform_observation` holds one row per
admitted source transition/snapshot about an OBJECT (poll, room, member
set, pin set, artifact, reaction target, message metadata); provenance is
by VALUE (event_id + source_digest — `bridge_event` is the live queue
journal, not a durable parent). `platform_object_state` is the fold's
compare-and-swap head. Both take `row_history` triggers: the observation's
`target_message_id` re-links in place when a retained target arrives, and
the head is rewritten by every applied observation.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0144_platform_observation"
down_revision = "0143_bridge_revision_width"
branch_labels = None
depends_on = None

_HISTORY_TABLES = ("platform_observation", "platform_object_state")


def upgrade() -> None:
    op.create_table(
        "platform_observation",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False),
                  primary_key=True),
        sa.Column("contract", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text, nullable=False),
        sa.Column("source_digest", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_key", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("thread", sa.Text, nullable=False, server_default=""),
        sa.Column("object_ref", sa.Text, nullable=False),
        sa.Column("operation", sa.Text, nullable=False),
        sa.Column("target_key", sa.Text),
        sa.Column("target_author_id", sa.Text),
        sa.Column("target_message_id", sa.BigInteger,
                  sa.ForeignKey("messages.id",
                                name="platform_observation_target_fkey",
                                ondelete="SET NULL")),
        sa.Column("actor_id", sa.Text),
        sa.Column("actor_status", sa.Text, nullable=False),
        sa.Column("subject_ref", sa.Text),
        sa.Column("occurred_ms", sa.BigInteger),
        sa.Column("received_ms", sa.BigInteger, nullable=False),
        sa.Column("time_quality", sa.Text, nullable=False),
        sa.Column("source_revision", sa.Text),
        sa.Column("source_position", sa.BigInteger),
        sa.Column("revision_scope", sa.Text),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("coverage", pg.JSONB, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.UniqueConstraint("platform", "account_key", "source_digest",
                            name="uq_platform_observation_source"),
        sa.CheckConstraint(
            "kind IN ('reaction','message_metadata','poll','room',"
            "'membership','pin','artifact')",
            name="ck_platform_observation_kind"),
        sa.CheckConstraint(
            "operation IN ('create','revise','withdraw','add','remove',"
            "'clear','snapshot')",
            name="ck_platform_observation_op"),
        sa.CheckConstraint(
            "actor_status IN ('known','anonymous','not_supplied',"
            "'not_applicable')",
            name="ck_platform_observation_actor"),
        sa.CheckConstraint(
            "time_quality IN ('upstream','bridge_local','unknown')",
            name="ck_platform_observation_timeq"),
    )
    op.create_index(
        "idx_platform_observation_object", "platform_observation",
        ["platform", "account_key", "conversation_id", "kind", "object_ref",
         "id"])
    op.create_index(
        "idx_platform_observation_unresolved", "platform_observation",
        ["platform", "account_key", "conversation_id", "target_key"],
        postgresql_where=sa.text("target_message_id IS NULL"))
    op.create_index(
        "idx_platform_observation_target", "platform_observation",
        ["kind", "target_message_id"])

    op.create_table(
        "platform_object_state",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False),
                  primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_key", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("thread", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("object_ref", sa.Text, nullable=False),
        sa.Column("state", pg.JSONB, nullable=False),
        sa.Column("applied", sa.Integer, nullable=False, server_default="0"),
        sa.Column("head_observation_id", sa.BigInteger),
        sa.Column("source_position", sa.BigInteger),
        sa.Column("revision_scope", sa.Text),
        sa.Column("observed_ms", sa.BigInteger),
        sa.Column("ordering", sa.Text, nullable=False,
                  server_default="arrival"),
        sa.Column("coverage", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("projection_rev", sa.Integer, nullable=False,
                  server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "account_key", "conversation_id",
                            "thread", "kind", "object_ref",
                            name="uq_platform_object_state_object"),
        sa.CheckConstraint("ordering IN ('ordered','arrival','gap')",
                           name="ck_platform_object_state_ordering"),
    )
    op.create_index(
        "idx_platform_object_state_room", "platform_object_state",
        ["platform", "account_key", "conversation_id", "kind"])

    # Both tables change in place -> `nora_row_history` versions (the same
    # attach every in-place table's migration performs).
    for table in _HISTORY_TABLES:
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON \"{table}\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE "
            f"ON \"{table}\" FOR EACH STATEMENT "
            f"EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )

    # The 'observation' stimulus kind — the ONE source-event family the
    # cognition loop admits for non-message platform evidence (design
    # 05). `ck_stimulus_kind` is rebuilt rather than added so the shipped
    # constraint keeps one name and one vocabulary.
    op.drop_constraint("ck_stimulus_kind", "stimulus", type_="check")
    op.create_check_constraint(
        "ck_stimulus_kind", "stimulus",
        "kind IN ('message', 'social', 'play', 'rollup', 'time', 'self', "
        "'observation')")


def downgrade() -> None:
    op.drop_constraint("ck_stimulus_kind", "stimulus", type_="check")
    op.create_check_constraint(
        "ck_stimulus_kind", "stimulus",
        "kind IN ('message', 'social', 'play', 'rollup', 'time', 'self')")
    for table in _HISTORY_TABLES:
        op.execute(
            f'DROP TRIGGER IF EXISTS nora_row_history ON "{table}"')
        op.execute(
            f'DROP TRIGGER IF EXISTS nora_row_history_truncate ON "{table}"')
    op.drop_index("idx_platform_object_state_room",
                  table_name="platform_object_state")
    op.drop_table("platform_object_state")
    op.drop_index("idx_platform_observation_unresolved",
                  table_name="platform_observation")
    op.drop_index("idx_platform_observation_target",
                  table_name="platform_observation")
    op.drop_index("idx_platform_observation_object",
                  table_name="platform_observation")
    op.drop_table("platform_observation")
