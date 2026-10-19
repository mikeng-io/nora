"""memory_node: `persona_line`, one Persona account line (design/unified-memory/45 §1).

The switch (00 «Sequence» step 8): a line's text is embedded and searched like
a note's; its citations, `about`, `supersedes` and `draws_on` are edges. No new
relation: `cites`, `about`, `supersedes` and `draws_on` are in the closed set
already. It is embedded, so `idx_memory_node_unembedded`'s predicate (the
worker's literal `kind IN (...)`, which must imply it) names it too.

No backfill here: a node is a `memory_search` hit, and the shadow's rule is
that nothing changes a prompt before `[persona] render` flips (45 §6). The
store projects on each write only while the switch is on; the lines written
during the shadow join the index through the flip procedure's
`nora memory rebuild` (45 §8), never through the queue, whose drain runs
whether or not the switch is on.

Follows `0122_message_mention_persons`. `uv run alembic heads` must print
exactly ONE line.

Revision ID: 0123_persona_line_index
Revises: 0122_message_mention_persons
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0123_persona_line_index"
down_revision = "0122_message_mention_persons"
branch_labels = None
depends_on = None

_KINDS = (
    "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character', "
    "'outing', 'stay', 'trip', 'person', 'room', 'unit', 'nora', 'place', "
    "'social', 'social_media', 'social_location'"
)
_EMBEDDED = (
    "'message', 'media', 'note', 'memo', 'event', 'lesson', 'character', "
    "'social', 'social_media', 'social_location'"
)
_LINE = "'persona_line'"


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
    _kinds(f"{_KINDS}, {_LINE}", f"{_EMBEDDED}, {_LINE}")


def downgrade() -> None:
    # Derived rows (NOT_RESTORED): a rebuild on the older code writes none.
    op.execute("DELETE FROM memory_edge WHERE src_kind = 'persona_line' "
               "OR dst_kind = 'persona_line'")
    op.execute("DELETE FROM memory_node WHERE kind = 'persona_line'")
    _kinds(_KINDS, _EMBEDDED)
