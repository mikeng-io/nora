"""Scope each raw presence fix to the observing unit its collector copy belongs to."""

import sqlalchemy as sa
from alembic import op

revision = "0184_presence_fix_unit"
down_revision = "0183_claim_grade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presence_fixes", sa.Column("unit", sa.Text))


def downgrade() -> None:
    op.drop_column("presence_fixes", "unit")
