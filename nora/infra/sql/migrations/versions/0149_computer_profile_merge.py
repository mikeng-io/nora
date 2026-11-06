"""Join independently landed profile media keys and scoped computer tables.

Keep both branch histories valid: an existing database at either parent must
apply the other branch before it reaches the application's single head.
"""

revision = "0149_computer_profile_merge"
down_revision = ("0147_profile_media_keys", "0148_computer_task_maintenance")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
