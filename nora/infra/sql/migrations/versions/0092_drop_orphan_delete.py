"""drop `orphan_delete`: held platform deletes are not kept any more (a delete of a
message not on record is dropped, as a reaction is).

`0088_orphan_delete` kept a delete whose target was not on record yet, so the message
was hidden the moment it arrived. Live there had been 2 deletes in 5,984 messages
(2026-10-03) and the table held 0 rows: the harm it guarded never happened, and the
matching, caps, age window and start-up sweep around it were machinery for nothing
(AGENTS.md «A guard needs a consequence»). A delete of a message that IS on record
still hides it (`messages.hidden_at`); that is unchanged.

Drops the table, its indexes and its `nora_row_history` triggers. Its `row_history`
versions are left (the history table is append-only and keeps what the table held).
Downgrade recreates the empty table, exactly as `0088` made it.

Follows `0091_cognition_cutover`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0092_drop_orphan_delete
Revises: 0091_cognition_cutover
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0092_drop_orphan_delete"
down_revision = "0091_cognition_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"orphan_delete\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"orphan_delete\"")
    op.execute("DROP INDEX IF EXISTS uq_orphan_delete_delivery")
    op.drop_index("idx_orphan_delete_recorded", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_deleter", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_sent_ts", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_handle", table_name="orphan_delete")
    op.drop_table("orphan_delete")


def downgrade() -> None:
    op.create_table(
        "orphan_delete",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("handle", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("sent_ts", sa.BigInteger),
        sa.Column("deleter_id", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "deleter_ids", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("deleter_person_id", sa.Text),
        sa.Column("event_ts", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "handle <> '' OR sent_ts IS NOT NULL", name="ck_orphan_delete_names_a_target",
        ),
    )
    op.create_index(
        "idx_orphan_delete_handle", "orphan_delete",
        ["platform", "conversation_id", "handle"], postgresql_where=sa.text("handle <> ''"),
    )
    op.create_index(
        "idx_orphan_delete_sent_ts", "orphan_delete",
        ["platform", "conversation_id", "sent_ts"],
        postgresql_where=sa.text("sent_ts IS NOT NULL"),
    )
    op.create_index("idx_orphan_delete_deleter", "orphan_delete", ["platform", "deleter_id"])
    op.create_index("idx_orphan_delete_recorded", "orphan_delete", ["recorded_at"])
    op.execute(
        "CREATE UNIQUE INDEX uq_orphan_delete_delivery ON orphan_delete "
        "(platform, conversation_id, handle, (coalesce(sent_ts, -1)), deleter_id)"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"orphan_delete\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"orphan_delete\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
