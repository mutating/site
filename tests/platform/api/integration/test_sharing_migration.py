"""Sharing migration preserves old memberships and rolls back without deleting members."""

import asyncio

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def test_sharing_migration_preserves_memberships_and_defaults(
    api, company, membership, http_server, migration_database, alembic_config
):
    """Existing roles survive downgrade/upgrade and inherit defaults from a nullable permission override."""
    assert (
        await api.patch(f"/api/v1/memberships/{membership['id']}", json={"permissions": []})
    ).status_code == 200
    assert (
        await api.post("/api/v1/invitations", json={"company_id": company["id"]})
    ).status_code == 201
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0006")
    async with migration_database.engine.connect() as connection:
        rows = (await connection.execute(text("SELECT id, role FROM memberships"))).all()
        roles = {str(identifier).replace("-", ""): role for identifier, role in rows}
        assert len(rows) == 2 and roles[membership["id"].replace("-", "")] == "user"
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT permissions FROM memberships"))
        ).scalars().all() == [None, None]
        assert (
            await connection.execute(text("SELECT COUNT(*) FROM company_invitations"))
        ).scalar() == 0
