"""Source-specific Persona revision progress at the consolidation seam."""
import sqlalchemy as sa
from alembic import op

revision = "0125_persona_source_revision"
down_revision = "0124_typed_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consolidation_watermark", sa.Column("source_revision", sa.Text))
    op.add_column("consolidation_watermark", sa.Column("source_read_revision", sa.Text))


def downgrade() -> None:
    op.drop_column("consolidation_watermark", "source_read_revision")
    op.drop_column("consolidation_watermark", "source_revision")
