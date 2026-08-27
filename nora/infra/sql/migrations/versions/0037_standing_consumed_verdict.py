"""standing.consumed_verdict_ms -- S2's acted-on watermark
(design/dreamer-agent-loop/26-standing.md sec. 2).

**Why a column and not a comparison.** The re-derivation trigger asks "has an
ungrounded verdict arrived that this position has not been re-derived from".
The first implementation answered it by comparing the latest verdict's
`created_at` against the position's `updated_at` -- using the position's own
last write as a PROXY for "a rebuild consumed that verdict".

The proxy is falsifiable by a path that moves `updated_at` without consuming
anything, and that path is ordinary: `TurnVerdictStore.latest` fails for ONE
turn, the trigger correctly declines to fire (an unreadable instrument is
R-27's «I could not check», never «there was nothing»), the fold then revises
normally and writes the position with a NEWER `updated_at` -- and from that
turn on the comparison is false forever. The ungrounded verdict is not
deferred, it is gone. Driven through the real stage over three turns before
this migration was written: the verdict was dropped and the by-trigger counter
read zero, which sec. 6 interprets as the trigger being healthy.

So the property "this verdict has been acted on" is recorded rather than
inferred. NULLABLE, and null is not zero: a sentinel that compares equal to a
real `created_at` is a watermark that can be satisfied by accident, and every
row that exists before this migration has genuinely never had a re-derivation
act on a verdict.

NO BACKFILL, deliberately. Existing rows get NULL, which means "nothing
consumed yet" -- so the first ungrounded verdict after the upgrade triggers one
re-derivation per conversation that has one outstanding. That is the correct
reading of their history, not a migration artefact: under the shipped code
those verdicts were never acted on.

No CHECK. `Standing.__post_init__` refuses a negative, and unlike the body
ceiling there is no second writer this constraint would be protecting against
-- the column is written by one sink and read by one trigger. A constraint
whose only purpose is to restate a type is noise in the schema-parity gate.

Revision ID: 0037_standing_consumed_verdict
Revises: 0036_note_revision_provenance
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_standing_consumed_verdict"
down_revision = "0036_note_revision_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standing",
        sa.Column("consumed_verdict_ms", sa.BigInteger, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("standing", "consumed_verdict_ms")
