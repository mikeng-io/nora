"""reminder sub-day recurrence — `minutely`/`hourly`, and every-N-units.

Issue #109. `Recurrence` was `none/daily/weekly/monthly/yearly` and the CHECK
constraint hard-coded the same five, so "remind me every 10 minutes" was not
representable at all.

**The live failure this closes.** 2026-09-06, Discord, the F1 Italian GP:
somebody asked for a lap update every 10 minutes during the race. Nora's own
reasoning shows she saw the gap and planned the right workaround — twelve
separate reminders — then abandoned it and wrote ONE `daily` row with
`until` two hours out. `plan_successor` computes occurrence 1 as 21:10 the
NEXT day, past that `until`, so the series ended at occurrence 0. One firing,
which then died on the heartbeat's own timeout. A promise of eleven updates
delivered nothing.

**Two columns' worth of change, one of them new.** `recurrence_interval` is
what makes "every 10 minutes" expressible — `minutely` alone can only say
"every minute". It multiplies the calendar units too (`weekly x 2` is a
fortnight), which falls out of the same arithmetic and would have been an
arbitrary hole to refuse.

**Server default 1, so no backfill.** Every rule written before today meant
"every unit", which is exactly interval 1. The column is NOT NULL rather than
nullable-like its recurrence siblings because it is meaningful on a
`recurrence = 'none'` row too — a one-shot's interval is 1, not absent — which
is also why `ck_reminders_recurrence_interval` is its own constraint instead
of a clause inside the all-or-nothing `ck_reminders_recurrence`.

**Hand-written, like 0004 and 0005.** `alembic revision --autogenerate` does
not see CHECK constraints, so it would have produced the column and silently
dropped the enum widening — leaving a schema that accepts `minutely` in Python
and rejects it in Postgres.

Reversible: the downgrade narrows the enum back, and will fail loudly if any
sub-day row exists rather than silently corrupting one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_reminder_subday_recurrence"
down_revision = "0050_dream_schedule_state"
branch_labels = None
depends_on = None

_OLD_KINDS = "recurrence IN ('none','daily','weekly','monthly','yearly')"
_NEW_KINDS = (
    "recurrence IN ('none','minutely','hourly','daily','weekly','monthly','yearly')"
)


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column(
            "recurrence_interval", sa.Integer(), nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_reminders_recurrence_interval", "reminders", "recurrence_interval >= 1",
    )
    op.drop_constraint("ck_reminders_recurrence_kind", "reminders", type_="check")
    op.create_check_constraint("ck_reminders_recurrence_kind", "reminders", _NEW_KINDS)


def downgrade() -> None:
    # Narrow FIRST, so a sub-day row makes this fail here — loudly, with the
    # data intact — rather than after the column carrying its interval is
    # already gone.
    op.drop_constraint("ck_reminders_recurrence_kind", "reminders", type_="check")
    op.create_check_constraint("ck_reminders_recurrence_kind", "reminders", _OLD_KINDS)
    # AND refuse to discard a multiplier. Narrowing the enum catches `minutely`
    # and `hourly`, but a `weekly, interval=2` row satisfies the OLD kinds
    # perfectly — it would pass, lose its step to `drop_column`, and come back
    # on the next upgrade as interval 1 through the server default. A
    # fortnightly series would then fire weekly, forever, with nothing raised
    # anywhere. The first version of this downgrade was honest about one of the
    # two columns it drops (#109 review).
    rows = op.get_bind().execute(
        sa.text("SELECT count(*) FROM reminders WHERE recurrence_interval <> 1")
    ).scalar_one()
    if rows:
        raise RuntimeError(
            f"{rows} reminder(s) repeat every N>1 units; downgrading would "
            "silently reset them to every 1. Cancel or let those series finish "
            "first — this refuses rather than corrupt them."
        )
    op.drop_constraint("ck_reminders_recurrence_interval", "reminders", type_="check")
    op.drop_column("reminders", "recurrence_interval")
