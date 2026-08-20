"""instruction_sha256 — provenance on the four tables the Dreamer's
model-calling adaptors write (2026-08-20 instruction-substrate spec §4.3).

Nothing the Dreamer writes recorded what wrote it. `room_lexeme` holds a
model-authored `sense` and no record of the instruction text that asked for
it, so the gloss of `big walk` as 「群內約定俗成嘅散步/行路活動」 — a nighttime
stroll, for a co-op puzzle video game — cannot be attributed to a revision,
and no later edit can be shown to have fixed it. `extractor_confidence`,
already on both observation tables, is the model's own self-report, which is
the opposite of provenance.

**Why the column lands before the instruction substrate exists.** The
instructions are inline Python constants today
(`nora.language.observer._PROMPT_PREAMBLE` and its siblings); under the
substrate they become an assembled doctrine body plus one objective per
adaptor. The column is the same either way — one hash over whatever
instruction text actually produced the row — so waiting would buy nothing and
cost everything written in between: a backfill is impossible, because the
rows that would need it were written by instructions nobody recorded.

**NOT `room_portrait`** (spec §4.3): its objective is executed in code with no
model call, so no instruction text governed the row, and stamping a hash of
text that did not produce the output is provenance bound to the wrong
property — the defect the column exists to remove. Its provenance chain runs
through `lexeme_basis`/`exchange_basis`, whose rows this migration stamps.

**NULLABLE, no server default, no backfill.** Every row written from here on
carries a digest (the observers stamp at construction; the stores have no
other writer). The nullable half is entirely about rows already in
`nora-db-1`: they were produced by instructions nobody recorded, and inventing
one — a server default, a sentinel — would be a fabricated attribution,
strictly worse than an honest absent one. NULL reads as «I could not check»,
never «there was nothing» (R-27).

The CHECK is the fail-closed half: 64 lowercase hex characters or NULL,
nothing in between. `alembic check` is blind to CHECK constraints, so it is
`tests/infra/test_schema_parity.py` that guarantees this landed (CLAUDE.md).
Nothing here touches `idx_messages_conv_ts`.

Revision ID: 0023_dreamer_instruction_sha256
Revises: 0022_presence_trips_sequence
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_dreamer_instruction_sha256"
down_revision = "0022_presence_trips_sequence"
branch_labels = None
depends_on = None

# The four tables an adaptor that REACHES A MODEL writes — two evidence
# tables, stamped by the observer that produced the row, and their two
# consolidated tables, which inherit the newest evidence row's stamp in
# `rescore`.
_TABLES = (
    "language_observation",
    "room_lexeme",
    "exchange_observation",
    "room_exchange",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("instruction_sha256", sa.Text, nullable=True))
        op.create_check_constraint(
            f"{table}_instruction_sha256_check",
            table,
            "instruction_sha256 IS NULL OR instruction_sha256 ~ '^[0-9a-f]{64}$'",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"{table}_instruction_sha256_check", table, type_="check")
        op.drop_column(table, "instruction_sha256")
