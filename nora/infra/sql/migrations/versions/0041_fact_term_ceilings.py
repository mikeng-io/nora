"""fact / fact_candidate term ceilings -- the join key was unbounded

`design/dreamer-agent-loop/25-knowledge-pipeline.md` §9.1 gathers the tier's
caps and finds two columns with none at all: `subject_id` and `object_literal`.

**It is the note-body defect, and it bites harder here.** The subject is a JOIN
KEY (`21-substrate-contract.md` §6.2) -- `fact_candidate_triple_key` is
`(workspace, subject_id, predicate, object)`, and the adjudicator maps a verdict
back to its candidate by that triple -- so an unbounded subject is an unbounded
index entry on the one column every read path uses. A model-authored string
reaching a durable column with no bound is F14's shape, and it landed on the
column least able to absorb it.

**Two layers, doing two different jobs, and only one of them is here.** These
CHECKs are the CEILING: a backstop against a config typo or a future writer
nobody has thought of. The OPERATING caps are `[facts] max_term_chars` (200) and
`max_predicate_chars` (100), refused above these ceilings at config load and
enforced at every write -- `30-review-response.md` §5's correction, that a
scheme which only refuses a config above its ceiling leaves oversized rows
satisfying the CHECK while defeating the prompt budget the cap exists to
protect.

**The numbers are imported, never repeated.** `FACT_TERM_CEILING_CHARS` and
`FACT_PREDICATE_CEILING_CHARS` live in `nora.domain.facts` and are read by the
dataclass, by `schema.py` and by this file. The note tier shipped `32000`
hardcoded in both its migration and its schema, and the schema-parity gate could
not see it: both carried the same wrong number, so they agreed with each other
and with nothing else. A literal in two places is a constraint nobody can
change.

**`fact_candidate` gets the same ceilings as `fact`**, because a candidate
becomes a fact. A term storable as one and not the other would push the refusal
to adjudication -- after the research loop has already been spent on it.

Safe on the live database: `fact` and `fact_candidate` are both empty
(verified). On a populated one, `ALTER TABLE ... ADD CONSTRAINT` validates
existing rows and would fail loudly naming the constraint, which is the correct
behaviour -- a silent truncation of somebody's claim is not.

Revision ID: 0041_fact_term_ceilings
Revises: 0040_fact_asserter
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op

from nora.domain.facts import (
    FACT_PREDICATE_CEILING_CHARS,
    FACT_TERM_CEILING_CHARS,
)

revision = "0041_fact_term_ceilings"
down_revision = "0040_fact_asserter"
branch_labels = None
depends_on = None

_CHECKS = (
    (
        "fact",
        "fact_term_fits_the_ceiling_check",
        f"length(subject_id) <= {FACT_TERM_CEILING_CHARS} "
        f"and length(object_literal) <= {FACT_TERM_CEILING_CHARS}",
    ),
    (
        "fact",
        "fact_predicate_fits_the_ceiling_check",
        f"length(predicate) <= {FACT_PREDICATE_CEILING_CHARS}",
    ),
    (
        "fact_candidate",
        "fact_candidate_term_fits_the_ceiling_check",
        f"length(subject_id) <= {FACT_TERM_CEILING_CHARS} "
        f"and length(object) <= {FACT_TERM_CEILING_CHARS}",
    ),
    (
        "fact_candidate",
        "fact_candidate_predicate_fits_the_ceiling_check",
        f"length(predicate) <= {FACT_PREDICATE_CEILING_CHARS}",
    ),
)


def upgrade() -> None:
    for table, name, expr in _CHECKS:
        op.create_check_constraint(name, table, expr)


def downgrade() -> None:
    for table, name, _expr in _CHECKS:
        op.drop_constraint(name, table, type_="check")
