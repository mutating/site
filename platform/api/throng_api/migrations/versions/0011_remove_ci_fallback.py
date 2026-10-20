"""Remove hosted placement policy; preserve historical placement failures.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE ci_reservations SET status = 'failed' WHERE status = 'fallback'")
    op.drop_column("ci_reservations", "fallback")
    op.drop_column("ci_routes", "fallback")


def downgrade():
    for table in ("ci_routes", "ci_reservations"):
        op.add_column(
            table, sa.Column("fallback", sa.String(100), nullable=False, server_default="none")
        )
