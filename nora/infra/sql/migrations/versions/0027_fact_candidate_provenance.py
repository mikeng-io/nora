"""fact_candidate.instruction_sha256 / model — the provenance DEC-125/DEC-126
put on every adaptor-written table, on the one adaptor-written table that
predates the adaptor writing it.

`fact_candidate` is written through `DreamSink.propose`, and every `Proposal`
that reaches it carries `instruction_sha256` and `model` (`Proposal`'s own
docstring: provenance rides on the proposal rather than inside `payload`
precisely so a sink can keep it). `SqlFactCandidateStore` discarded both —
provenance thrown away at the one place it was free to keep, on rows whose
whole purpose is to be re-read a tick later and reasoned about. Found by
joining `tests/contract/test_dream_sink_contract.py`, whose
`test_provenance_survives_the_write` is the shared assertion for exactly this
and which no `read_row` could satisfy while the columns did not exist.

**NOT NULL, no server default, and deliberately no backfill.** The choice is
the same one `0025` made for `fact` and the opposite of the one `0023`/`0024`
made for the four Dreamer tables, for the reason that decided all three: those
tables held rows written before the columns existed, so a NOT NULL column
would have had to invent a digest for them; this table's rows are ALL written
by the store below, and there are none of them yet. Verified rather than
assumed, on the live database, before writing this file:

    docker exec nora-db-1 psql -U nora -d nora \\
        -c "select version_num from alembic_version" \\
        -c "select count(*) from fact_candidate"
    -> 0025_fact_tier, 0

So the column CAN be total, and being total is what makes it worth having: a
nullable `instruction_sha256` would make `FactCandidate.instruction_sha256`
`str | None`, forcing every reader to branch on a NULL no writer can produce —
an unreachable "provenance unknown" state that reads as a real one. The
`Proposal` type makes both fields non-optional `str`; the column should say
the same thing.

**What the missing backfill buys, and what it costs.** `ADD COLUMN ... NOT
NULL` with no default FAILS, loudly, against any database that does hold rows
— a deployment this migration has already checked does not exist, and any
OTHER deployment that does is one where the alternative is worse. The
alternative is a server default: `'0' * 64` satisfies
`fact_candidate_instruction_sha256_check` and is indistinguishable, forever,
from a digest that was actually read from an instruction file. That is a row
that looks attributable and is not — R-27's ERROR-surfacing-as-`ok`, written
into a column instead of returned from a call. A migration that stops is a
problem someone sees; a fabricated digest is a problem nobody ever sees. If a
non-empty `fact_candidate` ever appears somewhere, the answer is to decide
what those rows' provenance actually was, not to let this file guess.

**Both CHECKs are written HERE as well as in `schema.py`, and that is not
redundancy.** `compare_metadata` — what `tests/infra/test_schema_parity.py`
runs — is blind to CHECK constraints (CLAUDE.md; demonstrated on this repo
2026-08-20 by deleting four of them from `schema.py` alone and watching the
whole suite stay green). A CHECK declared only on the `sa.Table` is
documentation; this file is what puts it in the database, and
`tests/infra/test_fact_schema.py` is what proves each one live by attempting
the exact INSERT it exists to refuse.

- `fact_candidate_instruction_sha256_check` — `instruction_sha256 ~
  '^[0-9a-f]{64}$'`, the same predicate `fact_instruction_sha256_check`
  carries and with no `IS NULL` branch, for the same reason it has none:
  the column is NOT NULL, so the null arm would be unreachable text. (The
  four Dreamer tables' version of this CHECK does carry that arm, because
  their columns are nullable. Which arm is right is decided by the
  nullability, not by copying whichever neighbour was read last.)
- `fact_candidate_model_is_named_check` — `length(btrim(model)) > 0`, the
  shape `0024`/`0025` used and for the reason `0025` states: a model name has
  no provable pattern (no width, no alphabet, no registry this process can
  consult), so the CHECK asserts only what IS provable — that the stamp is a
  real name and not `''`/`'   '`, which are non-NULL to every query asking
  "which rows are attributable" and would turn R-27's `unavailable` into a
  false `ok`.

**A re-sighting does not move either column** — that is the store's rule
(`SqlFactCandidateStore.propose`'s upsert `set_`), not this file's, but it is
the reason the columns are safe to make total: they are established once, with
`established_by` and `first_seen_at`, by the run that first saw the claim, and
a later sighting of the same triple bumps counts without rewriting who
established the row.

**Numbering, and the hazard it was written under.** This was authored against
`0025_fact_tier` because `0026_note_tier` was on an unmerged branch at the
time, and a `down_revision` naming an absent revision is an immediate
`ScriptDirectory` failure. The note tier landed first (2026-08-21), so this
re-points at `0026` — the agreed rule was that whoever merges SECOND moves.

**Why that agreement existed at all**, because the failure it avoids is
invisible: two revisions both naming `0025` as parent give `0025` two children,
git merges the two files without a conflict, every test that does not actually
migrate stays green, and `run_migrations` calls `command.upgrade(cfg, "head")`
— singular — which refuses a multi-head chain. The container fails to start.
`uv run alembic heads` printing exactly ONE line is the check; `alembic check`
does not catch it.

Revision ID: 0027_fact_candidate_provenance
Revises: 0026_note_tier
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_fact_candidate_provenance"
down_revision = "0026_note_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_candidate", sa.Column("instruction_sha256", sa.Text, nullable=False),
    )
    op.add_column("fact_candidate", sa.Column("model", sa.Text, nullable=False))
    op.create_check_constraint(
        "fact_candidate_instruction_sha256_check",
        "fact_candidate",
        "instruction_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "fact_candidate_model_is_named_check",
        "fact_candidate",
        "length(btrim(model)) > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fact_candidate_model_is_named_check", "fact_candidate", type_="check",
    )
    op.drop_constraint(
        "fact_candidate_instruction_sha256_check", "fact_candidate", type_="check",
    )
    op.drop_column("fact_candidate", "model")
    op.drop_column("fact_candidate", "instruction_sha256")
