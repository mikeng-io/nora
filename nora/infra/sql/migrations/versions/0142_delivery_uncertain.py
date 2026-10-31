"""Close `messages.delivery_status` and admit `uncertain`.

A bridge send whose platform call may have landed while the answer was
lost is not `failed` — nothing disproved it — and must not stay `pending`,
because a pending row keeps the `live_invocation` owner valid and invites
a blind second send. The column had no CHECK before; the closed set is
now durable (`pending`, `sent`, `failed`, `uncertain`).
"""

from alembic import op

revision = "0142_delivery_uncertain"
down_revision = "0141_bridge_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_messages_delivery_status",
        "messages",
        "delivery_status IN ('pending','sent','failed','uncertain')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE messages SET delivery_status='failed' "
        "WHERE delivery_status='uncertain'"
    )
    op.drop_constraint("ck_messages_delivery_status", "messages")
