"""Index the migration baseline independently of ordinary resource history.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_resource_history_reason_created_at",
        "computer_resource_changes",
        ["reason", "created_at"],
    )


def downgrade():
    op.drop_index("ix_resource_history_reason_created_at", table_name="computer_resource_changes")
