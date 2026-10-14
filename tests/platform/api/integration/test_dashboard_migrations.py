"""Dashboard migrations preserve existing users and only backfill live capacity."""

import asyncio
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import select, text, update

from throng_api.models import AvailabilityBaseline, CapacityInterval, Computer

pytestmark = pytest.mark.anyio


async def test_dashboard_downgrade_preserves_users_without_nicknames(
    dashboard_downgrade_collision, http_server, migration_database, alembic_config
):
    """Legacy schemas receive unique fallback nicknames without losing optional-name users."""
    rows = dashboard_downgrade_collision
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0003")
    async with migration_database.engine.connect() as connection:
        names = dict((await connection.execute(text("SELECT email, nickname FROM users"))).all())
        assert names["named@example.com"] == rows["existing"]["nickname"]
        assert names["unnamed@example.com"] == rows["nickname"]
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


async def test_availability_migration_backfills_only_live_owned_capacity(
    run_task,
    company_agent,
    computer,
    user,
    company,
    http_server,
    migration_database,
    alembic_config,
):
    """Existing personal and company workers retain their capacity and active reservations."""
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    database = migration_database
    # The test application clock is frozen; migration time uses the real host clock.
    expiry = datetime.now(UTC) + timedelta(hours=1)
    async with database.write() as session:
        await session.execute(update(Computer).values(lease_expires_at=expiry))
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0004")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with database.read() as session:
        baseline = await session.get(AvailabilityBaseline, 1)
        rows = (await session.scalars(select(CapacityInterval))).all()
        assert len(rows) == 2
        personal = next(row for row in rows if row.computer_id == computer["id"])
        corporate = next(row for row in rows if row.computer_id == company_agent["computer"]["id"])
        assert personal.user_id == user["id"] and personal.company_id is None
        assert corporate.company_id == company["id"] and corporate.user_id is None
        assert (personal.cpu_millis, personal.memory_bytes) == (2000, 2048)
        assert (personal.cpu_occupied_millis, personal.memory_occupied_bytes) == (1000, 1024)
        assert (corporate.cpu_occupied_millis, corporate.memory_occupied_bytes) == (0, 0)
        assert all(
            row.starts_at == baseline.available_from and row.ends_at == expiry for row in rows
        )
