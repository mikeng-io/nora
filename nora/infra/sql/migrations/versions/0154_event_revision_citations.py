"""The citation fold an event write rests on lives on its revision."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0154_event_revision_citations"
down_revision = "0153_topic_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `{part: [refs]}` in the citations-column spelling; NULL on the rows
    # that predate it, so they project nothing rather than nothing-known.
    op.add_column(
        "event_revision",
        sa.Column("citations", pg.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_event_revision_citations_shape",
        "event_revision",
        "citations is null or jsonb_typeof(citations) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_event_revision_citations_shape", "event_revision",
    )
    op.drop_column("event_revision", "citations")
