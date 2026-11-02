"""multi-post outbound native identity — every confirmed native handle.

One send can produce several posts (a caption plus attachment stanzas);
`platform_message_id`/`platform_ref` keep their single-handle meaning, and
the new columns carry every confirmed ref so an inbound reaction or edit to
ANY piece resolves. Additive only: no backfill, no touched index.
"""

import sqlalchemy as sa
from alembic import op

revision = "0146_multi_post_native_ids"
down_revision = "0145_proactive_open_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("platform_message_ids_json", sa.dialects.postgresql.JSONB))
    op.add_column(
        "bridge_action",
        sa.Column("platform_refs", sa.dialects.postgresql.JSONB))


def downgrade() -> None:
    op.drop_column("bridge_action", "platform_refs")
    op.drop_column("messages", "platform_message_ids_json")
