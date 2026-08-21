"""note / note_revision / note_entity / note_candidate — the note tier, dormant
(2026-08-21 note-tier design).

Four tables and no writer yet. A note is TRACKED UNCERTAINTY, scoped to a
WORKSPACE — never a settled claim (that is a `fact`, with a warrant) and never a
conclusion about a person (Honcho's `(observer, observed)` axis). The tables land
ahead of the tools and the store so both have a sink that already exists and is
already tested when they arrive — see `nora/domain/notes.py` for the dataclasses
these columns must hold every instance of, and reject every instance they forbid.

**Every CHECK below is required BY THIS FILE, not by `schema.py` alone.**
`alembic check` / `compare_metadata` — what `tests/infra/test_schema_parity.py`
runs — is blind to CHECK constraints (CLAUDE.md; demonstrated on this repo
2026-08-20, when deleting four CHECKs from `schema.py` alone left every test
green, schema parity included). A CHECK declared only on the `sa.Table` is
documentation; this migration is what puts it in the database.

- `note_has_an_anchor_check` — a date, entities, or both. Without a referent
  there is nothing to look the note up by later, nothing to compare for a merge
  (§7.1 keys merge eligibility on `(anchor_date, entity_set)` and on nothing
  else), and no mechanical retirement (§7.4). "We should hang out sometime" is
  the case this refuses.
- `note_has_a_closing_condition_check` — non-empty after `btrim`, the shape
  `fact_model_is_named_check` uses and for the same reason: `'   '` is non-empty
  to every query asking "does this note say what would settle it". Retirement,
  promotion-readiness and a revisit's success criterion are all read off this one
  column, so a blank one silently disables three mechanisms.
- `note_title_fits_the_index_check` / `note_description_fits_the_index_check` —
  the index entry goes into EVERY tag, so these are prompt-budget invariants. In
  the database as well as the domain type because truncating at render is silent
  loss and the whole tier rests on the index being a complete listing.
- `note_replacement_implies_retirement_check` — **deliberately NOT `fact`'s
  biconditional.** `fact_supersession_is_timed_check` is
  `(superseded_by is null) = (superseded_at is null)` because a contradicted fact
  is always replaced by the corrected one. A note is not: retirement has no
  successor, because a dinner that happened is replaced by nothing. So this is a
  one-directional implication. That asymmetry is exactly why
  `DreamSink.supersede` takes `replaced_by: str | None` (DEC-130) rather than a
  required id, and a future edit "fixing" this into a biconditional to match the
  fact tier would break retirement outright.
- `note_strikes_are_not_negative_check` — the self-loop's counter.
- `note_revision_has_a_body_check`, `note_revision_names_its_writer_check` —
  `established_by` is load-bearing beyond provenance: the strike reset
  distinguishes a TAG-PATH revision from the revisitor's own, and a revision
  that cannot say who wrote it makes that breaker self-referential.

**`pg_trgm`, not `tsvector`, and the index covers every body rather than only
heads.** Two decisions, one deliberate deviation from the design document.

The extension choice was settled against the live database (spec §5.3): read back
from `nora-db-1` on 2026-08-21 — PostgreSQL 18.4 on musl — `pg_trgm` is available
and `zhparser` / `pgroonga` / `pg_bigm` are not available at all. The corpus is
mixed English and Cantonese and `to_tsvector('english', …)` does not segment
Chinese, so a body reading 婚禮 would be unfindable while the search reported
`nothing_found` — R-27's failure wearing a search result's clothes. A
CJK-segmenting extension needs a source-built image, the same packaging wall
DEC-129 hit with Apache AGE. Trigrams need no segmentation and no language
configuration to be wrong about; the cost is no stemming, which is stated rather
than discovered.

The deviation: §5.3 specifies the index is "maintained on the head revision
only". It cannot be, as a partial index — which revision is the head is a fact
about the `note` row, not a predicate over `note_revision`, so there is nothing
for `postgresql_where` to test. The property §5.3 actually wanted — **a search
never surfaces a body that is no longer the note's content** — is preserved by
the QUERY instead: `note_search` joins `note.head_revision_id = note_revision.id`
and so can only return heads. The index covers superseded bodies too and is
larger than it needs to be; at tens of notes per workspace that is not a cost
worth denormalising the head body onto `note` to avoid, and denormalising would
introduce a second copy of the body that could disagree with the first.

Revision ID: 0026_note_tier
Revises: 0025_fact_tier
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0026_note_tier"
down_revision = "0025_fact_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create extension if not exists pg_trgm")
    op.create_table(
        "note",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("uncertainty", sa.Text, nullable=False),
        sa.Column("closing_condition", sa.Text, nullable=False),
        sa.Column("anchor_date", sa.Integer),
        sa.Column("anchor_entity_ids", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("established_in", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("head_revision_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("retired_at", sa.BigInteger),
        sa.Column("replaced_by", pg.UUID(as_uuid=False),
                  sa.ForeignKey("note.id", name="note_replaced_by_fkey")),
        sa.Column("retire_reason", sa.Text),
        sa.Column("revisit_at", sa.BigInteger),
        sa.Column("strikes", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_wake_revision_id", pg.UUID(as_uuid=False)),
        sa.Column("event_id", pg.UUID(as_uuid=False)),
        sa.Column("reminder_id", pg.UUID(as_uuid=False)),
        sa.CheckConstraint(
            "anchor_date is not null or cardinality(anchor_entity_ids) > 0",
            name="note_has_an_anchor_check"),
        sa.CheckConstraint("length(btrim(closing_condition)) > 0",
                           name="note_has_a_closing_condition_check"),
        sa.CheckConstraint("length(btrim(title)) > 0 and length(title) <= 60",
                           name="note_title_fits_the_index_check"),
        sa.CheckConstraint(
            "length(btrim(description)) > 0 and length(description) <= 200",
            name="note_description_fits_the_index_check"),
        sa.CheckConstraint("replaced_by is null or retired_at is not null",
                           name="note_replacement_implies_retirement_check"),
        sa.CheckConstraint("strikes >= 0", name="note_strikes_are_not_negative_check"),
    )
    op.create_table(
        "note_revision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("note_id", pg.UUID(as_uuid=False),
                  sa.ForeignKey("note.id", name="note_revision_note_id_fkey"),
                  nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("parent_revision_id", pg.UUID(as_uuid=False)),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.CheckConstraint("length(btrim(body)) > 0",
                           name="note_revision_has_a_body_check"),
        sa.CheckConstraint("length(btrim(established_by)) > 0",
                           name="note_revision_names_its_writer_check"),
    )
    op.create_table(
        "note_entity",
        sa.Column("note_id", pg.UUID(as_uuid=False),
                  sa.ForeignKey("note.id", name="note_entity_note_id_fkey"),
                  primary_key=True),
        sa.Column("entity_id", pg.UUID(as_uuid=False),
                  sa.ForeignKey("fact_entity.id", name="note_entity_entity_id_fkey"),
                  primary_key=True),
    )
    op.create_table(
        "note_candidate",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace", sa.Text, nullable=False),
        sa.Column("probe", sa.Text, nullable=False),
        sa.Column("first_seen_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False,
                  server_default=sa.text("1")),
        sa.Column("observed_in", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.UniqueConstraint("workspace", "probe", name="note_candidate_probe_key"),
    )
    op.create_index("idx_note_standing", "note", ["workspace"],
                    postgresql_where=sa.text("retired_at is null"))
    op.create_index("idx_note_anchor", "note", ["workspace", "anchor_date"],
                    postgresql_where=sa.text("retired_at is null"))
    op.create_index("idx_note_revision_note_id", "note_revision", ["note_id"])
    # The search index (§5.3). GIN + trigram: no segmentation, so it matches
    # Cantonese and English alike. Hand-written because `op.create_index`'s
    # `postgresql_using="gin"` needs the opclass on the column expression, which
    # `alembic revision --autogenerate` does not produce.
    op.execute(
        "create index idx_note_revision_body_trgm on note_revision "
        "using gin (body gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("drop index if exists idx_note_revision_body_trgm")
    op.drop_index("idx_note_revision_note_id", table_name="note_revision")
    op.drop_index("idx_note_anchor", table_name="note")
    op.drop_index("idx_note_standing", table_name="note")
    op.drop_table("note_candidate")
    op.drop_table("note_entity")
    op.drop_table("note_revision")
    op.drop_table("note")
    # `pg_trgm` is deliberately NOT dropped: an extension is database-scoped
    # rather than owned by this migration, and another tier adopting trigram
    # search would have it removed underneath by this downgrade.
