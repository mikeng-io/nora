"""message_edit -- an edit is recorded beside the message it edits.

Before this, Signal and WhatsApp wrote an edit as a NEW `messages` row with
nothing linking it to the row it replaced, and Instagram and Discord dropped
edits entirely (design/message-edits/00). This migration gives an edit
somewhere to go that is not `messages`, and gives `messages` the two facts an
edit needs to be joined and judged:

* `message_edit` -- one row per edit, pointing at its `messages` row, CASCADE
  on delete so `prune_all` takes a message's edits with it.
* `messages.platform_message_id` -- the platform's handle, which is how
  WhatsApp, Instagram and Discord name the message an edit targets.
* `messages.mentions_json` -- the mention ids, so the gate can ask whether the
  version an edit replaced already addressed Nora.

THE BACKFILL IS FOR `platform_message_id` ONLY. Every row already carries its
handle inside `raw_json` -- `id` for WhatsApp and Discord (Discord's is a JSON
number, and `->>` renders it as the same digits `str(message.id)` does),
`message_id` for Instagram -- so an edit to a message stored before this deploy
still finds it. `mentions_json` is NOT backfilled: recovering it would mean
re-interpreting each platform's envelope here, and a NULL is read by the gate
as "unknown", which refuses (R-5). Edits happen within minutes of the original,
so the NULL window closes on its own.

`raw_json` is TEXT, and a row whose text is not JSON would abort the cast for
the whole statement; `pg_input_is_valid` (Postgres 16+, the deployment runs
18) skips such a row instead of failing the migration on it. Nora's own rows
are skipped (`is_nora = 0`): their `raw_json` is tag bookkeeping, not an
envelope.

Revision ID: 0060_message_edit
Revises: 0059_standing_revision
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_message_edit"
down_revision = "0059_standing_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("platform_message_id", sa.Text))
    op.add_column("messages", sa.Column("mentions_json", sa.Text))
    op.execute(
        """
        update messages
           set platform_message_id = nullif(
                 case platform
                   when 'instagram' then raw_json::jsonb ->> 'message_id'
                   else raw_json::jsonb ->> 'id'
                 end, '')
         where platform in ('whatsapp', 'discord', 'instagram')
           and is_nora = 0
           and pg_input_is_valid(raw_json, 'jsonb')
        """
    )
    op.create_index(
        "idx_messages_conv_platform_message_id", "messages",
        ["conversation_id", "platform_message_id"],
    )
    op.create_table(
        "message_edit",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "message_id", sa.Integer,
            sa.ForeignKey("messages.id", name="message_edit_message_id_fkey",
                          ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.BigInteger, nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("raw_json", sa.Text),
        sa.Column("mentions_json", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False,
                  server_default=sa.text("now()::text")),
    )
    op.create_index(
        "idx_message_edit_message_ts", "message_edit", ["message_id", "ts"],
    )


def downgrade() -> None:
    op.drop_index("idx_message_edit_message_ts", table_name="message_edit")
    op.drop_table("message_edit")
    op.drop_index("idx_messages_conv_platform_message_id", table_name="messages")
    op.drop_column("messages", "mentions_json")
    op.drop_column("messages", "platform_message_id")
