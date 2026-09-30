"""cognition_self_wake / cognition_remind_mirror -- step 3a-ii's pace verbs
(`wake_at`/`wake_move`/`wake_cancel`, design/cognition-loop/20-observe.md §3)
and the mirrored, held `remind`/`remind_move`/`remind_cancel` family
(30-cognition.md §2: "every reminder cognition writes is mirrored to a
review channel and held"), plus the FK restore step 3a-i's review left open.

TWO NEW TABLES, both append-only (`nora.infra.sql.history.APPEND_ONLY`):

`cognition_self_wake`: one row per pace-verb TRANSITION. `wake_ref` is the
stable id `wake_at` mints; `wake_move`/`wake_cancel` name it again rather
than a database id, because a self-wake's identity is what the model
addresses it by across calls, and a model never sees a row's primary key.
`transition` is `created`/`moved`/`cancelled` — the versioned-transition
shape 20 §3 states ("`wake_move`/`wake_cancel` are versioned transitions,
logged"), not a row updated in place. `stimulus_id` is the `self` stimulus
row a `created` or `moved` transition appends to the unit's `stimulus`
table (so the ALREADY-LIVE wake evaluator picks it up as a `self` trigger
with no evaluator change); null on `cancelled` (no stimulus survives to
point at) and on a refusal. `supersedes` names the wake_ref an earlier
pending self-wake this one displaces (20 §3: "a new self-wake that shares
any subject row with a pending one supersedes it") — this is a NORMAL
`created` transition, not a refusal.

`cognition_remind_mirror`: one row per mirrored act-verb call. `reminder_ref`
is the same kind of model-addressed stable id, minted by `remind` and named
again by `remind_move`/`remind_cancel`. Nothing here ever reaches the fire
path or a real reminders row (`tests/architecture/
test_cognition_shadow_cannot_write_or_send.py` is where that stays proven).

THE FK RESTORE (review follow-up on #625's step 3a-i). `cognition_session_
close.session_id` and `cognition_would_write.session_id` now FK
`cognition_session.id` — the reviewer's finding that this breaks no test:
`tests/integration/test_snapshot_holds_what_was_held.py`'s APPEND fixture
leaves `session_id` unset (null) for both, and a null FK column is never
checked, so the ordering hazard the step 3a-i docstring worried about
(no guarantee a session row exists yet when the fixture appends
independently) never actually arises there. `cognition_wake.id` stays
FK-less on `cognition_session.wake_id`, unchanged: THAT one really does
break `tests/contract/test_stimulus_store_contract.py`'s TRUNCATE of
`cognition_wake` between tests, because `cognition_wake` is truncated ALONE
in that suite while `cognition_session` (with rows) is not also truncated
there -- a fundamentally different shape from either table here, which are
both empty at the point every truncate in this tree touches them.

`cognition_self_wake` and `cognition_remind_mirror` both FK `session_id` to
`cognition_session.id` from the start (they are new at this step, so there
is no "restore" for them — they simply never had the ordering hazard to
avoid). Every place in this tree that TRUNCATEs `cognition_session` now
also names these two tables in the same statement, or the truncate is
refused outright (see the contract test's own comment).

THE SESSION LIFECYCLE (review of #627). Three more append-only tables, none
with a foreign key (a session id may be appended by a restore fixture before
its session, and `wake_id` already set the precedent):

`cognition_session_end`: one row when a session ends (`closed`, `failed`,
`timeout`, `cancelled`); a session row with none is in flight. Only `closed`
advances the unit's watermark (20 §3: «a failed session does not advance
it»), which the stimulus store's `view` reads.

`cognition_carry`: a stimulus a closed session returned to the next agenda
with a strike (30 §2), or released or struck; the newest row per
`(unit, stimulus_id)` is current.

`stimulus_retraction`: a `self` stimulus taken back when its self-wake is
cancelled, moved or superseded, so the evaluator never wakes on it.
`cognition_self_wake` also admits the `fired` transition and the code-only
`wake_fire` verb (a session picking a due self-wake up frees its slot).

Revision ID: 0079_cognition_pace_remind
Revises: 0078_cognition_session
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0079_cognition_pace_remind"
down_revision = "0078_cognition_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_cognition_session_close_session_id",
        "cognition_session_close", "cognition_session",
        ["session_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_cognition_would_write_session_id",
        "cognition_would_write", "cognition_session",
        ["session_id"], ["id"],
    )

    op.create_table(
        "cognition_self_wake",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "session_id", sa.BigInteger,
            sa.ForeignKey("cognition_session.id"), nullable=True,
        ),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("verb", sa.Text, nullable=False),
        sa.Column("wake_ref", sa.Text, nullable=True),
        sa.Column("transition", sa.Text, nullable=False),
        sa.Column("subject_rows", pg.JSONB, nullable=False),
        sa.Column("why", sa.Text, nullable=False),
        sa.Column("at_ms", sa.BigInteger, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("refusal_reason", sa.Text, nullable=True),
        sa.Column("refusal_detail", sa.Text, nullable=True),
        sa.Column("stimulus_id", sa.BigInteger, nullable=True),
        sa.Column("supersedes", sa.Text, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "verb IN ('wake_at', 'wake_move', 'wake_cancel', 'wake_fire')",
            name="ck_cognition_self_wake_verb",
        ),
        sa.CheckConstraint(
            "transition IN ('created', 'moved', 'cancelled', 'fired')",
            name="ck_cognition_self_wake_transition",
        ),
        sa.CheckConstraint(
            "outcome IN ('recorded', 'refused')", name="ck_cognition_self_wake_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'recorded') OR (refusal_reason IS NOT NULL)",
            name="ck_cognition_self_wake_outcome_shape",
        ),
    )
    op.create_index(
        "idx_cognition_self_wake_unit_ref", "cognition_self_wake", ["unit", "wake_ref"],
    )

    op.create_table(
        "cognition_remind_mirror",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "session_id", sa.BigInteger,
            sa.ForeignKey("cognition_session.id"), nullable=True,
        ),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("verb", sa.Text, nullable=False),
        sa.Column("reminder_ref", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("intent", sa.Text, nullable=True),
        sa.Column("subject_rows", pg.JSONB, nullable=False),
        sa.Column("at_ms", sa.BigInteger, nullable=True),
        sa.Column("why", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("refusal_reason", sa.Text, nullable=True),
        sa.Column("refusal_detail", sa.Text, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "verb IN ('remind', 'remind_move', 'remind_cancel')",
            name="ck_cognition_remind_mirror_verb",
        ),
        sa.CheckConstraint(
            "outcome IN ('recorded', 'refused')",
            name="ck_cognition_remind_mirror_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'recorded') OR (refusal_reason IS NOT NULL)",
            name="ck_cognition_remind_mirror_outcome_shape",
        ),
    )
    op.create_index(
        "idx_cognition_remind_mirror_unit_ref",
        "cognition_remind_mirror", ["unit", "reminder_ref"],
    )

    op.create_table(
        "cognition_session_end",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("session_id", sa.BigInteger, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("at_ms", sa.BigInteger, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "outcome IN ('closed', 'failed', 'timeout', 'cancelled')",
            name="ck_cognition_session_end_outcome",
        ),
    )
    op.create_index(
        "uq_cognition_session_end_session", "cognition_session_end", ["session_id"],
        unique=True,
    )

    op.create_table(
        "cognition_carry",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("stimulus_id", sa.BigInteger, nullable=False),
        sa.Column("strike", sa.Integer, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("waits", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("session_id", sa.BigInteger),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "state IN ('carried', 'released', 'struck')", name="ck_cognition_carry_state",
        ),
    )
    op.create_index(
        "idx_cognition_carry_unit_stimulus", "cognition_carry",
        ["unit", "stimulus_id", "id"],
    )

    op.create_table(
        "stimulus_retraction",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("stimulus_id", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
    )
    op.create_index(
        "uq_stimulus_retraction_stimulus", "stimulus_retraction", ["stimulus_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_stimulus_retraction_stimulus", table_name="stimulus_retraction")
    op.drop_table("stimulus_retraction")
    op.drop_index("idx_cognition_carry_unit_stimulus", table_name="cognition_carry")
    op.drop_table("cognition_carry")
    op.drop_index(
        "uq_cognition_session_end_session", table_name="cognition_session_end",
    )
    op.drop_table("cognition_session_end")

    op.drop_index(
        "idx_cognition_remind_mirror_unit_ref", table_name="cognition_remind_mirror",
    )
    op.drop_table("cognition_remind_mirror")

    op.drop_index("idx_cognition_self_wake_unit_ref", table_name="cognition_self_wake")
    op.drop_table("cognition_self_wake")

    op.drop_constraint(
        "fk_cognition_would_write_session_id", "cognition_would_write", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_cognition_session_close_session_id", "cognition_session_close",
        type_="foreignkey",
    )
