"""Receipts for closed historical graph wakes."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from nora.infra.sql.schema import METADATA

GRAPH_REPLAY_RECEIPT = sa.Table(
    "graph_replay_receipt", METADATA,
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("pass_id", sa.Text, nullable=False),
    sa.Column("clock", sa.DateTime(timezone=True), nullable=False),
    sa.Column("inputs", JSONB, nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("unit", "pass_id", name="pk_graph_replay_receipt"),
)
