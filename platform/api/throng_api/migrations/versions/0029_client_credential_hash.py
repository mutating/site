"""Move agent credential ownership to the enrolling client.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_enrollments") as batch:
        batch.drop_column("credential_encrypted")


def downgrade():
    with op.batch_alter_table("agent_enrollments") as batch:
        batch.add_column(sa.Column("credential_encrypted", sa.LargeBinary(), nullable=True))
