"""Nullable typed Identity values; legacy prose is retained without conversion."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0124_typed_identity"
down_revision = "0123_persona_line_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("fact_key", sa.Text),
        sa.Column("fact_value", pg.JSONB),
        sa.Column("valid_from", sa.BigInteger),
        sa.Column("valid_until", sa.BigInteger),
    ):
        op.add_column("persona_line", column)


def downgrade() -> None:
    for name in ("valid_until", "valid_from", "fact_value", "fact_key"):
        op.drop_column("persona_line", name)
