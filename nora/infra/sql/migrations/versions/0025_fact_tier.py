"""fact_entity / fact_candidate / fact — the fact tier, dormant
(2026-08-20 fact-tier design).

Three tables, no writer. A fact is a TRIPLE with the source that warrants
it, scoped to WORKSPACE — never a free-text statement (which no engine can
ever traverse) and never a claim about a person (Honcho's axis, not this
one). `fact_candidate` is the extraction stage's negative cache — without
`status='not_covered'` the same unprovable claim would be re-extracted and
re-adjudicated every tick forever. `fact_entity` resolves aliases
(`big walk` / `Big Walk` / 大步行) to one canonical subject or object so the
tier does not fragment into near-duplicates that can never contradict each
other. The tables land now, ahead of the Dreamer runtime that will write to
them, so that runtime has a sink that already exists and is already tested
when it arrives — see `nora/domain/facts.py` for the dataclasses these
columns must hold every instance of, and reject every instance they forbid.

**Every CHECK below is required BY THIS FILE, not by `schema.py` alone.**
`alembic check`/`compare_metadata` — what `tests/infra/test_schema_parity.py`
runs — is blind to CHECK constraints (CLAUDE.md; demonstrated on this repo
2026-08-20: deleting four CHECK constraints from `schema.py` alone left every
test green, schema parity included). A CHECK declared only on the
`sa.Table` in `schema.py` is documentation; this migration is what actually
puts it in the database, and `tests/infra/test_fact_schema.py` is what
proves each one live by attempting the exact insert it exists to refuse.

- `fact_instruction_sha256_check` — 64 lowercase hex or the row does not
  exist. `fact.instruction_sha256` is NOT NULL (unlike the four Dreamer
  tables `0023` touched, which hold rows written before the column existed);
  `fact` never will, so there is no NULL branch to carry.
- `fact_model_is_named_check` — non-empty after `btrim`, the same shape
  `0024` used and for the same reason: a model name has no provable pattern
  (no width, no alphabet, no registry this process can consult), so the
  CHECK asserts only what IS provable — that the stamp is a real name and
  not `''`/`'   '`, which are non-NULL to every query asking "which rows are
  attributable" and would turn R-27's `unavailable` into a false `ok`.
- `fact_one_object_check` — exactly one of `object_entity_id` /
  `object_literal`, matching `nora.domain.facts.Fact.__post_init__`'s
  `has_entity == has_literal` guard. The domain type refuses what the column
  would already refuse; this is the enforcement the domain type assumes
  exists underneath it.
- `fact_supersession_is_timed_check` — `superseded_by` implies
  `superseded_at`. A merge record without its time is exactly the
  unauditable supersession Graphiti's own issue #1771 documents.
- `fact_candidate_status_check` — `pending` / `adjudicated` / `not_covered`,
  matching `nora.domain.facts.CandidateStatus`.

`idx_fact_standing` serves the one read on the turn's critical path: the
standing facts for a set of subjects in one workspace. It is PARTIAL on
`superseded_at IS NULL` because a superseded fact is history and is never
rendered — same shape as `idx_messages_dream_lag`'s partial predicate two
migrations up the chain, and identically invisible to `alembic check`, which
is why `test_schema_parity.py`'s `EXPECTED_DESC_INDEXES`/index tests exist at
all (though this index carries no DESC column, so it needs no entry there —
only the CHECK/existence coverage `test_every_table_in_the_metadata_exists`
and `test_the_metadata_matches_the_migrated_database` already give it for
free, being parameterised over `METADATA`).

Revision ID: 0025_fact_tier
Revises: 0024_dreamer_model_provenance
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0025_fact_tier"
down_revision = "0024_dreamer_model_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_entity",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace", sa.Text, nullable=False),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("aliases", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("workspace", "canonical_name",
                            name="fact_entity_workspace_name_key"),
    )
    op.create_table(
        "fact_candidate",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace", sa.Text, nullable=False),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("predicate", sa.Text, nullable=False),
        sa.Column("object", sa.Text, nullable=False),
        sa.Column("first_seen_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False,
                  server_default=sa.text("1")),
        sa.Column("observed_in", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("last_adjudicated_at", sa.BigInteger),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.CheckConstraint("status in ('pending', 'adjudicated', 'not_covered')",
                           name="fact_candidate_status_check"),
        sa.UniqueConstraint("workspace", "subject_id", "predicate", "object",
                            name="fact_candidate_triple_key"),
    )
    op.create_table(
        "fact",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace", sa.Text, nullable=False),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("predicate", sa.Text, nullable=False),
        sa.Column("object_entity_id", sa.Text),
        sa.Column("object_literal", sa.Text),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("source_excerpt", sa.Text, nullable=False),
        sa.Column("valid_from", sa.BigInteger, nullable=False),
        sa.Column("valid_to", sa.BigInteger),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("superseded_at", sa.BigInteger),
        sa.Column("superseded_by", pg.UUID(as_uuid=False)),
        sa.Column("instruction_sha256", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.CheckConstraint("instruction_sha256 ~ '^[0-9a-f]{64}$'",
                           name="fact_instruction_sha256_check"),
        sa.CheckConstraint("length(btrim(model)) > 0",
                           name="fact_model_is_named_check"),
        sa.CheckConstraint("(object_entity_id is null) <> (object_literal is null)",
                           name="fact_one_object_check"),
        sa.CheckConstraint("(superseded_by is null) or (superseded_at is not null)",
                           name="fact_supersession_is_timed_check"),
    )
    op.create_index("idx_fact_standing", "fact", ["workspace", "subject_id"],
                    postgresql_where=sa.text("superseded_at is null"))


def downgrade() -> None:
    op.drop_index("idx_fact_standing", table_name="fact")
    op.drop_table("fact")
    op.drop_table("fact_candidate")
    op.drop_table("fact_entity")
