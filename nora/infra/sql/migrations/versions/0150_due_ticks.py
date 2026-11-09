"""due_tick: the one eligibility authority for timed instructions.

`design/cognition-loop/18-execution-and-time.md`: a deadline's eligibility is
an integer minute tick on the Unix epoch — `due_tick = ceil(target_ms /
60_000)` — and pending work is eligible when `due_tick <= current_tick`,
never before.

Both columns are GENERATED from the stored instant, so the requested instant
(`reminders.due_at`, `stimulus.due_ms`) stays the audit field and no writer
can make the two disagree: there is no writable second deadline to migrate
or reconcile. `reminders.due_at` is `timestamptz`, so the tick is derived
inside the expression; `stimulus.due_ms` is epoch-ms bigint, where ceil is
integer division.

- `reminders.due_tick` — the heartbeat's `due()` reads
  `state = 'pending' AND due_tick <= :tick`; `idx_reminders_due_tick`
  (`state, due_tick`) is the new due predicate's index. `idx_reminders_due`
  stays: the pending-ordering reads (`all_pending`, `pending_for`) still
  order by the real `due_at`.
- `stimulus.due_tick` — `pending_query`'s eligibility and the watermark's
  time bound read ticks (`coalesce(due_tick, tick(recorded_at))`), and the
  due branch's index moves to `(unit, due_tick)`, same name, same partial
  predicate.

Recovery: both columns are derived — dropping and re-adding them (or this
migration's `downgrade()` + `upgrade()`) recomputes them from the instants
that remain the source of truth. A database backup from before this change
restores identically; one from after it carries only the generated columns'
definition, never divergent values.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0150_due_ticks"
down_revision = "0149_computer_profile_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column(
            "due_tick", sa.BigInteger,
            # `extract(epoch from timestamptz)` is STABLE and refused in a
            # generated column; the epoch's interval arithmetic is
            # IMMUTABLE, so the same microseconds come through
            # `due_at - epoch` instead. Verified against PostgreSQL 16.
            sa.Computed(
                "(ceil((extract(epoch from "
                "(due_at - '1970-01-01 00:00:00+00'::timestamptz)) "
                "* 1000) / 60000))::bigint",
            ),
        ),
    )
    op.create_index(
        "idx_reminders_due_tick", "reminders", ["state", "due_tick"],
    )
    op.add_column(
        "stimulus",
        sa.Column(
            "due_tick", sa.BigInteger,
            # `(due_ms + 59999) / 60000` would be wrong twice over: Postgres
            # integer division truncates toward zero (a negative `due_ms`
            # rounds up a whole tick), and `+59999` can overflow bigint.
            # `q + (r > 0)` is the signed-safe ceiling — identical to
            # Python's `-((-due_ms) // 60000)`.
            sa.Computed(
                "((due_ms / 60000) + "
                "CASE WHEN due_ms % 60000 > 0 THEN 1 ELSE 0 END)"
            ),
        ),
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
    op.drop_column("stimulus", "due_tick")
    op.drop_index("idx_reminders_due_tick", table_name="reminders")
    op.drop_column("reminders", "due_tick")
