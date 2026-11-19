"""Backfill member names created before the membership directory existed."""

from uuid import uuid4

import anyio
import pytest
from alembic import command
from sqlalchemy import inspect, literal, select, text
from sqlalchemy.dialects import postgresql, sqlite

from throng_api.accounts import membership_page_statement, membership_search_cte
from throng_api.models import Membership

pytestmark = pytest.mark.anyio


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
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0019")
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
        await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0019")


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

    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0020")
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
