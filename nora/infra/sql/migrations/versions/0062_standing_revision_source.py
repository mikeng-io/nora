"""standing_revision.source -- marks a manual operator edit apart from a fold
(design/dreamer-agent-loop/26-standing.md, dated section 2026-09-25).

WHAT WAS MISSING. Every `standing_revision` row looked like a fold, because
`SqlStandingStore.write` had exactly one caller shape: the debrief stage and
the event-path rollup. On 2026-09-24 an operator-approved manual purge edited
two rows through that same `write`, and the history now records those edits
as folds -- indistinguishable from the model's own work, with no column that
could say otherwise.

WHY A COLUMN, NOT A COMPARISON. Nothing about a fold-written row and a
hand-edited row differs in shape -- same columns, same ceiling, same
constraints -- so there is no property of the row itself a reader could use
to infer which one wrote it. The distinction has to be recorded at write time
or it does not exist.

BACKFILL IS THE CORRECT VALUE, NOT A GUESS. `server_default 'fold'` covers
every row already in the table, and it is true of every one of them: the
`source` column did not exist before this migration, so nothing but the fold
path could have written any row it applies to (the two operator-edited rows
from 2026-09-24 predate this migration too, and are `'fold'` under it --
recorded as a known gap in the design section rather than hand-patched here,
since a migration back-dating provenance it did not observe is the same
mistake `0059_standing_revision`'s own no-backfill argument was written
against).

CHECK, NOT AN ENUM TYPE. Two values, one column, no other consumer --
`ck_derived_places_kind` and `standing_revision`'s own two existing
constraints are the same shape on this schema, and a Postgres ENUM would be a
second thing to migrate the day a third source is ever needed.

Revision ID: 0062_standing_revision_source
Revises: 0061_video_readings
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_standing_revision_source"
down_revision = "0061_video_readings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standing_revision",
        sa.Column(
            "source", sa.Text, nullable=False,
            server_default=sa.text("'fold'"),
        ),
    )
    op.create_check_constraint(
        "standing_revision_source_is_fold_or_operator_check",
        "standing_revision",
        "source IN ('fold', 'operator')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "standing_revision_source_is_fold_or_operator_check",
        "standing_revision",
        type_="check",
    )
    op.drop_column("standing_revision", "source")
