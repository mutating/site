"""Backfill member names created before the membership directory existed."""

from unittest.mock import Mock
from uuid import uuid4

import anyio
import pytest
from alembic import command
from alembic.util import CommandError
from sqlalchemy import event, inspect, literal, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from throng_api.accounts import membership_page_statement, membership_search_cte
from throng_api.migrations.billing import payment_owner_schema
from throng_api.models import Membership

pytestmark = pytest.mark.anyio


def test_payment_owner_schema_uses_a_qualified_catalogue():
    """Resolve the actual PostgreSQL reference and leave SQLite's schema implicit."""
    connection = Mock(dialect=postgresql.dialect())
    owner_query = connection.execute.return_value.scalar_one_or_none
    owner_query.return_value = "legacy_schema"
    assert payment_owner_schema(connection, None, "user_id") is None
    connection.execute.assert_not_called()
    connection.exec_driver_sql.assert_not_called()
    assert payment_owner_schema(connection, 'Billing, "now"', "user_id") == "legacy_schema"
    connection.exec_driver_sql.assert_called_once_with(
        'LOCK TABLE "Billing, ""now""".payment_accounts IN ACCESS SHARE MODE NOWAIT'
    )
    assert connection.execute.call_args.args[1] == {
        "table_name": '"Billing, ""now""".payment_accounts',
        "owner_column": "user_id",
    }
    owner_query.return_value = None
    with pytest.raises(CommandError, match=r"owner table for payment_accounts\.user_id changed"):
        payment_owner_schema(connection, "public", "user_id")


@pytest.mark.parametrize(
    ("dialect", "version", "hint"),
    [
        (sqlite.dialect, (3, 34), False),
        (sqlite.dialect, (3, 35), True),
        (postgresql.dialect, (16,), False),
    ],
)
def test_member_search_materialization_preserves_sqlite_version_compatibility(
    dialect, version, hint
):
    """Reuse both CTE consumers without requiring SQLite's newer syntax on older installations."""
    db = dialect()
    db.server_version_info = version
    matches = membership_search_cte(select(Membership.id), db)
    compiled = str(select(matches.c.id).compile(dialect=db))
    assert (" AS MATERIALIZED " in compiled) is hint


@pytest.mark.parametrize(
    ("dialect", "uses_lateral"), [(sqlite.dialect(), False), (postgresql.dialect(), True)]
)
def test_member_page_fetches_public_profiles_with_supported_joins(dialect, uses_lateral):
    """Keep PostgreSQL profile reads bounded without requiring LATERAL on SQLite."""
    page = select(Membership.id).limit(200).subquery()
    counts = select(literal(1).label("total")).subquery()
    sql = str(membership_page_statement(page, counts, dialect).compile(dialect=dialect))
    assert ("LEFT OUTER JOIN LATERAL" in sql) is uses_lateral
    assert "users.avatar_version" in sql
    assert "users.password_hash" not in sql
    assert "users.avatar_data" not in sql


async def test_member_search_backfills_existing_names_and_keeps_memberships(
    alembic_config, migration_database
):
    """Keep the normalized name and the original membership across repeated migration."""
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0024")
    user, company, member = [uuid4().hex for _ in range(3)]
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, created_at, email, password_hash, first_name, last_name) "
                "VALUES (:id, CURRENT_TIMESTAMP, 'legacy@example.com', 'unused', :first, :last)"
            ),
            {
                "id": user,
                "first": "  СЕМЕ\u0308Н",
                "last": "Straße ",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO companies (id, created_at, name, created_by_user_id) "
                "VALUES (:id, CURRENT_TIMESTAMP, 'Legacy', :user)"
            ),
            {"id": company, "user": user},
        )
        await connection.execute(
            text(
                "INSERT INTO memberships (id, created_at, user_id, company_id, role) "
                "VALUES (:id, CURRENT_TIMESTAMP, :user, :company, 'creator')"
            ),
            {"id": member, "user": user, "company": company},
        )

    for _ in range(2):
        await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
        async with migration_database.engine.connect() as connection:
            assert await connection.scalar(text("SELECT name_search FROM users")) == "семён strasse"
            assert await connection.scalar(text("SELECT count(*) FROM memberships")) == 1
        await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0024")


async def test_interrupted_organization_images_upgrade_can_retry(
    migration_database, alembic_config
):
    """Roll back the first new column if the next DDL statement fails."""
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0023")

    def interrupt(_connection, _cursor, statement, *_args):
        if "ADD COLUMN background_data" in statement:
            raise RuntimeError("Interrupted image upgrade")

    event.listen(Engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="Interrupted image upgrade"):
            await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt)

    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("companies"))
        assert "avatar_data" not in {column["name"] for column in columns}
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(command.check, alembic_config)


