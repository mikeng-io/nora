"""computer_exports — every file that left Nora's computer through
`computer_export` (design/sandbox-runtime §6, §7; S3a).

One row per export that reached its destination: the room and workspace
path it came from, the sha256 and size Nora computed on the bytes she
received (never the VM's claim), the media type read off those bytes, and
where it went: `link` (a Drop artifact, with its `artifact_id` and `url`),
`chat` (an attachment in the room) or `keep` (Nora's blob store). Both
`chat` and `keep` carry the `blob_key` the bytes are held under.

The FIRST row of an `artifact_id` names the only room that may revise it
(`computer_export(..., artifact_id=...)`); a revision is a new row with
`revised` true. So the table is authorisation state, and it is insert-only
the way `computer_events` is: `computer_exports_refuse_change` raises on any
UPDATE or DELETE, and on a TRUNCATE unless the session set
`nora.row_history = 'off'` (the `0068` guard's switch a table-reset fixture
uses). It guards against bugs and mistaken statements, not the owner role
(`0081_computer_events` says why).

Revision ID: 0083_computer_exports
Revises: 0082_computer_sessions
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0083_computer_exports"
down_revision = "0082_computer_sessions"
branch_labels = None
depends_on = None

_REFUSE_CHANGE = """
CREATE FUNCTION computer_exports_refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'TRUNCATE' AND current_setting('nora.row_history', true) = 'off' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION USING
        MESSAGE = 'computer_exports is append-only: ' || TG_OP || ' refused',
        ERRCODE = 'insufficient_privilege';
END
$fn$
"""


def upgrade() -> None:
    op.create_table(
        "computer_exports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("operation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", sa.Text, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("revised", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("blob_key", sa.Text, nullable=True),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "destination IN ('chat', 'keep', 'link')",
            name="ck_computer_exports_destination",
        ),
        sa.CheckConstraint(
            "(destination = 'link' AND artifact_id IS NOT NULL AND url IS NOT NULL) OR "
            "(destination <> 'link' AND artifact_id IS NULL AND NOT revised "
            "AND blob_key IS NOT NULL)",
            name="ck_computer_exports_target",
        ),
    )
    op.create_index(
        "idx_computer_exports_artifact", "computer_exports", ["artifact_id", "created_at"],
    )
    op.create_index(
        "idx_computer_exports_room", "computer_exports", ["conversation_id", "created_at"],
    )
    op.execute(_REFUSE_CHANGE)
    op.execute(
        "CREATE TRIGGER computer_exports_append_only BEFORE UPDATE OR DELETE "
        "ON computer_exports FOR EACH ROW EXECUTE FUNCTION computer_exports_refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER computer_exports_no_truncate BEFORE TRUNCATE "
        "ON computer_exports FOR EACH STATEMENT "
        "EXECUTE FUNCTION computer_exports_refuse_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS computer_exports_no_truncate ON computer_exports")
    op.execute("DROP TRIGGER IF EXISTS computer_exports_append_only ON computer_exports")
    op.execute("DROP FUNCTION IF EXISTS computer_exports_refuse_change()")
    op.drop_index("idx_computer_exports_room", table_name="computer_exports")
    op.drop_index("idx_computer_exports_artifact", table_name="computer_exports")
    op.drop_table("computer_exports")
