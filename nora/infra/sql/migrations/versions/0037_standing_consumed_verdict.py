"""standing.consumed_verdict_tag -- S2's acted-on watermark
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
inferred.

**IT CARRIES `tag_id`, NOT `created_at`, and that is the same lesson one turn
later.** The first version of this column held the consumed verdict's
millisecond -- which is a PROXY for its identity, and two ungrounded verdicts
sharing one millisecond collapse to a single watermark value, so the second is
never acted on. Exactly the substitution this column was added to remove, in the
column itself. And the premise is asserted twice in this codebase already: both
`latest_ungrounded` implementations justify their `tag_id` tie-break with "two
turns in one millisecond are reachable". If that is true the hole is real; if it
is false the tie-breaks are decoration. It cannot be both. `tag_id` is the
verdict's own primary key, so the watermark now names the row rather than
describing it.

NULLABLE, and null is not the empty string: a sentinel that could compare equal
to a real `tag_id` is a watermark satisfiable by accident, and every row that
exists before this migration has genuinely never had a re-derivation act on a
verdict.

NO BACKFILL, deliberately. Existing rows get NULL, which means "nothing
consumed yet" -- so the first ungrounded verdict after the upgrade triggers one
re-derivation per conversation that has one outstanding. That is the correct
reading of their history, not a migration artefact: under the shipped code
those verdicts were never acted on.

No FOREIGN KEY to `turn_verdict.tag_id`, deliberately. Verdicts are retained on
their own terms and a pruned one must not block a position from being written;
the watermark is a record of what was acted on, not a live reference. A dangling
tag reads as "consumed something no longer here", which is the correct answer
and the same one a present row gives.

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
        sa.Column("consumed_verdict_tag", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("standing", "consumed_verdict_tag")
