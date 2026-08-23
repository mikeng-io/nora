"""tag_trace — write-once archival retention for `TagTrace` (spec
docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
§6.1, §14.6). One row per completed tag turn: `messages`/`transcript` land as
JSONB rather than normalised into child tables, matching
`fact_candidate.observed_in`'s own JSONB-array precedent — this is an
archival table read back WHOLE by `SqlTagTraceStore.recent`, never queried by
a field inside either blob, so there is nothing a child table would buy here
that a blob does not already give.

**The CHECK is written HERE as well as in `schema.py`, and that is not
redundancy** — `compare_metadata` (what `tests/infra/test_schema_parity.py`'s
`test_the_metadata_matches_the_migrated_database` runs) is blind to CHECK
constraints (CLAUDE.md; demonstrated on this repo 2026-08-20 by deleting four
of them from `schema.py` alone and watching the whole suite stay green). A
CHECK declared only on the `sa.Table` is documentation; this file is what
puts it in the database.

- `tag_trace_outcome_check` — the closed set `nora.domain.tag_trace.
  VALID_OUTCOMES` already enforces at the dataclass boundary
  (`TagTrace.__post_init__`). Both layers say the same six values so a row
  reaching the database by any path other than `SqlTagTraceStore.save`
  (a hand-rolled `INSERT`, a future second writer) is refused identically
  rather than only by the Python layer nothing forces a second writer to
  import.

**No FK, no workspace column.** `TagTrace.conversation_id` is Nora's
conversation id, not a fact/note-tier `workspace` — this table is scoped and
read by conversation, per `SqlTagTraceStore.recent`'s own boundary, matching
`TagTrace`'s domain type exactly rather than inventing a second scoping axis
this tier does not have.

Revision ID: 0030_tag_trace
Revises: 0029_music_session
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0030_tag_trace"
down_revision = "0029_music_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tag_trace",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("messages", pg.JSONB, nullable=False),
        sa.Column("transcript", pg.JSONB, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "outcome in ('send_reply','recovered','silent','vetoed',"
            "'timeout','llm_error')",
            name="tag_trace_outcome_check",
        ),
    )
    op.create_index(
        "idx_tag_trace_conversation_created", "tag_trace",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_tag_trace_conversation_created", table_name="tag_trace")
    op.drop_table("tag_trace")
