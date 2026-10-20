"""Remember verified CI dispatch across runner exit and outcome lookup failures.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ci_reservations", sa.Column("started_at", sa.DateTime(timezone=True)))
    # Only a running record proves dispatch; exited history can be ambiguous.
    op.execute("UPDATE ci_reservations SET started_at = CURRENT_TIMESTAMP WHERE status = 'running'")


def downgrade():
    op.drop_column("ci_reservations", "started_at")
