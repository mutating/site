"""Distinguish API tokens from login credentials without guessing legacy purpose.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("access_tokens") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="legacy")
        )
        batch.create_check_constraint("token_purpose", "purpose IN ('api', 'login', 'legacy')")


def downgrade():
    with op.batch_alter_table("access_tokens") as batch:
        batch.drop_constraint("token_purpose", type_="check")
        batch.drop_column("purpose")
