"""Transaction and schema selection for the billing revisions."""

from contextlib import contextmanager

from alembic.util import CommandError
from sqlalchemy import Table, cast, func, text
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from throng_api.tariff_documentation_schema import postgres_string

SET_LOCAL_SEARCH_PATH = text("SELECT pg_catalog.set_config('search_path', :path, true)")
SET_LOCAL_LOCK_TIMEOUT = text("SELECT pg_catalog.set_config('lock_timeout', :timeout, true)")
BILLING_VERSION_RELATION = "billing_migration_version_relation"
BILLING_VERSION_TABLE = "billing_migration_version_table"
BILLING_VERSION_SCHEMA = "billing_migration_version_schema"
LOCK_VERSION_SCHEMA = """
    WITH version AS MATERIALIZED (
        SELECT n.nspname, c.oid AS table_oid, n.oid AS schema_oid
        FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.oid = pg_catalog.to_regclass({relation})
    )
    SELECT version.nspname, version.table_oid, version.schema_oid, locked.objid
    FROM version
    CROSS JOIN LATERAL pg_catalog.pg_get_object_address(
        'schema', ARRAY[version.nspname::text], ARRAY[]::text[]
    ) AS locked
"""
REFERENCED_OWNER_SCHEMA = text("""
    WITH owner AS MATERIALIZED (
        SELECT owner_schema.nspname AS schema_name,
               owner_schema.oid AS schema_oid,
               owner_table.relname AS table_name,
               owner_table.oid AS table_oid
        FROM pg_catalog.pg_constraint AS fk
        JOIN pg_catalog.pg_attribute AS owner_key ON owner_key.attrelid = fk.conrelid
        JOIN pg_catalog.pg_class AS owner_table ON owner_table.oid = fk.confrelid
        JOIN pg_catalog.pg_namespace AS owner_schema ON owner_schema.oid = owner_table.relnamespace
        WHERE fk.conrelid = pg_catalog.to_regclass(:table_name)
          AND fk.contype = 'f'
          AND fk.conkey = ARRAY[owner_key.attnum]
          AND owner_key.attname = :owner_column
    )
    SELECT owner.schema_name
    FROM owner
    CROSS JOIN LATERAL pg_catalog.pg_get_object_address(
        'schema', ARRAY[owner.schema_name::text], ARRAY[]::text[]
    ) AS locked_schema
    CROSS JOIN LATERAL pg_catalog.pg_get_object_address(
        'table', ARRAY[owner.schema_name::text, owner.table_name::text], ARRAY[]::text[]
    ) AS locked_table
    WHERE locked_schema.objid = owner.schema_oid AND locked_table.objid = owner.table_oid
""")


def checked_version_schema(location):
    if location is None or location[2] != location[3]:
        raise CommandError("Billing schema changed while Alembic was selecting its version table")
    return location[:2]


def lock_schema_for_version_read(cursor, _sql, _parameters, execution):
    compiled = execution.compiled
    if compiled is None or not isinstance(compiled.statement, Select):
        return
    info = execution.root_connection.info
    if BILLING_VERSION_SCHEMA in info:
        return
    tables = compiled.statement.get_final_froms()
    if (
        len(tables) != 1
        or not isinstance(tables[0], Table)
        or (tables[0].schema, tables[0].name) != info[BILLING_VERSION_TABLE]
    ):
        return
    # Run after cursor listeners set the effective search_path and role for the version read.
    relation = postgres_string(compiled.preparer.format_table(tables[0]))
    cursor.execute(LOCK_VERSION_SCHEMA.format(relation=relation))
    info[BILLING_VERSION_SCHEMA] = checked_version_schema(cursor.fetchone())


def constrain_billing_version_update(connection, statement, multiparams, params, _options):
    target = connection.info.get(BILLING_VERSION_RELATION)
    if (
        target is not None
        and isinstance(statement, Update)
        and (statement.table.schema, statement.table.name) == target[:2]
    ):
        # Alembic requires one updated row. The schema lock keeps the name bound to its OID.
        relation = connection.dialect.identifier_preparer.format_table(statement.table)
        statement = statement.where(cast(func.pg_catalog.to_regclass(relation), OID) == target[2])
        connection.info.pop(BILLING_VERSION_RELATION)
    return statement, multiparams, params


def referenced_owner_schema(connection, source_schema, table_name, owner_column):
    """Resolve and lock the actual target of a qualified table's existing owner key."""
    if source_schema is None:
        return None
    preparer = connection.dialect.identifier_preparer
    catalogue = f"{preparer.quote_schema(source_schema)}.{preparer.quote(table_name)}"
    # Keep the foreign key stable while locking the objects it refers to.
    connection.exec_driver_sql(f"LOCK TABLE {catalogue} IN ACCESS SHARE MODE NOWAIT")
    schema = connection.execute(
        REFERENCED_OWNER_SCHEMA,
        {"table_name": catalogue, "owner_column": owner_column},
    ).scalar_one_or_none()
    if schema is None:
        raise CommandError(
            f"The owner table for {table_name}.{owner_column} changed during migration"
        )
    return schema


def payment_owner_schema(connection, billing_schema, owner_column):
    return referenced_owner_schema(connection, billing_schema, "payment_accounts", owner_column)


@contextmanager
def billing_migration_schema(connection, context, *, lock_timeout=None):
    if connection.dialect.name == "sqlite":
        # SQLite's legacy driver starts transactions for DML, but not for DDL.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        yield None, ""
        return

    # Use the schema whose Alembic revision is being updated.
    preparer = connection.dialect.identifier_preparer
    version_table = preparer.quote_identifier(context.version_table)
    if context.version_table_schema is not None:
        version_table = preparer.quote_schema(context.version_table_schema) + "." + version_table
    locked = connection.info.get(BILLING_VERSION_SCHEMA)
    if locked is None:
        locked = checked_version_schema(
            connection.execute(
                text(LOCK_VERSION_SCHEMA.format(relation=":relation")), {"relation": version_table}
            ).one_or_none()
        )
        connection.info[BILLING_VERSION_SCHEMA] = locked
    name, oid = locked
    connection.info[BILLING_VERSION_RELATION] = (
        context.version_table_schema,
        context.version_table,
        oid,
    )
    quoted = preparer.quote_schema(name)
    previous_path = connection.exec_driver_sql("SHOW search_path").scalar_one()
    previous_row_security = connection.exec_driver_sql("SHOW row_security").scalar_one()
    # PostgreSQL searches pg_catalog implicitly and creates unqualified tables in this schema.
    connection.execute(SET_LOCAL_SEARCH_PATH, {"path": f"{quoted}, pg_temp"})
    # A migration must see every row it checks, copies or backfills.
    connection.exec_driver_sql("SET LOCAL row_security = off")
    if lock_timeout is not None:
        previous_lock_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        connection.execute(SET_LOCAL_LOCK_TIMEOUT, {"timeout": lock_timeout})
    yield name, quoted + "."
    if lock_timeout is not None:
        connection.execute(SET_LOCAL_LOCK_TIMEOUT, {"timeout": previous_lock_timeout})
    connection.execute(SET_LOCAL_SEARCH_PATH, {"path": previous_path})
    connection.execute(
        text("SELECT pg_catalog.set_config('row_security', :previous, true)"),
        {"previous": previous_row_security},
    )
