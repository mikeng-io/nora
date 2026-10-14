"""messages.external_files_json -- a shared file link is recorded as a handle.

design/external-files/00-design.md §1 (build step S1): each link a provider
recognises as one of its files (a Google Doc, Sheet, Slides deck or Drive file)
is recorded beside its message as `{provider, file_id, url[, resource_key]}`.
`media_json`'s shape: one TEXT column holding a JSON array, NULL when there is
none, unindexed.

THE BACKFILL. The render that shows a model `[Google Doc link c41d09]` instead of the URL
works from the body, so it already covers every row stored before this column;
the backfill is what makes those rows' codes RESOLVE as well, so an old link
is not a handle that answers «nothing found». It runs the same pure recogniser
ingest runs (`nora.domain.external_files.file_refs_in`), over the human rows
whose body mentions a Google host, which the `LIKE` pre-filter keeps to a
handful. No network call. `messages` is in `HISTORY_TABLES`, so each UPDATE
is captured as a history row, as `0060_message_edit`'s backfill was.

# ponytail: the backfill imports the live recogniser rather than a frozen copy
# (`0041_fact_term_ceilings` is the precedent); a later change to it changes
# what a fresh database's backfill finds, which matters only to an empty table.

Revision ID: 0104_external_files
Revises: 0103_memos
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from nora.domain.external_files import external_files_json, file_refs_in

revision = "0104_external_files"
down_revision = "0103_memos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("external_files_json", sa.Text))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "select id, body from messages "
        "where is_nora = 0 and body ilike '%google.com/%'"
    )).all()
    for row_id, body in rows:
        text = external_files_json(file_refs_in(body))
        if text is not None:
            conn.execute(
                sa.text("update messages set external_files_json = :t where id = :i"),
                {"t": text, "i": row_id},
            )


def downgrade() -> None:
    op.drop_column("messages", "external_files_json")
