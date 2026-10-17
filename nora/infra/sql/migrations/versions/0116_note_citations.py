"""note_revision.citations: the ids and Rollup keys a note revision cites.

design/unified-memory/00-intent.md «Sequence» step 4 and
25-memory-graph.md §1: `cites` edges exist only for stores that store
citations. Memos, lessons and Character revisions do; a note's citations sat
only in its body's brackets, so the memory index drew no `cites` edge from a
note and a search walking out of a message never reached the note resting on
it. The column holds `memo_revision.citations`' form (ids as digits, keys as
`rollup(...)`), derived from the body by `NoteRevision.citations`.

THE BACKFILL. Every stored revision gets what its body cites, read with the
same pure pattern every writer now stores through
(`nora.domain.note_body.citations_in`), so a body that predates the schema
answers too (none at all is an empty array, a real state). `note_revision` is
APPEND-ONLY, and the UPDATE here is a migration's, as `0073_lineage`'s was.
Every note is then put on `memory_projection_queue`, `0113`'s way back to a
source: the runtime's drain re-projects each one with its `cites` edges, with
no rebuild.

# ponytail: the backfill imports the live reader rather than a frozen copy
# (`0104_external_files` is the precedent); a later change to it changes what
# a fresh database's backfill finds, which matters only to an empty table.

Follows `0115_watermark_bigint`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0116_note_citations
Revises: 0115_watermark_bigint
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from nora.domain.note_body import citations_in

revision = "0116_note_citations"
down_revision = "0115_watermark_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("note_revision", sa.Column("citations", pg.ARRAY(sa.Text)))
    conn = op.get_bind()
    update = sa.text(
        "UPDATE note_revision SET citations = :c WHERE id = :i"
    ).bindparams(sa.bindparam("c", type_=pg.ARRAY(sa.Text)))
    for row_id, body in conn.execute(sa.text("SELECT id, body FROM note_revision")).all():
        conn.execute(update, {"c": list(citations_in(body)), "i": row_id})
    op.alter_column("note_revision", "citations", nullable=False)
    op.execute(
        "INSERT INTO memory_projection_queue (kind, source_id, last_error) "
        "SELECT 'note', id::text, 'Reprojection' FROM note "
        "ON CONFLICT (kind, source_id) DO NOTHING"
    )


def downgrade() -> None:
    # Derived rows (NOT_RESTORED): the older code projects a note with no
    # `cites` edge, so the ones this revision's projection wrote go.
    op.execute("DELETE FROM memory_edge WHERE src_kind = 'note' AND rel = 'cites'")
    op.drop_column("note_revision", "citations")
