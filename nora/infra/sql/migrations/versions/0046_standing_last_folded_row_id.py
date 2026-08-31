"""standing.last_folded_row_id -- the row-id watermark the fold reads since
(design/standing-watermark/20).

**Why a column and not a timestamp or a comparison.** The standing fold used
to read the LAST 40 rows of the conversation -- the reply path's window --
and a conversation that passed 40 messages between tags lost the middle: the
position cannot inherit context it never saw. The fix is a cursor: every fold
reads `since(last_folded_row_id)`, capped at `[standing] fold_batch` (200),
and the watermark advances to the last row the fold read -- in the SAME
statement as the position, so a crash between the two can never leave a
position that claims to have read further than it did.

**Row id, not timestamp.** Timestamps are platform-shaped (Discord snowflakes
and Signal epoch ms do not share a scale) and two messages in one conversation
can share a ts. Row id is monotonic and unambiguous, and the cursor pattern
already exists in `RecordStore.since`.

**NULLABLE, and null is the only "never folded".** A conversation that has
never folded reads from the beginning (`after_id=0` -- the cold-start case,
rollup in 30-rollup.md). A sentinel that could compare equal to a real row id
is a watermark satisfiable by accident, so there is no `0`-means-never state;
the same argument migration 0037 already made for `consumed_verdict_tag`.

**NO BACKFILL, deliberately.** Existing rows get NULL, which means "read from
the beginning" -- the first fold after the upgrade re-reads the whole
conversation in batches. That is the correct reading of their history (they
never folded a watermark), not a migration artefact.

Revision ID: 0046_standing_last_folded_row_id
Revises: 0045_places_address
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_standing_last_folded_row_id"
down_revision = "0045_places_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standing", sa.Column("last_folded_row_id", sa.BigInteger, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("standing", "last_folded_row_id")
