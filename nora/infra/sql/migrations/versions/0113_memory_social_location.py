"""memory_node: `social_location`, the place a social item is tagged with.

design/cognition-loop/22-social-stream.md §9 and
design/unified-memory/25-memory-graph.md §7a: a social item's `location` is a
node of its own, keyed like its item and `attached_to` it, so a read can gate
it by presence (`nora.domain.memory_index.PRESENCE_KINDS`) while the item
stays readable by its caption. It is embedded, so `idx_memory_node_unembedded`'s
predicate (the worker's literal `kind IN (...)`, which must imply it) names it
too. No new relation: `attached_to` is in the closed set already.

Every recorded item with a location is put on `memory_projection_queue`,
the index's own way back to a source it has not projected: the runtime's
drain loop re-projects each one (`drain_queue`), so an item recorded before
this revision gains its location node with no rebuild, and searches answer
`partial` until the queue is empty. `last_error` names why the row is
there, as a failure's class would.

Follows `0112_social_backfill`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0113_memory_social_location
Revises: 0112_social_backfill
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0113_memory_social_location"
down_revision = "0112_social_backfill"
branch_labels = None
depends_on = None

_KINDS = (
    "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character', "
    "'outing', 'stay', 'trip', 'person', 'room', 'unit', 'nora', 'place', "
    "'social', 'social_media'"
)
_EMBEDDED = (
    "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character', "
    "'social', 'social_media'"
)
_LOCATION = "'social_location'"


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
    _kinds(f"{_KINDS}, {_LOCATION}", f"{_EMBEDDED}, {_LOCATION}")
    op.execute(
        "INSERT INTO memory_projection_queue (kind, source_id, last_error) "
        "SELECT 'social', network || ':' || item_id, 'Reprojection' FROM social_item "
        "WHERE btrim(coalesce(location, '')) <> '' "
        "ON CONFLICT (kind, source_id) DO NOTHING"
    )


def downgrade() -> None:
    # Derived rows (NOT_RESTORED): a rebuild on the older code writes none.
    # The queue's `social` rows are left: the older drain projects them too.
    op.execute("DELETE FROM memory_edge WHERE src_kind = 'social_location' "
               "OR dst_kind = 'social_location'")
    op.execute("DELETE FROM memory_node WHERE kind = 'social_location'")
    _kinds(_KINDS, _EMBEDDED)
