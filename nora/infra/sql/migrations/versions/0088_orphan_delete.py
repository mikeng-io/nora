"""orphan_delete: a platform delete whose target is not on record YET, kept so the
message is withdrawn the moment it is (design/context-assembly/04-every-occasion.md
§7 (A), DEC-276).

A delete can arrive before the message it names (Discord dispatches each event as its
own task and a photo is recorded only after its custody fetch), after a restart that
took an in-memory hold with it, long after a hold would have expired, or while the
lookup of its target was failing. `message_delete.message_id` needs a target, so such a
delete had nowhere durable to live and the message, when it landed, stayed visible
forever. This table is where it lives until it is applied (hide plus cascade) or
purged.

One NEW small table. `messages` is not touched. Keyed by what the lookup uses: the
platform handle (`handle`, '' when the platform names the target by timestamp) or
Signal's sent-timestamp (`sent_ts`), in one conversation on one platform. The deleter is
kept as every id form the platform gave (`deleter_ids`), the one the parser picked
(`deleter_id`) and the person it resolved to, because on Signal and WhatsApp the delete
is honoured only from the message's author, a check made when the message arrives.
Ids and times only; no text, no name.

BOUNDED (amended before this migration shipped): a unique key on the delivery (a
re-delivered event does not add a row), indexes the match and the caps read (a handle
and a sent-timestamp, each per room, and the deleter), and caps and a 3-day TTL enforced
in code (`nora.domain.delete_authorship`).

In-place table (a row is deleted when applied or purged): it carries the
`nora_row_history` trigger, as `citation` does for the same reason (a DELETE keeps a
`delete` version), and is listed in `nora.infra.sql.history.HISTORY_TABLES`.

Follows `0087_document_state`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0088_orphan_delete
Revises: 0087_document_state
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0088_orphan_delete"
down_revision = "0087_document_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orphan_delete",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("handle", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("sent_ts", sa.BigInteger),
        sa.Column("deleter_id", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "deleter_ids", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("deleter_person_id", sa.Text),
        sa.Column("event_ts", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "handle <> '' OR sent_ts IS NOT NULL", name="ck_orphan_delete_names_a_target",
        ),
    )
    # The match by handle and by Signal's timestamp, each per room; a partial index
    # because a row names one or the other.
    op.create_index(
        "idx_orphan_delete_handle", "orphan_delete",
        ["platform", "conversation_id", "handle"], postgresql_where=sa.text("handle <> ''"),
    )
    op.create_index(
        "idx_orphan_delete_sent_ts", "orphan_delete",
        ["platform", "conversation_id", "sent_ts"],
        postgresql_where=sa.text("sent_ts IS NOT NULL"),
    )
    # The per-deleter cap's count, and the purge by age.
    op.create_index("idx_orphan_delete_deleter", "orphan_delete", ["platform", "deleter_id"])
    op.create_index("idx_orphan_delete_recorded", "orphan_delete", ["recorded_at"])
    # One row per delivery: the same event delivered again (or five times) adds nothing.
    # Also serves the per-room cap's count (its leading columns).
    op.execute(
        "CREATE UNIQUE INDEX uq_orphan_delete_delivery ON orphan_delete "
        "(platform, conversation_id, handle, (coalesce(sent_ts, -1)), deleter_id)"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"orphan_delete\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"orphan_delete\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"orphan_delete\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"orphan_delete\"")
    # Going back down forgets every delete still waiting for its message. They were
    # never applied, so nothing visible changes; the messages they name stay visible.
    op.execute("DROP INDEX IF EXISTS uq_orphan_delete_delivery")
    op.drop_index("idx_orphan_delete_recorded", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_deleter", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_sent_ts", table_name="orphan_delete")
    op.drop_index("idx_orphan_delete_handle", table_name="orphan_delete")
    op.drop_table("orphan_delete")
