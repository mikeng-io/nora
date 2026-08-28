"""fact_asserter -- WHO claimed it, which `established_by` never said

`design/dreamer-agent-loop/21-substrate-contract.md` §7. `established_by` holds
the RUN that produced a fact -- the tick. Nothing anywhere recorded the human
who asserted the claim, and for a tier whose job is belief that is the wrong
direction: corroboration and disagreement are properties of WHO said it.

**Two consequences of the omission, both live today.**

`occurrence_count` conflates *"one person said it three times"* (emphasis) with
*"three people said it once"* (consensus), and the selection filter that decides
what is worth a research loop ranks on exactly that number. And there is no
representation at all for the room disagreeing with itself -- a different thing
from the web contradicting the room, and arguably the more interesting signal.

**It was lost twice and only one loss is argued.** The extraction prompt renders
`id: body`, dropping the speaker deliberately, and that argument is sound and
untouched here: naming the speakers is how a batch of ordinary chat starts
looking like a batch of claims about the people in it -- measured 2026-08-27 at
five person-claims out of six edges, with the roster supplied. The SECOND loss
is at promotion, and it is argued nowhere: a candidate carries `observed_in`,
the messages each claim came from, and `fact` had no such column. The link was
complete on the candidate and absent on the row it became.

**So the fix is a join in code, not a change to the prompt.** `message_id ->
sender_id -> person_id`, from rows the database already holds, at the moment the
fact is written. The model contributes nothing but the message ids it already
returns and that `accept` already validates against the batch. The objective is
unchanged, the prompt is unchanged, the model still never sees a name.

**A junction, not an array column**, and the repo has already made this exact
call once: `fact_premise` exists rather than a `premise_ids` array because
"which facts depend on this one" is an unindexable array-containment scan with
the array and an indexed lookup with the table. The junction SHAPE is what makes
"what has this person been telling us" indexable at all -- which is also what
makes an abusive `stated` claim attributable rather than anonymous
(`30-review-response.md` §2.3). **The index itself is deliberately NOT created
here**: nothing filters this table by `person_id` yet, and an index with no
query is a write cost paid for a read nobody makes. It arrives with the
sender-scoped verb that will issue it.

**Grain: (fact_id, person_id, message_id)** -- the finest that loses nothing.
Three people once is three rows over three persons; one person three times is
three rows over one person. Anything coarser destroys the distinction the table
exists to preserve.

**No FOREIGN KEY on `message_id`**, the same call `standing.consumed_verdict_tag`
makes and for the same reason: `prune_all` deletes messages by conversation, and
a pruned message must not delete the attribution. Who said a thing is a fixed
historical fact; the message is the evidence, and losing the evidence does not
unmake the assertion. `fact_id` DOES cascade, because
`30-review-response.md` §7 makes run-scoped rollback a supported operation and
an orphan asserter row would outlive the fact it attributes.

Revision ID: 0039_fact_asserter
Revises: 0038_fact_object_relax
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0039_fact_asserter"
down_revision = "0038_fact_object_relax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_asserter",
        sa.Column("fact_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("message_id", sa.Integer, nullable=False),
        sa.Column("asserted_at", sa.BigInteger, nullable=False),
        sa.ForeignKeyConstraint(
            ["fact_id"], ["fact.id"],
            name="fact_asserter_fact_id_fkey", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_id", "person_id", "message_id"),
        # '' and '   ' are non-NULL to every query asking which claims are
        # attributable, so a blank person_id turns R-27's `unavailable` into a
        # false `ok` -- the same argument `fact_model_is_named_check` makes one
        # table over.
        sa.CheckConstraint(
            "length(btrim(person_id)) > 0",
            name="fact_asserter_person_is_named_check",
        ),
    )
    # `fact_premise` cascades too, from here. Without it, deleting a fact is
    # blocked by its premise rows before `fact_asserter`'s cascade can run —
    # so the run-scoped rollback this cascade exists for could never happen,
    # and the justification was true of one table and false of the operation.
    # Both tables are empty, so this is a constraint swap and nothing else.
    op.drop_constraint("fact_premise_fact_id_fkey", "fact_premise",
                       type_="foreignkey")
    op.drop_constraint("fact_premise_premise_fact_id_fkey", "fact_premise",
                       type_="foreignkey")
    op.create_foreign_key(
        "fact_premise_fact_id_fkey", "fact_premise", "fact",
        ["fact_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fact_premise_premise_fact_id_fkey", "fact_premise", "fact",
        ["premise_fact_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fact_premise_fact_id_fkey", "fact_premise",
                       type_="foreignkey")
    op.drop_constraint("fact_premise_premise_fact_id_fkey", "fact_premise",
                       type_="foreignkey")
    op.create_foreign_key(
        "fact_premise_fact_id_fkey", "fact_premise", "fact", ["fact_id"], ["id"],
    )
    op.create_foreign_key(
        "fact_premise_premise_fact_id_fkey", "fact_premise", "fact",
        ["premise_fact_id"], ["id"],
    )
    op.drop_table("fact_asserter")
