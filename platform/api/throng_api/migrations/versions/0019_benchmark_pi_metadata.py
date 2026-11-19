"""Store optional benchset Pi equivalents and reference calibration.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def begin_sqlite_schema_change(connection):
    # SQLite's default driver mode does not start a transaction for DDL.
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def lock_postgres_tables(connection, tables, mode):
    try:
        connection.exec_driver_sql(f"LOCK TABLE {tables} IN {mode} MODE NOWAIT")
    except sa.exc.DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        raise CommandError(
            "Cannot change the schema while benchmark tables are in use. Retry after active transactions finish."
        ) from error


def upgrade():
    connection = op.get_bind()
    begin_sqlite_schema_change(connection)
    if connection.dialect.name == "postgresql":
        # Creating the FK locks computers. 0018 can already hold the results
        # lock in this transaction, so waiting on a computer deletion can deadlock.
        lock_postgres_tables(connection, "computers", "SHARE ROW EXCLUSIVE")
        lock_postgres_tables(connection, "benchmark_results", "ACCESS EXCLUSIVE")
    op.create_table(
        "benchmark_aggregate_invalidations",
        sa.Column(
            "computer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("computers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.Uuid(as_uuid=False), primary_key=True),
    )
    with op.batch_alter_table("benchmark_results") as batch:
        batch.add_column(sa.Column("pi_equivalent", sa.Float(), nullable=True))
        batch.add_column(sa.Column("reference_machine", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("reference_run", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("aggregated_scores", sa.JSON(), nullable=True))


def downgrade():
    connection = op.get_bind()
    begin_sqlite_schema_change(connection)
    if connection.dialect.name == "postgresql":
        # Dropping the foreign key also locks computers. Reads can visit these
        # tables in either order, so release all locks if any are unavailable.
        lock_postgres_tables(
            connection,
            "benchmark_results, benchmark_aggregate_invalidations, computers",
            "ACCESS EXCLUSIVE",
        )
    names = ("pi_equivalent", "reference_machine", "reference_run", "aggregated_scores")
    results = sa.table("benchmark_results", *(sa.column(name) for name in names))
    invalidations = sa.table("benchmark_aggregate_invalidations")
    stored_pi = sa.select(1).where(sa.or_(*(column.is_not(None) for column in results.c))).exists()
    invalidated = sa.select(1).select_from(invalidations).exists()
    if connection.scalar(sa.select(sa.or_(stored_pi, invalidated))):
        raise CommandError("Cannot downgrade while benchset Pi metadata exists")
    with op.batch_alter_table("benchmark_results") as batch:
        for name in names:
            batch.drop_column(name)
    op.drop_table("benchmark_aggregate_invalidations")
