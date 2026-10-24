"""Workflow resource overrides, with existing route resources remaining the default.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ci_routes",
        sa.Column("workflow_profiles", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("ci_routes", "workflow_profiles")
