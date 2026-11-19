"""Store private organization avatars and backgrounds.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("companies") as batch:
        batch.add_column(sa.Column("avatar_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("background_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("avatar_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("background_version", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("companies") as batch:
        batch.drop_column("background_version")
        batch.drop_column("avatar_version")
        batch.drop_column("background_data")
        batch.drop_column("avatar_data")
