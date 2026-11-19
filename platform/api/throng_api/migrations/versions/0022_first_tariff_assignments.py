"""Assign the first plan of each tariff type to existing accounts.

Revision ID: 0022
Revises: 0021
"""

from contextlib import contextmanager

from alembic import op

from throng_api.first_tariff_schema import (
    CREATE_FIRST_TARIFF_TRIGGERS,
    DROP_FIRST_TARIFF_TRIGGERS,
    LOCK_CATALOGUE_GUARD_TRANSITION,
    POSTGRES_ACCOUNT_DEFAULT_TRIGGER,
    POSTGRES_CREATE_FIRST_TARIFF_OBJECTS,
    POSTGRES_DROP_FIRST_TARIFF_OBJECTS,
    backfill_first_tariffs,
    postgres_catalog_ddl,
    postgres_catalogue_lock_statements,
    version_coordination,
)
from throng_api.migrations.billing import billing_migration_schema

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


@contextmanager
def locked_schema(connection):
    with billing_migration_schema(connection, op.get_context()) as (name, schema):
        if schema:
            for statement in postgres_catalogue_lock_statements(schema):
                connection.exec_driver_sql(statement)
        yield name, schema


def upgrade():
    connection = op.get_bind()
    with locked_schema(connection) as (name, schema):
        if schema:
            # Reject older snapshots even if the competing write predates these triggers.
            connection.exec_driver_sql(version_coordination(schema))
            connection.exec_driver_sql(
                postgres_catalog_ddl(
                    *POSTGRES_CREATE_FIRST_TARIFF_OBJECTS,
                    before=LOCK_CATALOGUE_GUARD_TRANSITION,
                    schema=name,
                )
            )
        else:
            for ddl in CREATE_FIRST_TARIFF_TRIGGERS["sqlite"]:
                connection.exec_driver_sql(ddl)
        connection.exec_driver_sql(backfill_first_tariffs(schema))


def downgrade():
    connection = op.get_bind()
    with locked_schema(connection) as (name, schema):
        if schema:
            connection.exec_driver_sql(
                postgres_catalog_ddl(
                    *POSTGRES_DROP_FIRST_TARIFF_OBJECTS,
                    POSTGRES_ACCOUNT_DEFAULT_TRIGGER,
                    before=LOCK_CATALOGUE_GUARD_TRANSITION,
                    schema=name,
                )
            )
        else:
            for ddl in DROP_FIRST_TARIFF_TRIGGERS["sqlite"]:
                connection.exec_driver_sql(ddl)
