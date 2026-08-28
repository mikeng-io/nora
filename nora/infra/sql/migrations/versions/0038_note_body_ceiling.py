"""note_revision.body gains a length ceiling — 27 §5.2's database half.

§5.2 decides TWO numbers and only the second lives here: *"`[notes]
max_body_chars`, operating value **8000**, database ceiling **32000**"*. The
operating value landed with the domain constant and the config field; this is
the ceiling.

**THE GAP BETWEEN THEM IS THE POINT, and it is why this is not simply 8000.**

- **8000 says «no writer should produce that».** It is enforced in
  `NoteRevision.__post_init__` and in both proposal types, so a model or a tool
  that overruns is refused at the boundary with a message naming the cap, where
  the refusal is reviewable.
- **32000 says «this row is corrupt».** A body between the two means something
  reached the table WITHOUT passing the domain type — a bug to go and find,
  not a row to reject when somebody reads it.

A CHECK at 8000 would collapse those two meanings into one and destroy the
signal: every over-cap write would look identical whether it came from a writer
that skipped validation or from a config the operator legitimately raised.
`NotesConfig.max_body_chars` is bounded `le=32000` for the same reason — a
configured value above the ceiling would be unenforceable, refused by Postgres
rather than by the domain, and the operator would find out from a failed write.

**Why the body is capped at all**, since it is the one note field that is not
injected into the prompt: the exposure is the CURATOR. It reads whole bodies per
tick against a budget counted in ROWS, and a row budget is only a budget if a
row has a bounded size. One unbounded body consumes a tick's whole allowance and
the pass then reports «nothing to do» about the notes it never reached — the
`unavailable`-as-`nothing_found` failure at the pass level, which the tier's own
spec names in advance. `nora_note_curator_bodies_read` has been measuring
against an unbounded quantity since it shipped.

**NOT NULL is not in question here — the column already is.** This adds only a
CHECK, so there is no backfill decision and no server default. Read from the
live database before writing this file, as `0027` and `0036` both did:

    docker exec nora-db-1 psql -U nora -d nora \\
        -tAc "select (select count(*) from note), (select count(*) from note_revision)"
    -> 0|0

Both empty, so no existing row can violate the new constraint. A CHECK added
against rows that DID violate it would fail loudly at migration time, which is
the correct outcome and one this deployment does not have to face.

**The CHECK is written HERE as well as in `schema.py`, and that is not
redundancy.** `compare_metadata` — what `tests/infra/test_schema_parity.py`
runs — is blind to CHECK constraints (CLAUDE.md; demonstrated on this repo
2026-08-20 by deleting four of them from `schema.py` alone and watching the suite
stay green). A CHECK declared only on the `sa.Table` is documentation; this file
is what puts it in the database.

`length()`, not `octet_length()`: the cap is on CHARACTERS, and a note body is
routinely Cantonese, where the two differ by a factor of three. Capping bytes
would give a Chinese-language room a third of the allowance an English-language
one gets, for a limit whose whole justification is «roughly a long page».

**Numbering.** `0037_standing_consumed_verdict` was head when this was written
and `uv run alembic heads` printed exactly ONE line first. Two revisions naming
the same parent give it two children, git merges the two files without a
conflict, every test that does not actually migrate stays green, and the
container fails at boot because `upgrade "head"` refuses a multi-head chain.
"""

from __future__ import annotations

from alembic import op

revision = "0038_note_body_ceiling"
down_revision = "0037_standing_consumed_verdict"
branch_labels = None
depends_on = None

#: 27 §5.2's DATABASE ceiling, four times the operating value. **Taken from
#: `nora.domain.notes.NOTE_BODY_CEILING_CHARS`, and deliberately NOT imported
#: from it** — a migration is a historical record of what was applied, and a
#: constant that moves later would silently rewrite what this revision claims
#: to have done. `schema.py` reads the constant; this file records the number.
#: `0034_standing` names its source the same way for the same reason.
BODY_CEILING_CHARS = 32000


def upgrade() -> None:
    op.create_check_constraint(
        "note_revision_body_fits_the_ceiling_check",
        "note_revision",
        f"length(body) <= {BODY_CEILING_CHARS}",
    )


def downgrade() -> None:
    op.drop_constraint(
        "note_revision_body_fits_the_ceiling_check",
        "note_revision",
        type_="check",
    )
