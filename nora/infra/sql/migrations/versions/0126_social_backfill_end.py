"""Keep the reason a social backfill stopped, distinct from its cursor."""

import sqlalchemy as sa
from alembic import op

revision = "0126_social_backfill_end"
down_revision = "0125_persona_source_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_poll", sa.Column("end_reason", sa.Text))


def downgrade() -> None:
    op.drop_column("social_poll", "end_reason")
