"""standing.hold_open / standing.i_owe, and their standing_revision siblings
-- room Standing's sectioned schema, live (design/cognition-loop/70-migration.md
step 3 row 3b: "`standing_revise` and the room Standing schema, live: the
debrief and the ingest fold both write through it from here, so the head has
one shape").

WHAT THIS ADDS. `design/cognition-loop/12-schemas.md` §5 gives room Standing
four sections: `Where it stands` (required, `standing.body`, unchanged by
this migration), `I hold open`, `I owe`, and `Found, not yet given`
(`standing.working_set`, unchanged). The two new sections are `hold_open`
and `i_owe`, nullable text, on both `standing` (the head) and
`standing_revision` (its chain) -- `standing_revision`'s own reason
(`0062_standing_revision_source`, restated by `0065_standing_working_set`
one field over): a revision holds a value the head held, so a section the
head accepted and the revision table refused would be a history recording a
position that could never have been taken.

HOW AN EXISTING HEAD MIGRATES. Additively, with no backfill and no rewrite:
`NULL` is "this section has not been revised" and is the correct reading of
every row already in `standing` and `standing_revision` -- the same argument
`0059_standing_revision` made for a chain with no pre-existing history and
`0065_standing_working_set` made for `working_set` itself, one section
further. A room's existing position (`body`, `working_set`) is untouched:
nothing in this migration UPDATEs a single row, so an existing head keeps
every fact it held and gains two empty sections rather than being rewritten
into the new shape. `nora.language.standing_render.render_standing` reflects
this: it appends `I hold open`/`I owe` to the `<standing>` block only when
they are non-NULL, so the rendered block for every row this migration
touches is byte-identical to what it rendered the moment before
(`tests/unit/test_standing_render.py`, the render-parity case this step
adds).

THE CHECK mirrors `standing_body_fits_the_ceiling_check`'s shape, applied to
an OPTIONAL column: a section that is given must not be blank (`NULL` is the
absence, and a blank string would be a second, competing spelling of it --
`nora.domain.standing.Standing.__post_init__`'s own rule for these two
fields, and `WorkingSetItem.bears_on`'s before it). No length ceiling is
asserted in SQL: the domain layer already asserts one
(`STANDING_BODY_CEILING_CHARS`, `Standing.__post_init__`), and neither
section is written by a live caller yet (`nora.infra.sql.standing_revise`'s
own module docstring says why) -- the ceiling this migration is missing is
the one 0059/0065 add for a column immediately in production use; adding it
speculatively here would be a number this migration could not measure
against anything.

WHO WRITES THESE COLUMNS. `nora.infra.sql.standing_revise.standing_revise`,
the one write verb both `nora.engagement.stages.debrief` (the tag path's
fold) and `nora.ingest.stages.standing_event` (the rollup fold) call from
this step forward -- see that module and `nora.domain.standing_revise` for
the schema and mode checks it runs before either column is written.

DOWNGRADE drops both columns from both tables. Any `hold_open`/`i_owe`
content written between an upgrade and a downgrade is lost -- there is no
data to preserve going down because no caller populates either column yet
(the same "additive, nothing lost going up" statement does not hold in
reverse for a column already in use, and 70-migration.md §7 is explicit that
rollback is "the previous behaviour", not lossless in both directions for
every future column).

25 characters for the revision id: `alembic_version.version_num` is
varchar(32), and `tests/architecture/test_migration_revision_fits_version_column.py`
is what a longer name would fail.

Revision ID: 0076_standing_sections
Revises: 0075_withdrawal
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0076_standing_sections"
down_revision = "0075_withdrawal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("standing", sa.Column("hold_open", sa.Text, nullable=True))
    op.add_column("standing", sa.Column("i_owe", sa.Text, nullable=True))
    op.create_check_constraint(
        "standing_sections_are_not_blank_when_given_check",
        "standing",
        "(hold_open is null or length(btrim(hold_open)) > 0) "
        "and (i_owe is null or length(btrim(i_owe)) > 0)",
    )
    op.add_column(
        "standing_revision", sa.Column("hold_open", sa.Text, nullable=True),
    )
    op.add_column(
        "standing_revision", sa.Column("i_owe", sa.Text, nullable=True),
    )
    op.create_check_constraint(
        "standing_revision_sections_are_not_blank_when_given_check",
        "standing_revision",
        "(hold_open is null or length(btrim(hold_open)) > 0) "
        "and (i_owe is null or length(btrim(i_owe)) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "standing_revision_sections_are_not_blank_when_given_check",
        "standing_revision", type_="check",
    )
    op.drop_column("standing_revision", "i_owe")
    op.drop_column("standing_revision", "hold_open")
    op.drop_constraint(
        "standing_sections_are_not_blank_when_given_check",
        "standing", type_="check",
    )
    op.drop_column("standing", "i_owe")
    op.drop_column("standing", "hold_open")
