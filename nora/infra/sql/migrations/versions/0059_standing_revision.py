"""standing_revision -- the room position gains the history the other two
model-written tiers already have.

WHAT WAS MISSING. `standing` is one row per `(platform, conversation_id)` and
every fold is an `ON CONFLICT DO UPDATE` over it, so the position Nora held
before the current one is not stale, it is GONE -- overwritten in place by the
next fold, with nothing anywhere holding the value it replaced. The note tier
has `note_revision` and the culture tier has `culture_revision`; standing was
the one model-written tier with no chain behind its head. That is the gap this
closes, and it closes it the same way both siblings already do: the head keeps
its shape and its semantics exactly, and an append-only table beside it records
what the head was each time it moved.

WHY IT MATTERS BEYOND TIDINESS. A position that cannot be read back cannot be
re-derived, and re-derivable is the third of the three rules AGENTS.md names.
It also makes a real past moment reconstructible: given a room and an instant,
the position Nora actually held then is a single index read rather than a thing
nobody can answer. Every other input to a turn is already point-in-time
recoverable -- `messages` is append-only with real timestamps, `note_revision`
and `culture_revision` are chains -- so standing was the ONE tier that made the
whole reconstruction impossible, and one missing tier is the same as none.

NO FOREIGN KEY, for `culture_revision`'s reason one table over. `standing`'s
key is the composite `(platform, conversation_id)` text pair rather than a
surrogate id, so a revision would have to carry both columns and name them in a
two-column FK -- and the direction that FK could point is the one that fails:
this table is written in the same transaction as the head's upsert, and a
constraint requiring the head row to exist first would be a constraint on
statement ORDER inside that transaction rather than on the data. The columns are
carried instead, which is what makes the as-of read a single-table query with no
join.

`updated_at` IS THE FOLD'S OWN TIMESTAMP, and there is deliberately no second
`written_at` column beside it. `note_revision` and `culture_revision` need one
because their heads carry no clock of their own; `standing.updated_at` already
means "when this position was taken", so copying it here carries the fold's
instant with no second value that could disagree with it. One clock, one
meaning: a `written_at` that was set from the same source would be a duplicate,
and one set from a different source would be a way for a revision to claim an
instant its own head never had.

THE NUMBERS ARE WRITTEN OUT rather than read from `nora.infra.sql.schema`,
following `0055_culture`, `0053_decision_log` and `0035_turn_verdict`: a
migration is a frozen historical statement, and one reading a live constant
silently changes what it applied when the constant moves.
`tests/infra/test_schema_parity.py` is what catches a change to one that did
not reach the other.

NO BACKFILL, and the absence is a decision. The rows that would be backfilled
are the CURRENT head of each room, and inserting them here would stamp them
with an `updated_at` that is true (it is the head's own) attached to a
provenance claim that is not: nothing observed those folds, and a history whose
earliest entries were manufactured by a migration is a history a reader cannot
tell apart from one that was recorded. So a room's chain starts at its next
fold, `read_as_of` answers `None` for any instant before that, and `None`
already means exactly that on this tier -- the cold-start state
`last_folded_row_id is null` names.

`updated_at DESC, id DESC` is authored by hand for `idx_messages_conv_ts`'s
reason: autogenerate cannot see per-column index ordering and reports permanent
phantom drift on it, so this index is excluded from `alembic check` in
`env.py` and its ordering is asserted from `pg_index.indoption` by
`tests/infra/test_schema_parity.py` instead.

THE INDEX'S TRAILING `id DESC` IS NOT THE AS-OF READ'S TIE-BREAK. `id` is
`gen_random_uuid()` and carries no relationship to which fold was written
first, so ordering on it picks a position by uuid luck rather than by time --
measured over twelve trials of two folds sharing one millisecond, it returned
the EARLIER position seven times. `SqlStandingStore.read_as_of` therefore orders
`updated_at DESC, fold_count DESC, id DESC`; this index's `(platform,
conversation_id, updated_at DESC)` prefix serves the filter and the backwards
scan, and the tie-break sorts over the few rows sharing the bound millisecond.
The index keeps its shape because that prefix is what it is for.

Revision ID: 0059_standing_revision
Revises: 0058_unit_rename
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0059_standing_revision"
down_revision = "0058_unit_rename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standing_revision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # The head's key, carried rather than referenced -- see the module
        # docstring on why there is no foreign key.
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("fold_count", sa.Integer, nullable=False),
        # The fold's own instant, copied from `standing.updated_at` in the same
        # transaction as the upsert that set it.
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.Column("consumed_verdict_tag", sa.Text, nullable=True),
        sa.Column("last_folded_row_id", sa.BigInteger, nullable=True),
        # 4000 characters. The SAME number `standing_body_fits_the_ceiling_
        # check` carries on the head (`nora.domain.standing.
        # STANDING_BODY_CEILING_CHARS`), because a revision holds the value the
        # head held: a ceiling on one and not the other would let history record
        # a position the head could never have taken. `length()` and never
        # `octet_length()` -- a position is routinely Cantonese, where the two
        # differ threefold.
        sa.CheckConstraint(
            "length(btrim(body)) > 0 and length(body) <= 4000",
            name="standing_revision_body_fits_the_ceiling_check",
        ),
        sa.CheckConstraint(
            "fold_count >= 0",
            name="standing_revision_fold_count_is_not_negative_check",
        ),
    )
    op.create_index(
        "idx_standing_revision_room_updated", "standing_revision",
        ["platform", "conversation_id", sa.text("updated_at DESC"),
         sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_standing_revision_room_updated", table_name="standing_revision"
    )
    op.drop_table("standing_revision")
