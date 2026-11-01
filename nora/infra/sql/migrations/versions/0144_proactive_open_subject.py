"""One open proactive row per (subject, action, destination).

A second partial expression index on `reminders`, the same shape
`0064_proactive_open_note` established for `note_id`: Postgres arbitrates the
resolved shared-object reference while the unit lock handles the in-process
read-check-write. Hand-written like 0064: `alembic check` cannot see a partial
predicate or an expression column, and
`tests/contract/test_reminder_store_contract.py` is what ties the store's
conflict behaviour to this index.
"""

import sqlalchemy as sa
from alembic import op

revision = "0144_proactive_open_subject"
down_revision = "0143_bridge_revision_width"
branch_labels = None
depends_on = None

METADATA = sa.MetaData()
REMINDERS = sa.Table(
    "reminders",
    METADATA,
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("payload", sa.JSON, nullable=True),
)


def upgrade() -> None:
    op.create_index(
        "uq_reminders_proactive_open_subject",
        "reminders",
        [
            sa.text("(payload ->> 'subject_kind')"),
            sa.text("(payload ->> 'subject_id')"),
            sa.text("COALESCE(payload ->> 'action', '')"),
            "platform",
            "conversation_id",
        ],
        unique=True,
        postgresql_where=sa.text("kind = 'proactive' AND state IN ('pending', 'firing')"),
    )


def downgrade() -> None:
    op.drop_index("uq_reminders_proactive_open_subject", table_name="reminders")
