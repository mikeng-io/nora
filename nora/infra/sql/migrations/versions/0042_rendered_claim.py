"""rendered_claim -- what she actually rendered, which nothing recorded

`design/dreamer-agent-loop/31-recognition.md` §3. Step 3 of the correction loop
is "RECOGNISE a member contradicting a claim Nora just RENDERED", and nothing in
the codebase records what she rendered. `TagTrace` keeps the turn's `messages`
and `transcript`; it does not keep which `fact` or `room_lexeme` rows went into
the prefill.

**Recovering them by parsing the block back out of a stored prefill would be
INTERPRETING TWICE** -- the spine's first rule, and the way two copies drift. So
the render site records it, because that is the one place the row ids are
already in hand.

**This table is also the measurement.** Without it, "Nora rendered it" is an
assumption. With it, it is a read -- the difference between a `measure` step and
a proxy.

**UPSERT, one row per rendered claim per conversation.** Keyed on `tag_id` as
well it would write a fresh row for the same claim on every consecutive tag --
forty rows for one fact in an active conversation, read back with `DISTINCT ON`
-- for provenance `tag_trace` already carries. `last_tag_id` records which turn
last put the row in scope and is deliberately not part of the key.

**`row_key` is TEXT.** A fact's is its uuid; a lexeme's is its composite key,
because `room_lexeme`'s primary key is `(scope_id, canonical_form, sense)` --
three TEXT columns with no surrogate id, and the compound key is the fix for a
real overwrite bug rather than an oversight. A `uuid` column here, which is what
the design's first draft declared, would have been unimplementable for half the
rows it is specified to hold.

**A WORKING SET, not an audit trail**, so it is pruned on the recognition window
and nothing reads it afterwards. The audit trail is the Langfuse trace and the
`tag_trace` row.

**No FOREIGN KEY on `row_key`**, for the reason it cannot have one: it holds two
different tables' keys in one column, and one of them is not a uuid. A dangling
key reads as "rendered something since superseded", which is the correct answer
and the same one a present row gives.

Revision ID: 0042_rendered_claim
Revises: 0041_fact_term_ceilings
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_rendered_claim"
down_revision = "0041_fact_term_ceilings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rendered_claim",
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("row_key", sa.Text, nullable=False),
        sa.Column("surface", sa.Text, nullable=False),
        sa.Column("workspace", sa.Text),
        sa.Column("last_tag_id", sa.Text, nullable=False),
        sa.Column("last_rendered_at", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("conversation_id", "kind", "row_key"),
        sa.CheckConstraint(
            "kind in ('fact', 'lexeme')", name="rendered_claim_kind_check",
        ),
        sa.CheckConstraint(
            "length(btrim(surface)) > 0",
            name="rendered_claim_surface_is_named_check",
        ),
    )
    # `DESC` is authored by hand rather than left to autogenerate, the same
    # care `idx_messages_conv_ts` needs: the read is "newest first inside the
    # window", and an ascending index serves it with a sort on top.
    op.execute(
        "create index idx_rendered_claim_window on rendered_claim "
        "(conversation_id, last_rendered_at desc)"
    )


def downgrade() -> None:
    op.drop_index("idx_rendered_claim_window", table_name="rendered_claim")
    op.drop_table("rendered_claim")
