"""memory_node: the social stream's kinds, `social` and `social_media`.

design/cognition-loop/22-social-stream.md §9 and
design/unified-memory/25-memory-graph.md §2 and §7: one `social` node per
`social_item` and one `social_media` node per medium of it. Both are
embedded, so `idx_memory_node_unembedded`'s predicate (the worker's literal
`kind IN (...)`, which must imply it) names them too. No new relation: an
item is `authored_by` its person, a medium `attached_to` its item and
`shared_by` the person, all in the closed set already.

Follows `0109_blob_tombstone`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0110_memory_social
Revises: 0109_blob_tombstone
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0110_memory_social"
down_revision = "0109_blob_tombstone"
branch_labels = None
depends_on = None

_KINDS = (
    "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character', "
    "'outing', 'stay', 'trip', 'person', 'room', 'unit', 'nora', 'place'"
)
_EMBEDDED = "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character'"
_SOCIAL = "'social', 'social_media'"


def _kinds(kinds: str, embedded: str) -> None:
    op.drop_constraint("ck_memory_node_kind", "memory_node", type_="check")
    op.create_check_constraint("ck_memory_node_kind", "memory_node", f"kind IN ({kinds})")
    op.drop_index("idx_memory_node_unembedded", table_name="memory_node")
    op.create_index(
        "idx_memory_node_unembedded", "memory_node",
        ["embed_attempts", sa.text("id DESC")],
        postgresql_where=sa.text(
            f"embedding IS NULL AND state = 'live' AND kind IN ({embedded})"
        ),
    )


def upgrade() -> None:
    _kinds(f"{_KINDS}, {_SOCIAL}", f"{_EMBEDDED}, {_SOCIAL}")


def downgrade() -> None:
    # Derived rows (NOT_RESTORED): a rebuild on the older code writes none.
    # A queued social row would raise KeyError in the older drain on every
    # pass and hold every search `partial`.
    op.execute(
        "DELETE FROM memory_projection_queue WHERE kind IN ('social', 'social_media')"
    )
    op.execute(
        "DELETE FROM memory_edge WHERE src_kind IN ('social', 'social_media') "
        "OR dst_kind IN ('social', 'social_media')"
    )
    op.execute("DELETE FROM memory_node WHERE kind IN ('social', 'social_media')")
    _kinds(_KINDS, _EMBEDDED)
