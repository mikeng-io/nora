"""memory_node gains its vector; the dead `embedding` table goes
(design/unified-memory/65-build-step-3-vectors.md §1, V1).

**Store 2048, index 256** (65 §0, measured in #24): `embedding halfvec(2048)`
on the node itself, so a search's scope filter runs inside the scan with no
join (60 §1), and an HNSW expression index over the leading 256 dimensions,
`halfvec_cosine_ops`. A search takes that index's top 50 and re-scores them on
the full 2048 (V2). pgvector's HNSW limit for `halfvec` is 4000 dimensions;
the live server is 0.8.6 on PG18.

**The node is its own queue** (65 §1.1). `project()` clears `embedding`,
`embedding_model`, `embedded_at`, `embed_attempts` and `embed_error` when the
embedded input changes; the embedding worker picks live, embeddable nodes
with a NULL `embedding` through `idx_memory_node_unembedded`, a partial index
whose predicate names the embeddable kinds (`nora.domain.memory_index.
EMBEDDED_KINDS`) as literals, so the worker's literal predicate implies it.

**`embedding` (0044) is dropped** (65 §1.2): its code went with the
deprecated tiers on 2026-09-07 and nothing has read or written it since. The
downgrade puts it back empty, as 0044 built it.

Every new column is NULL or defaulted, so the ALTERs rewrite no row. The
index builds are quick on today's table: every `embedding` starts NULL.

Follows `0105_memory_index`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0106_memory_vectors
Revises: 0105_memory_index
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0106_memory_vectors"
down_revision = "0105_memory_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_node ADD COLUMN embedding halfvec(2048)")
    op.add_column("memory_node", sa.Column("embedding_model", sa.Text))
    op.add_column("memory_node", sa.Column("embedded_at", sa.BigInteger))
    op.add_column("memory_node", sa.Column(
        "embed_attempts", sa.Integer, nullable=False, server_default=sa.text("0"),
    ))
    op.add_column("memory_node", sa.Column("embed_error", sa.Text))
    op.execute(
        "CREATE INDEX idx_memory_node_embedding_256 ON memory_node USING hnsw "
        "((subvector(embedding, 1, 256)::halfvec(256)) halfvec_cosine_ops)"
    )
    op.create_index(
        "idx_memory_node_unembedded", "memory_node", ["embed_attempts", "id"],
        postgresql_where=sa.text(
            "embedding IS NULL AND state = 'live' AND kind IN ('message', 'media', "
            "'note', 'memo', 'event', 'lesson', 'character')"
        ),
    )

    op.drop_index("idx_embedding_scope", table_name="embedding")
    op.drop_index("idx_embedding_v1024", table_name="embedding")
    op.drop_table("embedding")


def downgrade() -> None:
    op.create_table(
        "embedding",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("row_key", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("fingerprint", sa.Text, nullable=False),
        sa.Column("embedded_at", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("kind", "row_key"),
        sa.CheckConstraint(
            "length(btrim(text)) > 0", name="embedding_text_is_present_check",
        ),
        sa.CheckConstraint(
            "length(btrim(model)) > 0", name="embedding_model_is_named_check",
        ),
    )
    op.execute("ALTER TABLE embedding ADD COLUMN v1024 vector(1024)")
    op.execute(
        "CREATE INDEX idx_embedding_v1024 ON embedding USING hnsw (v1024 vector_cosine_ops)"
    )
    op.create_index("idx_embedding_scope", "embedding", ["kind", "scope"])

    op.drop_index("idx_memory_node_unembedded", table_name="memory_node")
    op.drop_index("idx_memory_node_embedding_256", table_name="memory_node")
    for column in ("embed_error", "embed_attempts", "embedded_at", "embedding_model",
                   "embedding"):
        op.drop_column("memory_node", column)
