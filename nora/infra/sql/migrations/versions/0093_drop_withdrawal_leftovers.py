"""drop what the withdrawal cascade left behind: the `unsupported_at` columns, the
`citation` edge table, and the token columns of `cognition_wake_outcome`.

Nothing marks a row `unsupported_at` since the cascade was removed (0092), nothing reads
`citation`, and nothing reads the token columns `0090` added to `cognition_wake_outcome`
(their only reader, `nora cognition health`, is gone; the `nora_cognition_unit_tokens_total`
counter stays). AGENTS.md «A guard needs a consequence». `messages.hidden_at` stays: a
platform delete hides the message through it.

Drops `note.unsupported_at`, `standing.unsupported_at`, `culture.unsupported_at`,
`events.unsupported_at`, the `citation` table (its indexes, constraints and
`nora_row_history` triggers go with it; its `row_history` versions are left, the history
table being append-only), and `cognition_wake_outcome.{input,output,cache_read}_tokens` and
`trace_id`.

Downgrade recreates the columns (NULL) and the EMPTY table. What was in them is not kept.

Follows `0092_drop_orphan_delete`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0093_drop_withdrawal_leftovers
Revises: 0092_drop_orphan_delete
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0093_drop_withdrawal_leftovers"
down_revision = "0092_drop_orphan_delete"
branch_labels = None
depends_on = None

_HEADS = ("note", "standing", "culture", "events")
_TOKEN_COLUMNS = (
    ("input_tokens", sa.BigInteger), ("output_tokens", sa.BigInteger),
    ("cache_read_tokens", sa.BigInteger), ("trace_id", sa.Text),
)


def upgrade() -> None:
    op.drop_table("citation")
    for table in _HEADS:
        op.drop_column(table, "unsupported_at")
    for name, _ in _TOKEN_COLUMNS:
        op.drop_column("cognition_wake_outcome", name)


def downgrade() -> None:
    for name, kind in _TOKEN_COLUMNS:
        op.add_column("cognition_wake_outcome", sa.Column(name, kind))
    for table in _HEADS:
        op.add_column(table, sa.Column("unsupported_at", sa.BigInteger, nullable=True))
    op.create_table(
        "citation",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("citing_store", sa.Text, nullable=False),
        sa.Column("citing_key", sa.Text, nullable=False),
        sa.Column("cited_store", sa.Text, nullable=False, server_default="message"),
        sa.Column("cited_id", sa.Text, nullable=False),
        sa.Column("part", sa.Text, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "citing_store in ('note', 'standing', 'culture', 'event')",
            name="citation_citing_store_is_known_check",
        ),
        sa.CheckConstraint(
            "cited_store in ('message', 'rollup')", name="citation_cited_store_is_known_check",
        ),
        sa.CheckConstraint(
            "part is null or length(btrim(part)) > 0", name="citation_part_is_not_blank_check",
        ),
        sa.CheckConstraint(
            "length(btrim(citing_key)) > 0", name="citation_has_a_citing_key_check",
        ),
        sa.CheckConstraint(
            "length(btrim(cited_id)) > 0", name="citation_has_a_cited_id_check",
        ),
        sa.UniqueConstraint(
            "citing_store", "citing_key", "cited_store", "cited_id", "part",
            name="citation_edge_unique", postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("idx_citation_cited", "citation", ["cited_store", "cited_id"])
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"citation\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"citation\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
