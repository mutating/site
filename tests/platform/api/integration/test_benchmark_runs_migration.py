"""Run-history migration preserves populated databases and refuses lossy rollback."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest
from alembic import command as alembic
from alembic.util import CommandError
from sqlalchemy import DateTime, Float, String, Uuid, column, inspect, select, table

from throng_api.models import BenchmarkResult
from throng_api.serialization import public

pytestmark = pytest.mark.anyio


async def test_existing_results_survive_run_migration(
    computer, http_server, migration_database, alembic_config
):
    """Existing identities, scores and dates survive; rollback cannot discard added runs."""
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0017")
    async with migration_database.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns("benchmark_results")
        )
        assert "run_id" not in {column["name"] for column in columns}
    # Seed the actual old schema: no modern run identity exists to discard.
    legacy = table(
        "benchmark_results",
        column("id", Uuid(as_uuid=False)),
        column("computer_id", Uuid(as_uuid=False)),
        column("created_at", DateTime(timezone=True)),
        column("benchmark_name", String(200)),
        column("benchmarker_version", String(200)),
        column("number", Float),
    )
    saved = [
        {
            "id": str(uuid4()),
            "computer_id": computer["id"],
            "created_at": datetime(2026, 10, 7, 12, tzinfo=UTC),
            "benchmark_name": name,
            "benchmarker_version": version,
            "number": 12.5,
        }
        for name, version in [
            ("single_core", "0.0.1"),
            ("single_core", "0.0.2"),
            ("multi_core", "0.0.2"),
        ]
    ]
    async with migration_database.engine.begin() as connection:
        await connection.execute(legacy.insert(), saved)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.read() as session:
        for old in saved:
            row = public(await session.get(BenchmarkResult, old["id"]))
            assert row == {
                **old,
                "run_id": old["id"],
            }
    # Same version/name now accepts additional runs without changing old rows.
    async with migration_database.write() as session:
        session.add(
            BenchmarkResult(
                computer_id=computer["id"],
                benchmark_name="single_core",
                benchmarker_version="0.0.2",
                number=20,
            )
        )
    async with migration_database.read() as session:
        assert len(list(await session.scalars(select(BenchmarkResult)))) == 4
    await migration_database.close()
    with pytest.raises(CommandError, match="repeated benchmark runs"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0017")
    async with migration_database.read() as session:
        assert len(list(await session.scalars(select(BenchmarkResult)))) == 4


@pytest.mark.parametrize("count", [1, 2])
async def test_downgrade_cannot_discard_run_identity(
    api, computer, http_server, migration_database, alembic_config, count
):
    """Even a single modern run cannot be rolled back into a schema that loses its ID."""
    run_id = str(uuid4())
    saved = []
    for name in ["single_core", "multi_core"][:count]:
        response = await api.post(
            "/v1/benchmark-results",
            json={
                "computer_id": computer["id"],
                "benchmark_name": name,
                "benchmarker_version": "0.0.2",
                "number": 10,
                "run_id": run_id,
            },
        )
        assert response.status_code == 201, response.text
        saved.append(response.json())
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    with pytest.raises(CommandError, match="run identifiers"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0017")
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    async with migration_database.read() as session:
        rows = list(await session.scalars(select(BenchmarkResult)))
        assert {row.id for row in rows} == {row["id"] for row in saved}
        assert {row.run_id for row in rows} == {run_id}
