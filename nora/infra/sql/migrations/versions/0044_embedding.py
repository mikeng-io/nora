"""embedding -- the dense channel's storage, and the seam's one table

`design/dreamer-agent-loop/24-embedding-seam.md` §7.5.

**ONE table for every tier, not one per tier.** §1's whole argument is that this
is one seam and not three: a tier adopts retrieval by declaring three things --
what kind it is, what text is worth matching, and what its key is -- and writing
no schema at all.

**`kind` is an open string, never an enum.** A closed set means the fifth tier
ships with a CHECK edit before it can store its first vector. The exchange
tier's closed act set is right for ACTS, which are a vocabulary; `kind` is a
REGISTRY, and registries stay open.

**One column per DIMENSION, routed by `kind`.** `vector(N)` fixes N at DDL time,
so per-kind dimension cannot be one column -- a future tier bringing its own
model adds a COLUMN rather than a type, and the "declare three things and write
no schema" bar still holds for any tier reusing an existing dimension.

**1024 is MEASURED, not assumed.** `bge-m3:latest` was called on the live
endpoint on 2026-08-28 and returned 1024 floats. A column built against a
guessed width fails on the first write, in a background pass nobody is watching.
The constant lived in `nora.infra.sql.schema` until 0106 dropped the table;
it is spelled here now.

**HNSW rather than IVFFlat.** IVFFlat needs a training pass over existing rows
to build its lists, and this table starts EMPTY -- an IVFFlat index built on
nothing has useless lists until somebody remembers to rebuild it. HNSW needs no
such pass. `vector_cosine_ops` because `23` §6 ranks on cosine distance.

Requires the `vector` extension, which migration order does not guarantee -- the
operator's container swap installs it, and this refuses to run without it rather
than creating a table whose vector column silently is not one.

Revision ID: 0044_embedding
Revises: 0043_disputed
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

#: bge-m3's width. It was `nora.infra.sql.schema.EMBEDDING_DIMENSIONS` until
#: `0106_memory_vectors` dropped this table and that name took Qwen3-VL's
#: 2048: a migration is a frozen statement, so the number lives here now.
EMBEDDING_DIMENSIONS = 1024

revision = "0044_embedding"
down_revision = "0043_disputed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create extension if not exists vector")
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
    op.execute(f"alter table embedding add column v1024 vector({EMBEDDING_DIMENSIONS})")
    op.execute(
        "create index idx_embedding_v1024 on embedding "
        "using hnsw (v1024 vector_cosine_ops)"
    )
    op.create_index("idx_embedding_scope", "embedding", ["kind", "scope"])


def downgrade() -> None:
    op.drop_index("idx_embedding_scope", table_name="embedding")
    op.drop_index("idx_embedding_v1024", table_name="embedding")
    op.drop_table("embedding")
    # `vector` is deliberately NOT dropped: an extension is database-scoped and
    # another table may hold a column of that type. `0026_note_tier` records
    # the same reasoning for `pg_trgm`.
