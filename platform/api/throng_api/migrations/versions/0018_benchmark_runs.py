"""Preserve individual benchmark runs, including repeated tooling versions.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("benchmark_results") as batch:
        batch.add_column(sa.Column("run_id", sa.Uuid(as_uuid=False), nullable=True))
    # No individual runs can be reconstructed from the old overwritten rows.
    # Preserve every surviving result and its original timestamp/UUID as-is.
    op.execute(sa.text("UPDATE benchmark_results SET run_id = id"))
    with op.batch_alter_table("benchmark_results") as batch:
        batch.drop_constraint(op.f("uq_benchmark_results_computer_id"), type_="unique")
        batch.alter_column("run_id", existing_type=sa.Uuid(as_uuid=False), nullable=False)
        batch.create_unique_constraint(
            op.f("uq_benchmark_results_computer_id"),
            ["computer_id", "benchmark_name", "run_id"],
        )


def downgrade():
    connection = op.get_bind()
    rows = sa.table(
        "benchmark_results",
        sa.column("id"),
        sa.column("run_id"),
        sa.column("computer_id"),
        sa.column("benchmark_name"),
        sa.column("benchmarker_version"),
    )
    duplicates = (
        sa.select(sa.func.count())
        .select_from(rows)
        .group_by(rows.c.computer_id, rows.c.benchmark_name, rows.c.benchmarker_version)
        .having(sa.func.count() > 1)
    )
    if connection.execute(duplicates).first():
        raise CommandError("Cannot downgrade while repeated benchmark runs exist")
    new_runs = sa.select(rows.c.id).where(rows.c.run_id != rows.c.id).limit(1)
    if connection.execute(new_runs).first():
        raise CommandError("Cannot downgrade while benchmark run identifiers would be lost")
    with op.batch_alter_table("benchmark_results") as batch:
        batch.drop_constraint(op.f("uq_benchmark_results_computer_id"), type_="unique")
        batch.drop_column("run_id")
        batch.create_unique_constraint(
            op.f("uq_benchmark_results_computer_id"),
            ["computer_id", "benchmark_name", "benchmarker_version"],
        )
