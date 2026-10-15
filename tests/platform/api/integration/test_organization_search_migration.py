"""Names written before search existed remain searchable after a real migration."""

from uuid import uuid4

import anyio
import pytest
from alembic import command
from sqlalchemy import text
from throng_api.organization_names import name_key, normalize_name

pytestmark = pytest.mark.anyio


async def test_search_migration_backfills_unicode_names_and_preserves_references(
    alembic_config, migration_database
):
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0008")
    user_id, company_id, member_id = [uuid4().hex for _ in range(3)]
    name = "ЛАБ E\u0301 Straße 02"
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, created_at, email, password_hash, first_name, last_name, is_system_admin) VALUES (:id, CURRENT_TIMESTAMP, 'legacy@example.com', 'unused', '', '', false)"
            ),
            {"id": user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO companies (id, created_at, name, created_by_user_id) VALUES (:id, CURRENT_TIMESTAMP, :name, :user)"
            ),
            {"id": company_id, "name": name, "user": user_id},
        )
        await connection.execute(
            text(
                "INSERT INTO memberships (id, created_at, user_id, company_id, role) VALUES (:id, CURRENT_TIMESTAMP, :user, :company, 'creator')"
            ),
            {"id": member_id, "user": user_id, "company": company_id},
        )
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    async with migration_database.engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT name, name_search, name_key FROM companies")
            )
        ).one()
        assert tuple(row) == (name, normalize_name(name), name_key(name))
        assert await connection.scalar(text("SELECT count(*) FROM memberships")) == 1
    # Repeated deployment is idempotent; downgrade removes only the new columns.
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0008")
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT name_search FROM companies")
        ) == normalize_name(name)
