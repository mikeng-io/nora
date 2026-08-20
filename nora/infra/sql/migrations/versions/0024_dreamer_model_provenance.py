"""`model` — the second half of the provenance `0023` opened, on the same
four Dreamer tables (2026-08-20 instruction-substrate spec §6.2, question 2,
decided in favour the same day).

`0023` records WHICH INSTRUCTION produced a row. That is one input; the model
that read it is another, and the two do not substitute for each other — the
same instruction text run against a different model produces a different
`sense`, a different act call, a different population of rows. So
`instruction_sha256` alone does not answer "what produced this row", and a
corpus split by instruction revision alone silently mixes two writers.

**Why now, and not with the substrate.** The same arithmetic `0023` ran, and
it has not changed: a column now is one migration, a backfill later is
impossible, because the rows that would need it were written by a model
nothing recorded. `[language].model` has changed before, and it is the
keystone's own §9.1 open question — which model a research loop should run —
which means it *will* change again. Rows written either side of that change
have to be distinguishable, and only a column written at the time can make
them so. That the Dreamer runs `deepseek-v4-flash` today (the same model Nora
runs, and `LanguageConfig.model`'s default) makes the value stable, not
guaranteed: it is a config value, which is precisely the reason to record it.

**NOT `room_portrait`**, for the reason `0023` excluded it, one step stronger:
`RoomPortraitSynthesizer` makes no model call at all ("No model call
anywhere"), so there is no model to attribute — not an unknown one, an absent
one. Its provenance still resolves through `lexeme_basis`/`exchange_basis`,
whose rows this migration stamps.

**NULLABLE, no server default, no backfill** — `0023`'s argument unchanged.
Every row written from here on carries the name the observer read off the
client that made the call; the nullable half is entirely about the rows
already in `nora-db-1`, which were written by a model nobody recorded.
Defaulting them to today's `[language].model` would be a *fabricated
attribution* — provenance bound to a proxy, the exact defect the column
exists to remove — and worse than the digest case, because the value would
look entirely plausible. NULL reads as «I could not check», never «there was
nothing» (R-27).

**The CHECK is deliberately NOT `0023`'s shape.** A SHA-256 digest has a
provable form and `0023` asserts it: 64 lowercase hex or NULL. A model name
has none — no width, no alphabet, no registry this process can consult — so
a pattern here would be a guess about naming conventions dressed as a
constraint, and would one day reject a real provider rename and lose a row's
provenance to keep the guess. What IS provable, and is the whole of what the
CHECK asserts, is non-emptiness: a stamp is a real name or it is honestly
absent, never a value that reads as recorded and names nothing. `''` and
`'   '` are non-NULL to every query asking "which rows are attributable",
which turns an `unavailable` into a false `ok` — the same failure
`nora.domain.provenance.instruction_sha256` refuses by raising on an empty
part list, one layer down.

The consolidated tables INHERIT this column beside the digest, from the same
`rn = 1` row of `rescore`'s `latest` CTE. That is not a convenience: two
provenance fields taken from two different observations would name a pairing
that never existed — an instruction that was never run against that model.

`alembic check` is blind to CHECK constraints, so it is
`tests/infra/test_schema_parity.py` that guarantees this landed (CLAUDE.md).
Nothing here touches `idx_messages_conv_ts`.

Revision ID: 0024_dreamer_model_provenance
Revises: 0023_dreamer_instruction_sha256
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_dreamer_model_provenance"
down_revision = "0023_dreamer_instruction_sha256"
branch_labels = None
depends_on = None

# The same four tables `0023` stamped, and for the same reason: the two
# evidence tables an adaptor that REACHES A MODEL writes, plus the two
# consolidated tables that inherit the newest evidence row's stamp in
# `rescore`. `room_portrait` reaches no model at all and is excluded.
_TABLES = (
    "language_observation",
    "room_lexeme",
    "exchange_observation",
    "room_exchange",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("model", sa.Text, nullable=True))
        op.create_check_constraint(
            f"{table}_model_check",
            table,
            "model IS NULL OR length(btrim(model)) > 0",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"{table}_model_check", table, type_="check")
        op.drop_column(table, "model")
