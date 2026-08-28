"""fact.object — the literal always survives, the entity id becomes a real id

`design/dreamer-agent-loop/21-substrate-contract.md` §6.4 catalogued three
shipped defects on the object path and §10 decided the fix. All three are one
column pair, and all three are free today: `fact` has zero rows on the live
database.

**Defect 1 — the id column was not an id.** `object_entity_id` was `sa.Text`
against `fact_entity.id`, which is `uuid`, with **no foreign key between
them** — the only FK into the entity table belonged to the note tier. So the
"node reference" half of the exactly-one CHECK referenced nothing: a second
free-text column wearing an id's name, which would have accepted `"House
House"` as readily as a uuid and never told anybody. It is now `uuid` with the
FK, so the reference is enforced by the database rather than asserted by a
comment.

**Defect 2 — the negative cache broke silently.** `FactCandidateReader.pending`
dedupes a candidate against standing facts by comparing `object_literal`. Under
the old CHECK an entity-object fact had `object_literal` NULL, and `NULL = 'x'`
is NULL — so the claim was never deduped, was re-adjudicated every tick
forever, and because it also read as *contradicting* its own candidate it
ranked FIRST each time. That is precisely the recurrence `fact_candidate`
exists to stop. Reproduced with controls against the real schema before this
migration was written: the same triple with a literal object dedupes, with an
entity object it stays pending, and a no-standing-fact control also stays
pending (so the observation is not just "pending is the default").

**Defect 3 — two read paths printed the raw column.** `fact_render` and
`fact_adaptor` both fell back to emitting `object_entity_id` verbatim when
`object_literal` was NULL, which renders a bare uuid into Nora's prompt — the
identifier-in-the-prompt shape `dream_screen` drops silently.

**The fix is the same one for all three: `object_literal` becomes NOT NULL and
`object_entity_id` becomes an ADDITIVE, nullable resolution pointer.** §6.5
framed this as a choice and §10 decided it, on three grounds with the third
decisive: every shipped read path keeps working; defect 2 disappears rather
than needing a branch; and **promotion stops being destructive**, so the
surface form the room actually used survives resolution. That last is the
property `23-retrieval.md` §6.2 depends on — the embedded text is the rendered
triple, and a triple whose object was eaten by canonicalisation embeds
something the room never said.

What the schema gives up is the "exactly one object" invariant: a row no longer
says on its own whether the object *is* the node or merely resembles it. That
was the argument against, it was weighed, and it lost to a non-destructive
promotion path.

**The type change is `USING object_entity_id::uuid`, and it is safe only
because the table is empty.** A non-empty table would need a validation pass
first — any row whose text was not uuid-shaped would fail the cast, which is
exactly the class of row the missing FK was admitting. Verified zero rows
before writing this.

Revision ID: 0038_fact_object_relax
Revises: 0037_standing_consumed_verdict
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0038_fact_object_relax"
down_revision = "0037_standing_consumed_verdict"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order matters: the CHECK forbids the state every later statement
    # produces, so it goes first.
    op.drop_constraint("fact_one_object_check", "fact", type_="check")
    op.alter_column(
        "fact",
        "object_entity_id",
        existing_type=sa.Text(),
        type_=pg.UUID(as_uuid=False),
        postgresql_using="object_entity_id::uuid",
        existing_nullable=True,
    )
    op.create_foreign_key(
        "fact_object_entity_id_fkey", "fact", "fact_entity",
        ["object_entity_id"], ["id"],
    )
    op.alter_column(
        "fact", "object_literal", existing_type=sa.Text(), nullable=False,
    )


def downgrade() -> None:
    # `object_literal` NOT NULL -> nullable is safe in either direction; the
    # CHECK is not, because a row with BOTH set (the state this migration
    # exists to allow) violates it. The downgrade therefore refuses rather
    # than silently dropping data to satisfy a constraint the operator is
    # reverting to — an unauditable delete is worse than a failed downgrade.
    both = op.get_bind().execute(
        sa.text(
            "select count(*) from fact "
            "where object_entity_id is not null and object_literal is not null"
        )
    ).scalar_one()
    if both:
        raise RuntimeError(
            f"{both} fact row(s) carry both an entity id and a literal, which "
            "`fact_one_object_check` forbids. Resolve them before "
            "downgrading; this migration will not choose which half to "
            "destroy."
        )
    op.alter_column(
        "fact", "object_literal", existing_type=sa.Text(), nullable=True,
    )
    op.drop_constraint("fact_object_entity_id_fkey", "fact", type_="foreignkey")
    op.alter_column(
        "fact",
        "object_entity_id",
        existing_type=pg.UUID(as_uuid=False),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.create_check_constraint(
        "fact_one_object_check",
        "fact",
        "(object_entity_id is null) <> (object_literal is null)",
    )
