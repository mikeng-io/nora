"""memory_node, memory_edge, memory_projection_queue, memory_index_state -- the
one memory index (design/unified-memory/60-build-step-3.md §1, 25-memory-graph.md).

The index is a PROJECTION: every row is read from a store that already owns
the truth, so all four tables are `NOT_RESTORED` (`nora.infra.sql.history`)
and a lost index is rebuilt, never restored. A node is a pointer plus what is
needed to find it (its text, time and scope); an edge names its two ends by
`(kind, source_id)`, never by `memory_node.id`, so an edge survives a rebuild
that renumbers the nodes, and has no foreign key: every hop joins
`memory_node`, so an edge whose end is gone drops itself (60 §1).

`kind` and `rel` are CHECKed against closed sets (25 §2: a new relation is a
schema change, not something a model invents). `kind` lists every kind step 3
names, so S2-S4 add projectors without another CHECK migration. A node that is
not an entity (a hub: person, room, unit, Nora, place) must carry a scope: a
unit, an origin room, or the persons it is readable to (60 §0.1, §0.3).

`text` carries a trigram GIN index, the Record's own lexical choice
(`idx_messages_body_trgm`, `0073_lineage`): the corpus is mixed English and
Cantonese and no tsvector config segments it. `pg_trgm` is installed by
`0026_note_tier`. No vector column yet: the embedding bake-off's migration
adds it to this row, so the scope filter runs inside the scan with no join.

`memory_index_state` holds exactly one row (`id` is a true-only key), seeded
`never`: an index nothing has rebuilt answers `partial`, never `nothing_found`.

Follows `0104_external_files`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0105_memory_index
Revises: 0104_external_files
Create Date: 2026-10-15
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0105_memory_index"
down_revision = "0104_external_files"
branch_labels = None
depends_on = None


def _created(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "memory_node",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("unit", sa.Text),
        sa.Column("origin_room", sa.Text),
        sa.Column("persons", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("exclude_persons", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("lineage", pg.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("sha256", sa.Text),
        sa.Column("text", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("at_ms", sa.BigInteger),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'live'")),
        _created("projected_at"),
        sa.UniqueConstraint("kind", "source_id", name="uq_memory_node_kind_source"),
        sa.CheckConstraint(
            "kind IN ('message', 'media', 'note', 'memo', 'event', 'lesson', "
            "'character', 'outing', 'stay', 'trip', 'person', 'room', 'unit', "
            "'nora', 'place')",
            name="ck_memory_node_kind",
        ),
        sa.CheckConstraint(
            "state IN ('live', 'retired', 'superseded')", name="ck_memory_node_state",
        ),
        sa.CheckConstraint(
            "kind IN ('person', 'room', 'unit', 'nora', 'place') "
            "OR unit IS NOT NULL OR origin_room IS NOT NULL "
            "OR cardinality(persons) > 0",
            name="ck_memory_node_has_a_scope",
        ),
    )
    op.create_index("idx_memory_node_unit", "memory_node", ["unit"])
    op.create_index("idx_memory_node_origin_room", "memory_node", ["origin_room"])
    op.create_index("idx_memory_node_sha256", "memory_node", ["sha256"])
    op.create_index(
        "idx_memory_node_text_trgm", "memory_node", ["text"],
        postgresql_using="gin", postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_table(
        "memory_edge",
        sa.Column("src_kind", sa.Text, nullable=False),
        sa.Column("src_id", sa.Text, nullable=False),
        sa.Column("rel", sa.Text, nullable=False),
        sa.Column("dst_kind", sa.Text, nullable=False),
        sa.Column("dst_id", sa.Text, nullable=False),
        sa.Column("provenance", sa.Text, nullable=False),
        _created("created_at"),
        sa.PrimaryKeyConstraint(
            "src_kind", "src_id", "rel", "dst_kind", "dst_id", name="memory_edge_pkey",
        ),
        sa.CheckConstraint(
            "rel IN ('authored_by', 'in_room', 'shared_by', 'attached_to', 'during', "
            "'about', 'cites', 'supersedes', 'tracks', 'closes_into', "
            "'leaves_behind', 'draws_on', 'informed_by', 'disputes', 'feedback_on', "
            "'depicts', 'mentions', 'located_in', 'outcome_of')",
            name="ck_memory_edge_rel",
        ),
    )
    # The walk's other direction: everything pointing AT a node.
    op.create_index(
        "idx_memory_edge_dst", "memory_edge", ["dst_kind", "dst_id"],
    )
    op.create_table(
        "memory_projection_queue",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        _created("first_failed_at"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("last_error", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("kind", "source_id", name="memory_projection_queue_pkey"),
    )
    op.create_table(
        "memory_index_state",
        sa.Column("id", sa.Boolean, primary_key=True, server_default=sa.text("true")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("unprojected", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("id", name="ck_memory_index_state_one_row"),
        sa.CheckConstraint(
            "status IN ('never', 'building', 'complete')",
            name="ck_memory_index_state_status",
        ),
    )
    op.execute("INSERT INTO memory_index_state (id, status) VALUES (true, 'never')")


def downgrade() -> None:
    # Going back down forgets the index; every store it projects is untouched.
    op.drop_table("memory_index_state")
    op.drop_table("memory_projection_queue")
    op.drop_index("idx_memory_edge_dst", table_name="memory_edge")
    op.drop_table("memory_edge")
    op.drop_index("idx_memory_node_text_trgm", table_name="memory_node")
    op.drop_index("idx_memory_node_sha256", table_name="memory_node")
    op.drop_index("idx_memory_node_origin_room", table_name="memory_node")
    op.drop_index("idx_memory_node_unit", table_name="memory_node")
    op.drop_table("memory_node")