async def test_organization_ddl_follows_the_alembic_version_schema(
    migration_database, alembic_config
):
    """A PostgreSQL search path change must not redirect columns or indexes to other tables."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite does not resolve Alembic tables through a PostgreSQL search path")
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0023")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA other_organizations")
        for table in (
            "users",
            "companies",
            "memberships",
            "computer_ownerships",
            "company_invitations",
            "computer_resource_changes",
        ):
            await connection.exec_driver_sql(
                f"CREATE TABLE other_organizations.{table} (LIKE public.{table} INCLUDING ALL)"
            )

    def move_search_path(connection, _cursor, statement, *_args):
        if statement.startswith("SELECT alembic_version.version_num"):
            connection.exec_driver_sql("SET LOCAL search_path TO other_organizations, public")

    def schema_features(connection, schema):
        db = inspect(connection)
        return (
            "name_search" in {field["name"] for field in db.get_columns("users", schema)},
            "background_data" in {field["name"] for field in db.get_columns("companies", schema)},
            "ix_memberships_company_order"
            in {index["name"] for index in db.get_indexes("memberships", schema)},
        )

    event.listen(Engine, "before_cursor_execute", move_search_path)
    try:
        for operation, revision, exists in (
            (command.upgrade, "head", True),
            (command.downgrade, "0023", False),
        ):
            await anyio.to_thread.run_sync(operation, alembic_config, revision)
            async with migration_database.engine.connect() as connection:
                assert await connection.run_sync(schema_features, "public") == (exists,) * 3
                assert (
                    await connection.run_sync(schema_features, "other_organizations")
                    == (False,) * 3
                )
    finally:
        event.remove(Engine, "before_cursor_execute", move_search_path)


async def test_organization_upgrade_locks_the_owner_schema_from_the_billing_fk(
    migration_database, alembic_config
):
    """A concurrent schema rename cannot redirect the company appearance columns."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can rename an owner's schema independently of the version table")
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0023")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA legacy_companies")
        await connection.exec_driver_sql("CREATE SCHEMA staged_companies")
        await connection.exec_driver_sql("ALTER TABLE public.companies SET SCHEMA legacy_companies")
        await connection.exec_driver_sql(
            "CREATE TABLE staged_companies.companies (LIKE legacy_companies.companies INCLUDING ALL)"
        )

    outcomes = []

    async def swap_namespaces():
        try:
            async with migration_database.engine.begin() as connection:
                await connection.exec_driver_sql("SET LOCAL lock_timeout = '150ms'")
                await connection.exec_driver_sql(
                    "ALTER SCHEMA legacy_companies RENAME TO renamed_companies"
                )
                await connection.exec_driver_sql(
                    "ALTER SCHEMA staged_companies RENAME TO legacy_companies"
                )
        except DBAPIError as error:
            outcomes.append(error.orig.sqlstate)
        else:
            outcomes.append("renamed")

    def after_owner_lookup(_connection, _cursor, statement, *_args):
        if not outcomes and "JOIN pg_catalog.pg_namespace AS owner_schema" in statement:
            anyio.from_thread.run(swap_namespaces)

    event.listen(Engine, "after_cursor_execute", after_owner_lookup)
    try:
        await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    finally:
        event.remove(Engine, "after_cursor_execute", after_owner_lookup)

    assert outcomes == ["55P03"]
    async with migration_database.engine.connect() as connection:
        schemas = await connection.scalars(
            text(
                "SELECT table_schema FROM information_schema.columns "
                "WHERE table_name = 'companies' AND column_name = 'avatar_version'"
            )
        )
        assert schemas.all() == ["legacy_companies"]


async def test_organization_upgrade_releases_locks_for_existing_readers(
    migration_database, alembic_config, migration_head
):
    """Keep a request's users-then-companies read from deadlocking with the upgrade."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL has per-table DDL locks")
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0023")
    changing_users = anyio.Event()

    def announce_users_ddl(_connection, _cursor, statement, *_args):
        if "ADD COLUMN name_search" in statement:
            anyio.from_thread.run_sync(changing_users.set)

    async def upgrade_while_users_are_in_use():
        with pytest.raises(DBAPIError) as blocked:
            await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
        assert blocked.value.orig.sqlstate == "55P03"

    event.listen(Engine, "before_cursor_execute", announce_users_ddl)
    try:
        async with anyio.create_task_group() as tasks:
            async with migration_database.engine.begin() as reader:
                await reader.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                await reader.exec_driver_sql("SET LOCAL deadlock_timeout = '150ms'")
                await reader.exec_driver_sql("SELECT id FROM users")
                tasks.start_soon(upgrade_while_users_are_in_use)
                with anyio.fail_after(10):
                    await changing_users.wait()
                await reader.exec_driver_sql("SELECT id FROM companies")
                assert (
                    await reader.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
                )
    finally:
        event.remove(Engine, "before_cursor_execute", announce_users_ddl)

    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == migration_head
        )


async def test_organization_page_indexes_match_models_after_rollback(
    alembic_config, migration_database
):
    """Replace redundant owner indexes reversibly and keep the schema aligned with the ORM."""
    page_tables = ("memberships", "computer_ownerships", "company_invitations")

    async def read_indexes():
        async with migration_database.engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: {
                    table: {index["name"] for index in inspect(sync).get_indexes(table)}
                    for table in (*page_tables, "computer_resource_changes")
                }
            )

    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0025")
    before = await read_indexes()
    for table in page_tables:
        assert f"ix_{table}_company_id" in before[table]
        assert f"ix_{table}_company_order" not in before[table]

    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    after = await read_indexes()
    for table in page_tables:
        assert f"ix_{table}_company_order" in after[table]
        assert f"ix_{table}_company_id" not in after[table]
    assert after["computer_resource_changes"] - before["computer_resource_changes"] == {
        "ix_inventory_history_before_company_time",
        "ix_inventory_history_after_company_time",
    }
    await anyio.to_thread.run_sync(command.check, alembic_config)
