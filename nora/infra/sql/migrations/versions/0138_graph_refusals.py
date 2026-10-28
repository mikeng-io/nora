"""Retain partial admission refusal reasons."""

import sqlalchemy as sa
from alembic import op

revision = "0138_graph_refusals"
down_revision = "0137_memory_graph"
branch_labels = None
depends_on = None
METADATA = sa.MetaData()

MEMORY_GRAPH_REFUSAL = sa.Table(
    "graph_refusal",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("part_id", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

MEMORY_GRAPH_ARRIVAL = sa.Table(
    "graph_arrival",
    METADATA,
    sa.Column("unit", sa.Text, primary_key=True),
    sa.Column("message_id", sa.BigInteger, primary_key=True),
    sa.Column("revision", sa.Text, primary_key=True),
    sa.Column("room", sa.Text, nullable=False),
    sa.Column(
        "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("batched", sa.Boolean, nullable=False, server_default=sa.false()),
)
sa.Index(
    "idx_graph_arrival_pending",
    MEMORY_GRAPH_ARRIVAL.c.unit,
    MEMORY_GRAPH_ARRIVAL.c.room,
    MEMORY_GRAPH_ARRIVAL.c.queued_at,
    postgresql_where=MEMORY_GRAPH_ARRIVAL.c.batched.is_(False),
)


def upgrade() -> None:
    METADATA.create_all(op.get_bind())
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON graph_arrival FOR EACH ROW EXECUTE FUNCTION "
        "nora_row_history('unit', 'message_id', 'revision')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE "
        "ON graph_arrival FOR EACH STATEMENT EXECUTE FUNCTION "
        "nora_row_history_refuse_truncate()"
    )


def downgrade() -> None:
    METADATA.drop_all(op.get_bind())
