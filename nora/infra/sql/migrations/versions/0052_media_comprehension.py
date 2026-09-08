"""media comprehension — `title` and `content` beside `description` (issue #116).

`media_objects` held ONE LLM-derived field, `description`, and it was the only
thing a sighting of a photo could ever leave behind. Issue #116's story is what
that costs: ten photos of a meal, the whole group replying around them, and the
subject of the conversation is the one thing memory does not hold.

Three fields, one model pass. The read of an image or a video is the expensive
part; extra output fields on the same call are near-free, so one pass produces:

  title       a handle for the thing — a few words, what it IS
  description one or two sentences — what it SHOWS
  content     everything readable/legible in it, in full

`description` keeps its meaning and its rows; nothing is backfilled and nothing
is rewritten. An existing row simply has two empty columns until something reads
its bytes again, which is exactly the state it was in before — the transcript
renders `[image a3f2c1]` for it, claiming nothing.

Server default `''` and NOT NULL, matching `description`'s own shape on this
table: a media object with no comprehension yet is an INDEX ENTRY, and the
column that says "not read yet" must be the same empty string on every row
rather than a NULL on old rows and `''` on new ones.

Autogenerate would have produced this correctly; it is hand-written anyway
because `tests/infra/test_schema_parity.py` (not `alembic check`) is this
repo's schema gate and the reasoning above belongs beside the DDL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_media_comprehension"
down_revision = "0051_reminder_subday_recurrence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_objects",
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "media_objects",
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("media_objects", "content")
    op.drop_column("media_objects", "title")
