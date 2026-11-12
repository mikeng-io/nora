"""Entry lineage and the live-subject uniqueness the Cognition writer needs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0153_topic_lineage"
down_revision = "0152_fetched_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `subject` is store-written already casefolded, so no SQL normalisation
    # and no backfill: any held row needs reconciliation first.
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM notebook_entry LIMIT 1")
    ).first():
        raise RuntimeError(
            "notebook_entry holds rows: an existing topic account needs "
            "reconciliation before subject, retired and lineage land"
        )
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
    op.add_column(
        "notebook_entry",
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
    )
    # One live account per subject per unit; `retired` is a mutable head
    # flag (as `events` is over `event_revision`) -> HISTORY_TABLES.
    op.create_index(
        "uq_entry_live_subject",
        "notebook_entry",
        ["unit", "subject"],
        unique=True,
        postgresql_where=sa.text("NOT retired"),
    )
    for stmt in (
        'CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE '
        'ON "notebook_entry" FOR EACH ROW EXECUTE FUNCTION nora_row_history(\'id\')',
        'CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE '
        'ON "notebook_entry" EXECUTE FUNCTION nora_row_history_refuse_truncate()',
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS nora_row_history ON "notebook_entry"')
    op.execute(
        'DROP TRIGGER IF EXISTS nora_row_history_truncate ON "notebook_entry"'
    )
    op.drop_index("uq_entry_live_subject", table_name="notebook_entry")
    op.drop_column("notebook_entry", "subject")
    op.drop_column("notebook_entry", "retired")
    op.drop_column("notebook_entry_revision", "lineage")
