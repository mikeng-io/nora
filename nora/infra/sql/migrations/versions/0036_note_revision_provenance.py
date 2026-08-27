"""note_revision.instruction_sha256 / model — the same DEC-125/DEC-126 pair
`0027` added to `fact_candidate`, on the next adaptor-written table to be asked
for it.

`note_revision` becomes adaptor-written the moment `SqlNoteStore` satisfies
`DreamSink`: the curator's every output is a revision, arriving as a `Proposal`
that carries `instruction_sha256` and `model` (`Proposal`'s own docstring —
provenance rides on the proposal rather than inside `payload` precisely so a
sink can keep it). The store had nowhere to put either, so it would have
discarded both at the one place they are free to keep.

**Found the same way `0027` was found** — by joining
`tests/contract/test_dream_sink_contract.py`, whose
`test_provenance_survives_the_write` is the shared assertion for exactly this
and which no `read_row` can satisfy while the columns do not exist. That is now
TWO implementations whose join surfaced the same missing pair, which is worth
saying plainly: **the provenance columns are not something each tier should be
rediscovering.** A tier building in isolation never has to confront DEC-125/126;
the contract suite is where it finds out. The next adaptor-written table should
carry them from its first migration rather than gaining them in its second.

**ON `note_revision` AND NOT ON `note`, which is the one real decision here.**
The unit of provenance is the unit of authorship, and for this tier those differ:
a note is created once and REVISED many times, by different authors — the tag
path (`tag:<person_id>`) and, once it exists, the curator. `note_revision`
already carries `established_by` for exactly that reason (§9.6's strike reset
distinguishes a tag-path revision from the revisitor's own and cannot without
it). Putting the instruction digest on `note` would attribute the whole note to
whatever wrote it FIRST and silently mis-attribute every revision after —
provenance that looks total and is wrong, which is worse than provenance that is
absent.

**NULLABLE, and this is the one place this migration departs from `0027`.**
`0027` made `fact_candidate`'s columns NOT NULL because that table has exactly
one writer — `SqlFactCandidateStore.propose`, an adaptor path, where every row
arrives as a `Proposal` carrying both fields. **`note_revision` has TWO
writers**: the curator, via `DreamSink.propose`, and the tag path, via
`note_create`/`note_update`, which is not an adaptor and has no `Proposal`.

DEC-125/126 put provenance on ADAPTOR-WRITTEN tables. This table is
adaptor-written and also hand-written, so a total column would require the tag
path to supply an instruction digest — which means threading one through
`ToolRegistry.build`/`specs` and every caller, to stamp a value that says
nothing `established_by` does not already say. `established_by` is
`tag:<person_id>` for a tag-path revision and the run id for a curator one, and
it is REQUIRED (§9.6's strike reset depends on telling them apart). So the
distinction the digest would encode is already recorded, correctly, one column
over.

**NULL here therefore means "not written by an adaptor" — a real state, not an
absence.** That is the difference from `0027`'s reasoning and the reason the
opposite choice is right: there, a NULL would have been an unreachable
"provenance unknown" that reads as real; here, a NOT NULL column would force a
writer that has no digest to invent one, which is the "fabricate a value to
satisfy a schema" move `0027` refuses in its own next paragraph. Same rule,
opposite conclusion, because the number of writers differs.

**This follows the four Dreamer tables, not `fact_candidate`**, and `0027`'s
docstring already names the split: *"The four Dreamer tables' version of this
CHECK does carry that arm, because their columns are nullable. Which arm is
right is decided by the nullability, not by copying whichever neighbour was read
last."* Read that way, this table is a Dreamer table with a second writer.

No backfill is needed either way — read from the live database before writing
this file:

    docker exec nora-db-1 psql -U nora -d nora \
        -tAc "select (select count(*) from note), (select count(*) from note_revision)"
    -> 0|0

**Both CHECKs are written HERE as well as in `schema.py`, and that is not
redundancy.** `compare_metadata` — what `tests/infra/test_schema_parity.py`
runs — is blind to CHECK constraints (CLAUDE.md; demonstrated on this repo
2026-08-20). A CHECK declared only on the `sa.Table` is documentation; this file
is what puts it in the database.

- `note_revision_instruction_sha256_check` — `instruction_sha256 is null or
  instruction_sha256 ~ '^[0-9a-f]{64}$'`. The `IS NULL` arm is here because the
  column IS nullable, which is the rule `0027` states: the arm is decided by the
  nullability, not by copying whichever neighbour was read last.
- `note_revision_model_is_named_check` — `model is null or
  length(btrim(model)) > 0`. A model
  name has no provable pattern — no width, no alphabet, no registry this process
  can consult — so the CHECK asserts only what IS provable: that the stamp is a
  real name and not `''`/`'   '`, which are non-NULL to every query asking
  "which rows are attributable" and would turn R-27's `unavailable` into a false
  `ok`. Note `btrim` strips SPACES only, not tabs or newlines (verified against
  the deployed PG18: `length(btrim(E'\\t')) = 1`), so a Python-side check using
  `str.strip()` refuses a SUPERSET of what this refuses — a safe direction, and
  one to state rather than claim as equivalence.

**Numbering.** `0035_turn_verdict` was head when this was written; `0034_standing`
was head an hour earlier and `0035` was taken by another branch in between.
`uv run alembic heads` must print exactly ONE line after every merge — two
revisions naming the same parent give it two children, git merges the two files
without a conflict, every test that does not actually migrate stays green, and
the container fails at boot because `upgrade "head"` refuses a multi-head chain.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_note_revision_provenance"
down_revision = "0035_turn_verdict"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "note_revision", sa.Column("instruction_sha256", sa.Text, nullable=True),
    )
    op.add_column("note_revision", sa.Column("model", sa.Text, nullable=True))
    op.create_check_constraint(
        "note_revision_instruction_sha256_check",
        "note_revision",
        "instruction_sha256 is null or instruction_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "note_revision_model_is_named_check",
        "note_revision",
        "model is null or length(btrim(model)) > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "note_revision_model_is_named_check", "note_revision", type_="check",
    )
    op.drop_constraint(
        "note_revision_instruction_sha256_check", "note_revision", type_="check",
    )
    op.drop_column("note_revision", "model")
    op.drop_column("note_revision", "instruction_sha256")
