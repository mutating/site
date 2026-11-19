"""Run-history migration preserves populated databases and refuses lossy rollback."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import anyio
import pytest
from alembic import command as alembic
from alembic.util import CommandError
from sqlalchemy import (
    DateTime,
    Engine,
    Float,
    String,
    Uuid,
    column,
    event,
    inspect,
    select,
    table,
    text,
)
from sqlalchemy.exc import DBAPIError

from throng_api.models import BenchmarkAggregateInvalidation, BenchmarkResult
from throng_api.serialization import public

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def pause_migration(operation, alembic_config, target, event_name, sql_fragment):
    """Hold migration 0019 at a SQL statement while another connection writes."""
    loop = asyncio.get_running_loop()
    reached = asyncio.Event()
    proceed = Event()

    def pause(connection, cursor, statement, parameters, context, executemany):
        if sql_fragment in statement:
            loop.call_soon_threadsafe(reached.set)
            if not proceed.wait(timeout=10):
                raise RuntimeError("concurrent write did not finish")

    event.listen(Engine, event_name, pause)
    migration = asyncio.create_task(anyio.to_thread.run_sync(operation, alembic_config, target))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        yield
    finally:
        proceed.set()
        try:
            await asyncio.wait_for(migration, timeout=10)
        finally:
            event.remove(Engine, event_name, pause)


async def set_postgres_database_default(migration_database, setting):
    """Make new connections to this disposable database use the requested default."""
    async with migration_database.engine.begin() as connection:
        database = await connection.scalar(text("SELECT current_database()"))
        name = connection.dialect.identifier_preparer.quote_identifier(database)
        await connection.exec_driver_sql(f"ALTER DATABASE {name} SET {setting}")
    await migration_database.close()


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
                "pi_equivalent": None,
                "reference_machine": None,
                "reference_run": None,
                "aggregated_scores": None,
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
    # SQLite commits the safe downgrade to 0018 before reaching its run-history guard.
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
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


@pytest.mark.parametrize("deleted", [False, True])
async def test_downgrade_preserves_saved_pi_metadata(
    api, computer, http_server, migration_database, alembic_config, deleted
):
    """Rolling back 0019 preserves a saved score or a run's invalidation after deletion."""
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": "single_core",
            "benchmarker_version": "0.0.9",
            "number": 0,
            "pi_equivalent": 0,
        },
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    if deleted:
        assert (await api.delete(f"/v1/benchmark-results/{saved['id']}")).status_code == 204
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    with pytest.raises(CommandError, match="benchset Pi metadata"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0018")
    async with migration_database.read() as session:
        result = await session.get(BenchmarkResult, saved["id"])
        if deleted:
            assert result is None
            assert await session.get(
                BenchmarkAggregateInvalidation, (computer["id"], saved["run_id"])
            )
        else:
            assert result.pi_equivalent == 0.0


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
async def test_pi_schema_change_can_be_retried_after_interruption(
    migration_database, alembic_config, direction
):
    """Fail after DDL but before Alembic stamps 0019 or 0018, then retry the migration."""
    run = getattr(alembic, direction)
    initial, target = ("0018", "0019") if direction == "upgrade" else ("0019", "0018")
    if direction == "upgrade":
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, initial)

    def fail_before_stamp(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE alembic_version"):
            raise RuntimeError("migration interrupted before updating revision")

    event.listen(Engine, "before_cursor_execute", fail_before_stamp)
    try:
        with pytest.raises(RuntimeError, match="migration interrupted"):
            await anyio.to_thread.run_sync(run, alembic_config, target)
    finally:
        event.remove(Engine, "before_cursor_execute", fail_before_stamp)

    def pi_schema_presence(connection):
        inspector = inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("benchmark_results")}
        return "pi_equivalent" in columns, inspector.has_table("benchmark_aggregate_invalidations")

    async def assert_schema(revision):
        has_pi_metadata = revision == "0019"
        async with migration_database.engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == revision
            )
            assert await connection.run_sync(pi_schema_presence) == (
                has_pi_metadata,
                has_pi_metadata,
            )

    await assert_schema(initial)
    await migration_database.close()
    await anyio.to_thread.run_sync(run, alembic_config, target)
    await assert_schema(target)


