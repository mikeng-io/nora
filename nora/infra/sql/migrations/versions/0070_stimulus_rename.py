"""signal -> stimulus -- the cognition loop's inbox row stops sharing a word
with Signal, the messaging platform Nora lives on.

**THE COLLISION.** `0069_cognition_signal` named the per-unit table that wakes
cognition `signal` (design/cognition-loop/20-observe.md §3). That word is also
the name of a whole messaging platform this deployment runs on
(`nora/platform/signal/`, `signal-cli`, the `signal` platform string in every
room's config) — two unrelated things spelled the same, the exact shape
`0058_unit_rename`'s docstring already names a cost for. Caught the same day
`0069` shipped, before any dashboard or alert depended on the old metric
names (operator 2026-09-29, DEC-234, amending DEC-233). The cognition-loop
concept moves; the Signal platform — its adapter, its rooms, `signal-cli`, the
literal platform string `"signal"` — is untouched by this migration and by the
rename generally.

**SCOPE.** A pure rename: same columns, same types, same nullability, same
defaults, same rows, same indexes' definitions. Only the identifiers that
spell the retired word are touched:

- table `signal` -> `stimulus` (and its identity column's sequence,
  `signal_id_seq` -> `stimulus_id_seq` -- Postgres does not rename an identity
  sequence when its table is renamed);
- its five indexes: `idx_signal_unit_id`, `idx_signal_unit_due`,
  `idx_signal_unit_undated_recorded`, `idx_signal_unit_message_recorded`,
  `uq_signal_time_ref` -> the `idx_stimulus_*` / `uq_stimulus_time_ref` forms
  (`ALTER TABLE ... RENAME TO` does not rename a table's indexes, unlike
  `ALTER TABLE ... RENAME COLUMN`, which repoints index/constraint
  REFERENCES on its own -- `0058_unit_rename`'s docstring);
- its CHECK constraint `ck_signal_kind` -> `ck_stimulus_kind`;
- `cognition_wake`'s three columns that name a stimulus id or count:
  `from_signal_id` -> `from_stimulus_id`, `through_signal_id` ->
  `through_stimulus_id`, `signal_count` -> `stimulus_count`. `cognition_wake`
  itself, `wake`, `wake_at` and every other DEC-233 name are UNCHANGED: only
  the concept `signal` named is retired, not the wake log around it.

Nothing is read or rewritten, so this is O(catalog) on any table size and
needs no backfill. `nora.infra.sql.history.APPEND_ONLY`'s `signal` key moves
to `stimulus` in the same commit that carries this migration (the step-1b
`row_history`/append-only guard reads that dict, not the table name, so it
must move with the rename or the guard test fails on a stale key).

**METRICS.** `nora_signal_total{kind,cue}` and `nora_signal_pending_age_seconds`
existed for hours before this rename (`0069`/#615/DEC-233 merged and shipped
2026-09-28; this lands 2026-09-29) — the one stated exception to "metric
names are append-only in this repo" (GLOSSARY.md), made explicit here and in
DEC-234 because nothing outside this branch ever depended on the old names:
no dashboard, no alert rule, no PROMQL committed anywhere greps for them
(checked: `git grep -n nora_signal_total -- deploy` and
`git grep -n nora_signal_pending_age_seconds -- deploy` both empty at branch
time). They become `nora_stimulus_total{kind,cue}` and
`nora_stimulus_pending_age_seconds` in the same commit as this migration.

Revision ID: 0070_stimulus_rename
Revises: 0069_cognition_signal
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0070_stimulus_rename"
down_revision = "0069_cognition_signal"
branch_labels = None
depends_on = None

#: (old, new) index name pairs, in the order they are renamed forward; the
#: downgrade walks them in reverse. `uq_signal_time_ref` is a unique INDEX,
#: not a named constraint (`sa.Index(..., unique=True)` in schema.py), so it
#: renames the same way as the other four, via `ALTER INDEX`.
_INDEX_RENAMES: tuple[tuple[str, str], ...] = (
    ("idx_signal_unit_id", "idx_stimulus_unit_id"),
    ("idx_signal_unit_due", "idx_stimulus_unit_due"),
    ("idx_signal_unit_undated_recorded", "idx_stimulus_unit_undated_recorded"),
    ("idx_signal_unit_message_recorded", "idx_stimulus_unit_message_recorded"),
    ("uq_signal_time_ref", "uq_stimulus_time_ref"),
)

#: `cognition_wake` columns that name a stimulus id or count.
_COLUMN_RENAMES: tuple[tuple[str, str], ...] = (
    ("from_signal_id", "from_stimulus_id"),
    ("through_signal_id", "through_stimulus_id"),
    ("signal_count", "stimulus_count"),
)


def upgrade() -> None:
    op.rename_table("signal", "stimulus")
    op.execute('ALTER SEQUENCE "signal_id_seq" RENAME TO "stimulus_id_seq"')
    # `ALTER TABLE ... RENAME TO` renames neither the primary key constraint
    # nor its backing index (unlike `RENAME COLUMN`, which repoints every
    # index/constraint REFERENCE on its own): Postgres names both
    # `signal_pkey` from `0069`, and renaming the constraint renames its
    # backing index along with it, so one statement covers both.
    op.execute('ALTER TABLE "stimulus" RENAME CONSTRAINT "signal_pkey" TO "stimulus_pkey"')
    for old, new in _INDEX_RENAMES:
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')
    op.execute('ALTER TABLE "stimulus" RENAME CONSTRAINT "ck_signal_kind" TO "ck_stimulus_kind"')
    for old, new in _COLUMN_RENAMES:
        op.alter_column("cognition_wake", old, new_column_name=new)


def downgrade() -> None:
    for old, new in _COLUMN_RENAMES:
        op.alter_column("cognition_wake", new, new_column_name=old)
    op.execute('ALTER TABLE "stimulus" RENAME CONSTRAINT "ck_stimulus_kind" TO "ck_signal_kind"')
    for old, new in reversed(_INDEX_RENAMES):
        op.execute(f'ALTER INDEX "{new}" RENAME TO "{old}"')
    op.execute('ALTER TABLE "stimulus" RENAME CONSTRAINT "stimulus_pkey" TO "signal_pkey"')
    op.execute('ALTER SEQUENCE "stimulus_id_seq" RENAME TO "signal_id_seq"')
    op.rename_table("stimulus", "signal")
