"""Upgrade preserves legacy credentials and never infers purpose from a token's name."""

import asyncio

import anyio
import pytest
from alembic import command
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def test_token_purpose_upgrade_and_downgrade_preserve_credentials(
    api, user, access_token, http_server, migration_database, alembic_config
):
    await api.patch(f"/v1/access-tokens/{access_token['id']}", json={"name": "login"})
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    async with migration_database.engine.connect() as connection:
        before = (
            await connection.execute(
                text("SELECT id, token_hash, expires_at FROM access_tokens ORDER BY id")
            )
        ).all()
    await migration_database.close()
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0013")
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(command.check, alembic_config)
    async with migration_database.engine.connect() as connection:
        after = (
            await connection.execute(
                text("SELECT id, token_hash, expires_at FROM access_tokens ORDER BY id")
            )
        ).all()
        purposes = (
            (await connection.execute(text("SELECT purpose FROM access_tokens")))
            .scalars()
            .all()
        )
    assert before == after
    assert purposes == ["legacy", "legacy"]
