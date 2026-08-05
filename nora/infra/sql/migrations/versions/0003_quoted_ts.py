"""quoted_ts — the parent pointer, so a reply stops reading as a non-sequitur

Hand-written, like 0001 and 0002, and for the same two reasons 0002 spells out:

- **No `op.batch_alter_table`.** On SQLite, batch mode rebuilds the table and
  recreates its indexes from *reflection* — and `schema.py` documents that
  reflection is exactly what cannot see `idx_messages_conv_ts`'s `ts DESC`
  ordering. A rebuild therefore silently downgrades that index to ASC,
  `alembic check` reports nothing, and `recent`'s `ORDER BY ts DESC` quietly
  becomes a sort instead of an index scan.

  **Measured 2026-08-06, and 0002's blanket phrasing needed one correction.**
  A bare `op.batch_alter_table("messages")` around this add_column does NOT
  rebuild: batch mode defaults to `recreate="auto"`, and Alembic's auto rule
  decides a plain ADD COLUMN needs no table copy on SQLite, so it emits the
  same in-place ALTER and the index survives. It is `recreate="always"` — and
  any batch operation SQLite cannot do in place, e.g. dropping a column or
  changing a constraint — that performs the copy. Probed both ways against
  `tests/infra/test_alembic.py::
  test_0003_adds_quoted_ts_without_disturbing_the_ts_desc_index`: the bare form
  passed, `recreate="always"` turned `ts` from xinfo `desc=1` to `desc=0` and
  the test went red.

  So the rule stands, but for the accurate reason: batch mode is one edit away
  from a rebuild, and the rebuild is silent. Plain `ALTER TABLE ... ADD COLUMN`
  is an in-place header edit that cannot become one. The test pins the index
  shape either side of this migration rather than trusting the form of the
  call, which is what makes it catch the rebuild however it is reached.
- **No index on the new column.** Resolution is a dict lookup over the rows
  already in hand, not a query, so there is nothing for an index to serve — and
  the one index that matters here must not be disturbed.

ONE column, not two. The brief offered "quoted author + quoted ts". The author
is derivable from the parent row once found, and that row's `sender_id` /
`person_id` is authoritative, whereas `dataMessage.quote.author` is supplied by
whoever constructed the quote. Storing both would create two sources of truth
that can disagree, and the untrustworthy one is the one that would be rendered
(R-20). Store the pointer; derive the attribution.

Nullable with no server default: NULL means "this message is not a reply",
which is the ordinary case and is not the same thing as 0 — 0 is a real epoch
value. No backfill from `raw_json` either: 20 rows exist, exactly one carries a
quote, and Nora's own rows have no envelope to parse in the first place.

NOTE for whoever runs the gates after pulling this: `tests/infra/
test_schema_parity.py` compares against the live `data/messages.db`, so run
the migration (or start Nora once) BEFORE `uv run pytest -q`, or the schema
gate fails with a column-set mismatch that reads like a schema bug rather than
an un-upgraded database.

Revision ID: 0003_quoted_ts
Revises: 0002_person_id
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_quoted_ts"
down_revision = "0002_person_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("quoted_ts", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "quoted_ts")
