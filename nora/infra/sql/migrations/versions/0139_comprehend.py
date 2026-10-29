"""Record web pages and graph Cognition shadow receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0139_comprehend"
down_revision = "0138_graph_refusals"
branch_labels = None
depends_on = None


META = sa.MetaData()
COMPREHEND_WEB_PAGE = sa.Table(
    "comprehend_web_page",
    META,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("session_id", sa.BigInteger, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.clock_timestamp()),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("tool_call", pg.JSONB, nullable=False),
)
COMPREHEND_RESULT = sa.Table(
    "comprehend_result",
    META,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("work_id", sa.Text, nullable=False),
    sa.Column("input_hash", sa.Text, nullable=False),
    sa.Column("session_id", sa.BigInteger, nullable=False),
    sa.Column("part", sa.Text, nullable=False),
    sa.Column("payload", pg.JSONB, nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
sa.Index("idx_comprehend_result_work", COMPREHEND_RESULT.c.work_id, COMPREHEND_RESULT.c.input_hash)


def upgrade() -> None:
    META.create_all(op.get_bind())


def downgrade() -> None:
    META.drop_all(op.get_bind())
