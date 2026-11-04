"""scoped computer runtime: durable execution tasks + browser tab bindings.

`computer_tasks` is the durable intent the per-scope FIFO reads: one row per
execution segment (native op, browse objective, capture, worker job) written
BEFORE its Redis stream entry, terminal result persisted BEFORE the stream
ack. `browser_tasks` records which task owns which CDP target on which VM
instance — continuation binding, the four-target cap's population, and the
stale-instance expiry all read it. Insert-only history: rows move status,
never delete.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0147_computer_tasks"
down_revision = "0146_multi_post_native_ids"
branch_labels = None
depends_on = None

_TASK_STATUSES = ("queued", "running", "done", "failed", "cancelled",
                  "unavailable", "interrupted")
_TASK_KINDS = ("native", "browse", "capture", "worker")
_TAB_STATUSES = ("open", "closed", "expired")


def upgrade() -> None:
    op.create_table(
        "computer_tasks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("origin", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("parent", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(x) for x in _TASK_STATUSES)})",
            name="ck_computer_tasks_status"),
        sa.CheckConstraint(
            f"kind IN ({', '.join(repr(x) for x in _TASK_KINDS)})",
            name="ck_computer_tasks_kind"),
    )
    op.create_index("ix_computer_tasks_scope_open", "computer_tasks",
                    ["scope", "created_at"],
                    postgresql_where=sa.text(
                        "status IN ('queued','running')"))
    op.create_table(
        "browser_tasks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("task_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("instance", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("via", sa.Text, nullable=False, server_default="task"),
        sa.Column("last_url", sa.Text, nullable=False, server_default=""),
        sa.Column("last_title", sa.Text, nullable=False, server_default=""),
        sa.Column("detail", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(x) for x in _TAB_STATUSES)})",
            name="ck_browser_tasks_status"),
        sa.UniqueConstraint("scope", "instance", "target_id",
                            name="uq_browser_tasks_target"),
    )
    op.create_index("ix_browser_tasks_scope_open", "browser_tasks",
                    ["scope", "instance"],
                    postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_table("browser_tasks")
    op.drop_table("computer_tasks")
