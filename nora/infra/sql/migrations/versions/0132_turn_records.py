"""Keep every occasion's exchange and its recorded disclosure requirements."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0132_turn_records"
down_revision = "0131_extraction_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tag_trace", sa.Column(
        "turn_id", sa.BigInteger(), sa.Identity(), nullable=False,
    ))
    op.create_unique_constraint("uq_tag_trace_turn_id", "tag_trace", ["turn_id"])
    op.add_column("tag_trace", sa.Column("started_at", sa.BigInteger()))
    op.add_column("tag_trace", sa.Column(
        "occasion", sa.Text(), nullable=False, server_default="tag",
    ))
    op.add_column("tag_trace", sa.Column("platform", sa.Text(), nullable=False, server_default=""))
    op.add_column("tag_trace", sa.Column("unit", sa.Text()))
    for name, empty in (("bound_rooms", "[]"), ("requirements", "{}"), ("sent_message_ids", "[]")):
        op.add_column("tag_trace", sa.Column(
            name, pg.JSONB(), nullable=False, server_default=sa.text(f"'{empty}'::jsonb"),
        ))
    op.create_index(
        "idx_tag_trace_sent_messages", "tag_trace", ["sent_message_ids"], postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_tag_trace_sent_messages", table_name="tag_trace")
    op.drop_constraint("uq_tag_trace_turn_id", "tag_trace", type_="unique")
    for name in (
        "sent_message_ids", "requirements", "bound_rooms", "unit", "platform", "occasion",
        "started_at", "turn_id",
    ):
        op.drop_column("tag_trace", name)
