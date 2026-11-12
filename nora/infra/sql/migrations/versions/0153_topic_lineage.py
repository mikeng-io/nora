"""Entry lineage and the live-subject uniqueness the Cognition writer needs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0153_topic_lineage"
down_revision = "0152_fetched_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notebook_entry_revision",
        sa.Column(
            "lineage", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "notebook_entry",
        sa.Column("retired", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    # One live account per subject per unit; the store writes the casefolded
    # subject so the index carries the case-insensitive rule itself.
    op.execute(
        "UPDATE notebook_entry e SET retired = true FROM ("
        "SELECT DISTINCT ON (entry_id) entry_id, content "
        "FROM notebook_entry_revision ORDER BY entry_id, seq DESC"
        ") head WHERE head.entry_id = e.id AND head.content->>'state' = 'retired'"
    )
    op.add_column(
        "notebook_entry",
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE notebook_entry e SET subject = lower(head.subject) FROM ("
        "SELECT DISTINCT ON (entry_id) entry_id, content->'about'->0->>1 AS subject "
        "FROM notebook_entry_revision ORDER BY entry_id, seq DESC"
        ") head WHERE head.entry_id = e.id"
    )
    op.create_index(
        "uq_entry_live_subject",
        "notebook_entry",
        ["unit", "subject"],
        unique=True,
        postgresql_where=sa.text("NOT retired"),
    )


def downgrade() -> None:
    op.drop_index("uq_entry_live_subject", table_name="notebook_entry")
    op.drop_column("notebook_entry", "subject")
    op.drop_column("notebook_entry", "retired")
    op.drop_column("notebook_entry_revision", "lineage")