@pytest.mark.parametrize("write", ["score", "invalidation"])
async def test_downgrade_locks_writers_before_checking_pi_metadata(
    api, computer, http_server, migration_database, alembic_config, write
):
    """An overlapping writer cannot save a score or invalidation after the downgrade's data check."""
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": "single_core",
            "benchmarker_version": "0.0.9",
            "number": 10,
        },
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)

    if write == "score":
        statement = (
            BenchmarkResult.__table__.update()
            .where(BenchmarkResult.id == saved["id"])
            .values(pi_equivalent=0.5)
        )
    else:
        statement = BenchmarkAggregateInvalidation.__table__.insert().values(
            computer_id=computer["id"], run_id=saved["run_id"]
        )

    async with pause_migration(
        alembic.downgrade,
        alembic_config,
        "0018",
        "after_cursor_execute",
        "WHERE benchmark_results.pi_equivalent IS NOT NULL",
    ):
        async with migration_database.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("PRAGMA busy_timeout = 0")
            else:
                await connection.exec_driver_sql("SET LOCAL lock_timeout = '100ms'")
            with pytest.raises(DBAPIError, match="database is locked|lock timeout"):
                await connection.execute(statement)


async def test_postgres_downgrade_sees_metadata_committed_before_the_table_lock(
    api, computer, http_server, migration_database, alembic_config
):
    """A PostgreSQL database default of repeatable read cannot hide a committed Pi score."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction isolation")
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": "single_core",
            "benchmarker_version": "0.0.9",
            "number": 10,
        },
    )
    assert response.status_code == 201, response.text
    result_id = response.json()["id"]
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await set_postgres_database_default(
        migration_database, "default_transaction_isolation TO 'repeatable read'"
    )

    with pytest.raises(CommandError, match="benchset Pi metadata"):
        async with pause_migration(
            alembic.downgrade,
            alembic_config,
            "0018",
            "before_cursor_execute",
            "LOCK TABLE benchmark_results",
        ):
            async with migration_database.engine.begin() as connection:
                assert (
                    await connection.scalar(text("SHOW transaction_isolation")) == "repeatable read"
                )
                await connection.execute(
                    BenchmarkResult.__table__.update()
                    .where(BenchmarkResult.id == result_id)
                    .values(pi_equivalent=0.5)
                )
    async with migration_database.read() as session:
        result = await session.get(BenchmarkResult, result_id)
        assert result.pi_equivalent == 0.5


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_postgres_upgrade_allows_computer_deletion_before_migration_locks(
    api, computer, migration_database, alembic_config
):
    """The migration takes its computer lock before locking benchmark results."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL foreign key locks")
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": "single_core",
            "benchmarker_version": "0.0.2",
            "number": 10,
        },
    )
    assert response.status_code == 201, response.text
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0018")

    async with pause_migration(
        alembic.upgrade,
        alembic_config,
        "0019",
        "before_cursor_execute",
        "LOCK TABLE computers IN SHARE ROW EXCLUSIVE MODE NOWAIT",
    ):
        deleted = await api.delete(f"/v1/computers/{computer['id']}", timeout=4)
        assert deleted.status_code == 204, deleted.text
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_postgres_upgrade_from_0017_releases_earlier_schema_locks_on_conflict(
    api, computer, migration_database, alembic_config
):
    """A computer writer survives when 0018 has already locked results in the same upgrade."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL foreign key locks")
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0017")
    await set_postgres_database_default(migration_database, "lock_timeout TO '500ms'")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("LOCK TABLE computers IN ROW EXCLUSIVE MODE")
        with pytest.raises(CommandError, match="benchmark tables are in use"):
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0017"
        deleted = await api.delete(f"/v1/computers/{computer['id']}")
        assert deleted.status_code == 204, deleted.text

    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_postgres_downgrade_releases_partial_locks_on_conflict(
    api, computer, migration_database, alembic_config
):
    """The downgrade yields to a concurrent computer writer and can be retried afterward."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL foreign key locks")
    await set_postgres_database_default(migration_database, "lock_timeout TO '500ms'")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("LOCK TABLE computers IN ROW EXCLUSIVE MODE")
        with pytest.raises(CommandError, match="benchmark tables are in use"):
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0018")
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"
        deleted = await api.delete(f"/v1/computers/{computer['id']}")
        assert deleted.status_code == 204, deleted.text

    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0018")
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0018"
