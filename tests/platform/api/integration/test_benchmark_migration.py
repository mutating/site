"""The versioned results migration upgrades existing workers without changing them."""

import asyncio
from uuid import uuid4

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio


async def test_upgrade_downgrade_and_metadata_alignment(
    computer, http_server, migration_database, alembic_config
):
    """The migration can be reversed and reapplied without altering existing computers, and its schema agrees with the SQLAlchemy models."""
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0007")
    async with migration_database.engine.connect() as connection:
        assert "benchmark_results" not in await connection.run_sync(
            lambda sync: inspect(sync).get_table_names()
        )
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT name FROM computers")) == computer["name"]
        assert await connection.scalar(text("SELECT count(*) FROM benchmark_results")) == 0


async def test_database_enforces_identity_score_and_foreign_key(app, computer):
    """Database constraints independently reject duplicate benchmark identities, negative scores, and references to nonexistent computers."""
    from throng_api.models import BenchmarkResult

    def row(**changes):
        return BenchmarkResult(
            **{
                "computer_id": computer["id"],
                "benchmark_name": "single_core",
                "number": 1,
                "benchmarker_version": "0.0.2",
                **changes,
            }
        )

    async with app.state.db.write() as session:
        session.add(row())
    for changes in [
        {},
        {"number": -1, "benchmarker_version": "0.0.3"},
        {"computer_id": str(uuid4())},
    ]:
        with pytest.raises(IntegrityError):
            async with app.state.db.write() as session:
                session.add(row(**changes))
                await session.flush()
