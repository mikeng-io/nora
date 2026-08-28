"""fact / room_lexeme gain a `disputed` state -- the correction loop's one new column set

`design/dreamer-agent-loop/31-recognition.md` §7, implementing 30 §2.1's D25.

**A dispute MARKS a row; it does not mute it.** 30 §2.1, 29 §5 and 19 §4 all
decide a three-state render -- standing, disputed-and-marked, gone -- and only
§2.1.1's step-4 shorthand ("IT STOPS RENDERING THIS TURN") collapsed it to two.
A disputed row stops rendering as a STANDING CLAIM and starts rendering AS A
DISPUTE, carrying who disputed it.

That distinction is what makes a false positive survivable. Under suppression,
one message from one member removed a true row from every future turn, for
everyone in the workspace, with no TTL and no clear path while the Dreamer is
off -- a memory denial-of-service available to anyone in the room, in a design
whose own §2.3 says abuse is a design input.

**Four columns, not a boolean.** `disputed_at` is the state and the CAS target;
`disputed_by` names the asserter, which is what makes a prank legible;
`disputed_reason` is what they actually claimed, which is the adjudicator's
input at step 5 and was missing from the design's first draft; `disputed_tag_id`
is the turn that recognised it, so a dispute is re-derivable from a trace.

`disputed_by` is NULLABLE: an unmapped sender has no `person_id` at ingest, and
a sentinel would be a `person_id` that compares equal to nobody.

**BOTH TIERS**, because one correction contradicts a lexeme row and a fact row
at once (31 §6). On `room_lexeme` they survive `rescore` -- which recomputes
that table from `language_observation` on every consolidation -- only because
its upsert names its `set_` columns explicitly. That is correct behaviour
arrived at by accident of a list, so it is pinned by a test rather than trusted.

No CHECK tying the four together. `disputed_at` set alone is the ordinary state
for an unmapped sender or a dispute raised deterministically with no reason
text; requiring them together would refuse exactly the cases the loop must
handle.

Revision ID: 0043_disputed
Revises: 0042_rendered_claim
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_disputed"
down_revision = "0042_rendered_claim"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("disputed_at", sa.BigInteger),
    ("disputed_by", sa.Text),
    ("disputed_reason", sa.Text),
    ("disputed_tag_id", sa.Text),
)
_TABLES = ("fact", "room_lexeme")


def upgrade() -> None:
    for table in _TABLES:
        for name, kind in _COLUMNS:
            op.add_column(table, sa.Column(name, kind, nullable=True))
    # The read the loop makes: "which rows in this workspace are disputed".
    # Partial, because a disputed row is the rare case and a full index would
    # be a majority-class predicate the planner will not choose -- the same
    # reasoning `idx_messages_dream_lag` records for its own partial index.
    op.execute(
        "create index idx_fact_disputed on fact (workspace) "
        "where disputed_at is not null"
    )


def downgrade() -> None:
    op.drop_index("idx_fact_disputed", table_name="fact")
    for table in _TABLES:
        for name, _kind in _COLUMNS:
            op.drop_column(table, name)
