"""due_tick: the one eligibility authority for timed instructions.

`design/cognition-loop/execution.md`: a deadline's eligibility is
an integer minute tick on the Unix epoch — `due_tick = ceil(target_ms /
60_000)` — and pending work is eligible when `due_tick <= current_tick`,
never before. The migration's contract is «choose ONE writable eligibility
authority: `due_tick`; legacy timestamp readers keep a projection or are
migrated», so the tick is a real stored column every writer names — not a
GENERATED one a snapshot restore could never write — and each table's CHECK
binds it to the requested instant so the two can never disagree: the
timestamp is provenance, never a second deadline.

- `reminders.due_tick` — NOT NULL (every reminder has a `due_at`, so the
  tick always exists); the heartbeat's `due()` reads
  `state = 'pending' AND due_tick <= :tick`; `idx_reminders_due_tick`
  (`state, due_tick`) is the new due predicate's index. `idx_reminders_due`
  stays: the pending-ordering reads (`all_pending`, `pending_for`) still
  order by the real `due_at`.
- `stimulus.due_tick` — NULL only with `due_ms` (an undated stimulus is
  pending once recorded); `pending_query`'s eligibility and the watermark's
  time bound read ticks (`coalesce(due_tick, tick(recorded_at))`), and the
  due branch's index moves to `(unit, due_tick)`, same name, same partial
  predicate.

Existing rows get the tick their instant proves (`UPDATE` backfill) — one
writable column means the backfill is a real write, the CHECK then holds it
for every later writer. Recovery: recompute `due_tick` from the instant
with the CHECK's own expression; a database backup restores identically.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0150_due_ticks"
down_revision = "0149_computer_profile_merge"
branch_labels = None
depends_on = None

#: The tick `reminders.due_at` proves — `extract(epoch from timestamptz)` is
#: STABLE and refused where Postgres wants IMMUTABLE; the interval
#: arithmetic below is IMMUTABLE and gives the same microseconds. Shared by
#: the backfill and the CHECK so they can never disagree on the rule.
_REMINDER_TICK = (
    "(ceil((extract(epoch from "
    "(due_at - '1970-01-01 00:00:00+00'::timestamptz)) * 1000) / 60000))::bigint"
)

#: The tick `stimulus.due_ms` proves: `(due_ms + 59999) / 60000` would be
#: wrong twice over — Postgres integer division truncates toward zero (a
#: negative `due_ms` rounds up a whole tick) and `+59999` can overflow
#: bigint — so `q + (r > 0)`, the signed-safe ceiling identical to Python's
#: `-((-due_ms) // 60000)`. NULL in, NULL out.
_STIMULUS_TICK = (
    "(CASE WHEN due_ms IS NULL THEN NULL ELSE (due_ms / 60000) + "
    "CASE WHEN mod(due_ms, 60000) > 0 THEN 1 ELSE 0 END END)"
)


def upgrade() -> None:
    op.add_column("reminders", sa.Column("due_tick", sa.BigInteger))
    op.execute(f"UPDATE reminders SET due_tick = {_REMINDER_TICK}")  # noqa: S608 — module constant
    op.alter_column("reminders", "due_tick", nullable=False)
    op.create_check_constraint(
        "ck_reminders_due_tick", "reminders",
        f"due_tick IS NOT DISTINCT FROM {_REMINDER_TICK}",
    )
    op.create_index(
        "idx_reminders_due_tick", "reminders", ["state", "due_tick"],
    )
    op.add_column("stimulus", sa.Column("due_tick", sa.BigInteger))
    op.execute(
        f"UPDATE stimulus SET due_tick = {_STIMULUS_TICK} WHERE due_ms IS NOT NULL"  # noqa: S608 — module constant
    )
    op.create_check_constraint(
        "ck_stimulus_due_tick", "stimulus",
        f"due_tick IS NOT DISTINCT FROM {_STIMULUS_TICK}",
    )
    op.drop_index(
        "idx_stimulus_unit_due", table_name="stimulus",
        postgresql_where=sa.text("due_ms IS NOT NULL"),
    )
    op.create_index(
        "idx_stimulus_unit_due", "stimulus", ["unit", "due_tick"],
        postgresql_where=sa.text("due_tick IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_stimulus_unit_due", table_name="stimulus",
        postgresql_where=sa.text("due_tick IS NOT NULL"),
    )
    op.create_index(
        "idx_stimulus_unit_due", "stimulus", ["unit", "due_ms"],
        postgresql_where=sa.text("due_ms IS NOT NULL"),
    )
    op.drop_constraint("ck_stimulus_due_tick", "stimulus", type_="check")
    op.drop_column("stimulus", "due_tick")
    op.drop_index("idx_reminders_due_tick", table_name="reminders")
    op.drop_constraint("ck_reminders_due_tick", "reminders", type_="check")
    op.drop_column("reminders", "due_tick")
