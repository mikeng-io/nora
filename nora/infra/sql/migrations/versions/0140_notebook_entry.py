"""Notebook entry machinery, topic only."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0140_notebook_entry"
down_revision = "0139_comprehend"
branch_labels = None
depends_on = None
METADATA = sa.MetaData()

NOTEBOOK_ENTRY = sa.Table(
    "notebook_entry",
    METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("kind = 'topic'", name="ck_entry_kind"),
    sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_entry_unit"),
)
sa.Index("idx_notebook_entry_unit", NOTEBOOK_ENTRY.c.unit)
NOTEBOOK_ENTRY_REVISION = sa.Table(
    "notebook_entry_revision",
    METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("entry_id", sa.Text, sa.ForeignKey("notebook_entry.id"), nullable=False),
    sa.Column("base_revision_id", sa.Text, sa.ForeignKey("notebook_entry_revision.id")),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("content", pg.JSONB, nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("seq >= 1", name="ck_entry_revision_seq"),
    sa.CheckConstraint("(seq = 1) = (base_revision_id IS NULL)", name="ck_entry_revision_base"),
)
sa.Index(
    "uq_entry_revision_seq",
    NOTEBOOK_ENTRY_REVISION.c.entry_id,
    NOTEBOOK_ENTRY_REVISION.c.seq,
    unique=True,
)


def upgrade() -> None:
    METADATA.create_all(op.get_bind())


def downgrade() -> None:
    METADATA.drop_all(op.get_bind())
