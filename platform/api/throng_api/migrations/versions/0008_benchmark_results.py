"""Versioned benchmark results per computer and benchmark name.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("computers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("benchmark_name", sa.String(200), nullable=False),
        sa.Column("number", sa.Float(), nullable=False),
        sa.Column("benchmarker_version", sa.String(200), nullable=False),
        sa.UniqueConstraint("computer_id", "benchmark_name", "benchmarker_version"),
        sa.CheckConstraint("number >= 0", name="nonnegative_score"),
    )
    op.create_index("ix_benchmark_results_computer_id", "benchmark_results", ["computer_id"])


def downgrade():
    op.drop_table("benchmark_results")
