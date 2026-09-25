"""standing.working_set / standing_revision.working_set -- a second, small
structured field beside `body`: things the fold found this turn and has not
yet given the room (design/standing-compact/30-working-set.md, issue #445).

WHAT WAS MISSING. Standing's 1200-character paragraph is rewritten whole
every turn and ranks "found" below "concluded or committed" (10-changes §5),
so a fold that found several options -- several restaurants a search
returned, only two of them sent -- has no way to hold more than one without
either naming all of them in one crowded sentence or dropping to zero.
Measured (issue #445, PR #518, objective v8): residue moved 0/5 -> 1/5 once
the prose named the finding-and-usefulness test, and the fold now carries
ONE unsent fact tied to an open plan but still never the other restaurants a
search found -- a set of options is several items of the SAME rank, and the
paragraph shape gives the model no way to hold more than one.

WHY A SEPARATE FIELD, NOT A LOOSER BODY CAP. A working-set item is not prose
under `body`'s own argument ("a position that cannot be stated in 1200
characters is a summary, not a position") -- it is N short, independently
droppable strings, and folding it into the same budget means every option
found competes with "concluded or committed" clauses for the same characters
under a ranking that puts it last. A separate small budget removes that
competition rather than re-ranking it.

TWO CEILINGS, `body`'s OWN SHAPE. `jsonb_array_length(...) <= 20` and
`octet_length(working_set::text) <= 2400` are the ABSOLUTE ceiling -- a
backstop against a config typo, not the size decision. The OPERATING cap
(`[standing] working_set_max_items` = 5, `working_set_max_chars` = 600,
`StandingConfig`) is what the fold's write path actually refuses at, well
under this ceiling, the same two-layer split `STANDING_BODY_CEILING_CHARS`
(4000) and `DEFAULT_STANDING_MAX_CHARS` (1200) already make for `body`.

ON BOTH TABLES, `standing_revision`'S OWN REASON (0062_standing_revision_
source): a revision holds a value the head held, so a working set the head
accepted and the revision table refused would be a history recording a
position that could never have been taken.

NO BACKFILL. `'[]'::jsonb` is both the default for a new row and the correct
reading of every row that predates the column -- migration 0059's argument
for `standing_revision` itself, one field over: nothing before this column
existed could have written anything else.

THE NUMBERS ARE WRITTEN OUT rather than read from `nora.infra.sql.schema`,
following 0059/0062: a migration is a frozen historical statement, and one
reading a live constant silently changes what it applied when the constant
moves. `tests/infra/test_schema_parity.py` is what catches a drift between
this file and `nora.domain.standing`'s constants.

25 characters for the revision id: `alembic_version.version_num` is
varchar(32), and `tests/architecture/test_migration_revision_fits_version_column.py`
is what a longer name would fail.

Revision ID: 0065_standing_working_set
Revises: 0064_proactive_open_note
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0065_standing_working_set"
down_revision = "0064_proactive_open_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standing",
        sa.Column(
            "working_set", pg.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "standing_working_set_fits_the_ceiling_check",
        "standing",
        "jsonb_array_length(working_set) <= 20 "
        "and octet_length(working_set::text) <= 2400",
    )
    op.add_column(
        "standing_revision",
        sa.Column(
            "working_set", pg.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "standing_revision_working_set_fits_the_ceiling_check",
        "standing_revision",
        "jsonb_array_length(working_set) <= 20 "
        "and octet_length(working_set::text) <= 2400",
    )


def downgrade() -> None:
    op.drop_constraint(
        "standing_revision_working_set_fits_the_ceiling_check",
        "standing_revision", type_="check",
    )
    op.drop_column("standing_revision", "working_set")
    op.drop_constraint(
        "standing_working_set_fits_the_ceiling_check",
        "standing", type_="check",
    )
    op.drop_column("standing", "working_set")
