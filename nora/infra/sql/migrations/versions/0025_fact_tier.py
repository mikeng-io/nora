"""fact_entity / fact_candidate / fact / fact_premise — the fact tier, dormant
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
- `fact_supersession_is_timed_check` — `superseded_by` and `superseded_at`
  are set together or not at all (a biconditional, not an implication): a
  fact is either standing (both NULL) or superseded (both set). The hole a
  one-directional implication leaves — `superseded_at` set with
  `superseded_by` NULL — is exactly the unauditable supersession Graphiti's
  own issue #1771 documents: a merge computed and then thrown away, no
  record of what replaced it, while `Fact.is_standing` and the partial
  `idx_fact_standing` (both keyed on `superseded_at IS NULL`) already treat
  the row as gone.
- `fact_candidate_status_check` — `pending` / `adjudicated` / `not_covered`,
  matching `nora.domain.facts.CandidateStatus`.

**2026-08-21: the warrant axis.** The original shape above models TIME four
ways (`valid_from`/`valid_to`/`created_at`/`superseded_at`) and WARRANT QUALITY
zero ways — everything the tier can ever say about how well a claim is
supported had to squeeze through `superseded_at`, a single bit. Two axes were
conflated and one was missing (see `nora/domain/facts.py`'s module docstring
for the full argument):

- **Currency** — temporal validity, "was true then, isn't now". Already
  modelled by the four time columns above.
- **Fidelity** — epistemic status, "unconfirmed / confirmed / retracted".
  Orthogonal to currency: a fact can be high-fidelity + low-currency (true but
  outdated) or low-fidelity + high-currency (fresh but unconfirmed).

And `source_url`/`source_excerpt` being NOT NULL meant the only representable
warrant was "a page said so" — a fact warranted by other facts, by a repeated
observation, or by someone simply deciding something could not be written at
all. This migration adds:

- `fact_warrant_kind_check` — `warrant_kind in ('source', 'premises',
  'observation', 'stated')`, matching `nora.domain.facts.WarrantKind`.
  `source` is today's only writer-producible case; `premises`, `observation`,
  `stated` give the other three warrant shapes discussed in
  `docs/superpowers/specs/2026-08-20-fact-tier-design.md` §13.5 somewhere to
  land without a further migration.
- `fact_source_url_warrant_check` / `fact_source_excerpt_warrant_check` —
  each column is set if and only if `warrant_kind = 'source'`. This REPLACES
  the two columns' NOT NULL (dropped below): a nullable column with no CHECK
  would permit a fact with no warrant of any kind, which is exactly the
  unattributable claim this tier exists to prevent. The two columns are kept
  as separate CHECKs (rather than one combined predicate) so each is
  independently red-on-revert testable, and so a row can never carry
  `source_url` set while `source_excerpt` is null or vice versa for a
  non-`source` warrant — "forbidden otherwise", not merely "optional", is the
  deliberate choice: an optional stray `source_url` on a `premises`-warranted
  fact would be undefined in meaning (corroborating? decorative?), which is
  the exact ambiguity the discriminated warrant exists to kill.
- `fact_fidelity_check` — `fidelity in ('tentative', 'proven',
  'deprecated')`, matching `nora.domain.facts.FidelityStatus`. Deliberately
  not a float: a numeric confidence invites false precision nobody in this
  tier can calibrate. Server default **`'tentative'`**, not `'proven'` — a
  default is what a row gets when nobody thought about it, and in a
  fail-closed codebase (R-27, R-5) that value must UNDERCLAIM, never
  overclaim. `tentative` is also where an unconfirmed — including an
  abduced, see §13.5 — claim lands; making it the default rather than an
  opt-in is the right shape for a tier whose thesis is that unverified
  claims must stay visibly unverified. (2026-08-21 correction: this column
  first shipped defaulting to `'proven'`, reasoned from today's only writer
  path — the adaptor is source-verified, so `proven` looked harmless — which
  is designing for the writer this tier has today rather than the one it
  will have; fixed same-day, before any row existed.)
- `fact_supersede_reason_is_timed_check` — `supersede_reason` is set if and
  only if `superseded_at` is set, the same biconditional shape as
  `fact_supersession_is_timed_check` above and for the same reason: the
  *reason* a fact was superseded cannot be reconstructed from the row
  afterwards at any price, so it is captured at supersession time or not at
  all.
- `fact_supersede_reason_check` — when set, `supersede_reason in ('stale',
  'wrong', 'merged')`, matching `nora.domain.facts.SupersedeReason`.
- `fact_superseded_by_fkey` — `superseded_by` now FOREIGN KEYs to `fact.id`.
  It previously had none, so it could name a fact that does not exist. Being
  self-referential (same table), it needs no cross-table ordering in either
  `upgrade`/`downgrade`: `DROP TABLE fact` drops it with the table, same as
  any other constraint on `fact`. It does not change the existing
  biconditional's logic, only strengthens it: a non-NULL `superseded_by` must
  now also be a real, existing row.

**`fact_premise` — a junction table, not a `premise_ids` array column.**
2026-08-21, second pass: the axis first shipped as `fact.premise_ids UUID[]`,
with the honest but weak note that Postgres cannot FK array elements, so
referential integrity for premises would be the writer's job. Replaced same
day, before any row existed, for a reason that outweighs enforcement-over-a-
comment on its own: **the reverse lookup.** §13.5 records transitive
supersession as an open problem — when a premise is superseded, every fact
deduced from it is standing on a retracted foundation. "Which facts depend on
this one" is an `array @> ARRAY[...]` scan with no index that helps against
`UUID[]`; it is `SELECT fact_id FROM fact_premise WHERE premise_fact_id = ...`
against an index with a junction table. The array made the dependency
representable; only the junction table makes it QUERYABLE, which is the
precondition for ever handling it.

- `fact_premise(fact_id, premise_fact_id)`, both FOREIGN KEY to `fact.id`,
  composite PRIMARY KEY (so a given premise cannot be recorded twice for the
  same fact), plus `idx_fact_premise_premise_fact_id` on `premise_fact_id`
  alone for the reverse direction (the composite PK already indexes the
  forward direction, `fact_id` first).
- `fact_premise_no_self_reference_check` — `fact_id <> premise_fact_id`. A
  fact cannot be its own premise; that specific one-hop cycle is cheap to
  rule out at the row level. Longer cycles (A premises B premises A) are
  NOT detected here — that needs a graph walk, not a row CHECK, and this
  tier has no writer yet to make one urgent. Left as a known gap, not
  silently assumed away.
- **Where "`warrant_kind = 'premises'` ⇒ at least one premise" now lives:
  nowhere in the database.** It was a single-table CHECK
  (`fact_premise_ids_warrant_check`, on `cardinality(premise_ids)`) when
  premises were an array column; a junction table makes it a cross-table
  constraint (COUNT the `fact_premise` rows for a `fact.id`), which Postgres
  CHECK constraints cannot express and which this migration does not fake
  with a trigger — a trigger enforcing an app-level invariant behind a
  CHECK-shaped name would read as guaranteed when it is not yet built, and
  this tier's whole point is to not let a claim look more warranted than it
  is. Until a writer exists, nothing enforces this — the writer is what
  will insert the `fact` row and its `fact_premise` rows in the same
  transaction, and that transaction is where the invariant will actually be
  upheld. Recorded here so the gap is inherited on purpose, not rediscovered.
- Referential integrity for *content* (that a `fact_premise` row's
  `premise_fact_id` names a fact that is itself STANDING, not superseded) is
  still not database-enforced — the FK only guarantees the id exists, not
  that it is current. Same "writer's job" boundary as before, now scoped
  tighter (existence is enforced; standing-ness is not).

**2026-08-21, third pass: `supersede_source`.** `DreamSink.supersede`'s `source`
argument (a WARRANT — a URL or equivalent pointer to evidence, never a row id;
`superseded_by` already carries the successor) had nowhere to land: this table
modelled `because` (`supersede_reason`) but not the optional citation beside
it. `SqlFactStore.supersede` (this task) needs to round-trip whatever `source`
it was given, so this migration adds the column and
`fact_supersede_source_is_timed_check` — a one-directional implication
(`supersede_source is null or superseded_at is not null`), not the
biconditional `fact_supersede_reason_is_timed_check` uses, because `source` is
optional even on a superseded row (the port's own docstring: "`because` is
required and stays narrow enough to be a constrained enum... `source`" is
not).

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
        sa.Column("warrant_kind", sa.Text, nullable=False,
                  server_default=sa.text("'source'")),
        sa.Column("source_url", sa.Text),
        sa.Column("source_excerpt", sa.Text),
        sa.Column("valid_from", sa.BigInteger, nullable=False),
        sa.Column("valid_to", sa.BigInteger),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("superseded_at", sa.BigInteger),
        sa.Column("superseded_by", pg.UUID(as_uuid=False),
                  sa.ForeignKey("fact.id", name="fact_superseded_by_fkey")),
        sa.Column("supersede_reason", sa.Text),
        sa.Column("supersede_source", sa.Text),
        sa.Column("instruction_sha256", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("fidelity", sa.Text, nullable=False,
                  server_default=sa.text("'tentative'")),
        sa.CheckConstraint("instruction_sha256 ~ '^[0-9a-f]{64}$'",
                           name="fact_instruction_sha256_check"),
        sa.CheckConstraint("length(btrim(model)) > 0",
                           name="fact_model_is_named_check"),
        sa.CheckConstraint("(object_entity_id is null) <> (object_literal is null)",
                           name="fact_one_object_check"),
        sa.CheckConstraint("(superseded_by is null) = (superseded_at is null)",
                           name="fact_supersession_is_timed_check"),
        sa.CheckConstraint(
            "warrant_kind in ('source', 'premises', 'observation', 'stated')",
            name="fact_warrant_kind_check",
        ),
        sa.CheckConstraint(
            "(source_url is not null) = (warrant_kind = 'source')",
            name="fact_source_url_warrant_check",
        ),
        sa.CheckConstraint(
            "(source_excerpt is not null) = (warrant_kind = 'source')",
            name="fact_source_excerpt_warrant_check",
        ),
        sa.CheckConstraint(
            "fidelity in ('tentative', 'proven', 'deprecated')",
            name="fact_fidelity_check",
        ),
        sa.CheckConstraint(
            "(supersede_reason is null) = (superseded_at is null)",
            name="fact_supersede_reason_is_timed_check",
        ),
        sa.CheckConstraint(
            "supersede_reason is null or supersede_reason in "
            "('stale', 'wrong', 'merged')",
            name="fact_supersede_reason_check",
        ),
        sa.CheckConstraint(
            "supersede_source is null or superseded_at is not null",
            name="fact_supersede_source_is_timed_check",
        ),
    )
    op.create_index("idx_fact_standing", "fact", ["workspace", "subject_id"],
                    postgresql_where=sa.text("superseded_at is null"))
    # Created after `fact` so both FOREIGN KEYs below can target it.
    op.create_table(
        "fact_premise",
        sa.Column("fact_id", pg.UUID(as_uuid=False),
                  sa.ForeignKey("fact.id", name="fact_premise_fact_id_fkey"),
                  primary_key=True),
        sa.Column("premise_fact_id", pg.UUID(as_uuid=False),
                  sa.ForeignKey("fact.id", name="fact_premise_premise_fact_id_fkey"),
                  primary_key=True),
        sa.CheckConstraint("fact_id <> premise_fact_id",
                           name="fact_premise_no_self_reference_check"),
    )
    op.create_index("idx_fact_premise_premise_fact_id", "fact_premise",
                    ["premise_fact_id"])


def downgrade() -> None:
    # fact_premise FOREIGN KEYs into fact, so it must go before fact does.
    op.drop_index("idx_fact_premise_premise_fact_id", table_name="fact_premise")
    op.drop_table("fact_premise")
    op.drop_index("idx_fact_standing", table_name="fact")
    op.drop_table("fact")
    op.drop_table("fact_candidate")
    op.drop_table("fact_entity")
