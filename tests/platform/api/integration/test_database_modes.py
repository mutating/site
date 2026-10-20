"""Alternative database configuration and explicitly supported migration entry points."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from alembic import command as alembic
from alembic.script import ScriptDirectory
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from throng_api.cli import migrate
from throng_api.models import ComputerResource, Coordination, ResourceType

pytestmark = pytest.mark.anyio
ALEMBIC_INI = str(Path(__file__).resolve().parents[4] / "platform/api/alembic.ini")


async def test_memory_database_keeps_foreign_keys_and_transactional_coordination(memory_database):
    """In-memory SQLite enforces the same foreign keys and serialized commit/rollback semantics without a filesystem database."""
    database = memory_database
    await database.check()
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert await connection.scalar(text("PRAGMA busy_timeout")) == 10000
        assert await connection.scalar(text("PRAGMA journal_mode")) == "memory"
    with pytest.raises(IntegrityError):
        async with database.write() as session:
            session.add(
                ComputerResource(computer_id=str(uuid4()), resource_type_id=str(uuid4()), total=1)
            )
            await session.flush()
    async with database.read() as session:
        assert await session.scalar(select(Coordination.revision)) == 0
    async with database.write() as session:
        kind = ResourceType(name="GPU", code="gpu", unit="device")
        session.add(kind)
    async with database.read() as session:
        assert await session.scalar(select(Coordination.revision)) == 1
        assert (await session.get(ResourceType, kind.id)).code == "gpu"


async def test_postgres_configuration_preserves_native_uuid_and_bigint_sql(postgres_configuration):
    """The asyncpg configuration produces PostgreSQL UUID binds and row-locking updates without assuming SQLite-specific SQL or directories."""
    database = postgres_configuration
    assert database.engine.dialect.name == "postgresql"
    assert database.engine.dialect.driver == "asyncpg"
    assert database.engine.sync_engine.hide_parameters is True
    table_sql = str(
        CreateTable(ComputerResource.__table__).compile(bind=database.engine.sync_engine)
    )
    assert "total BIGINT NOT NULL" in table_sql and "occupied BIGINT NOT NULL" in table_sql
    assert "resource_type_id UUID NOT NULL" in table_sql
    async with database.read() as session:
        query = select(ComputerResource).where(ComputerResource.resource_type_id == str(uuid4()))
        sql = str(query.compile(bind=session.bind.sync_engine))
        assert "::UUID" in sql
        lock = (
            update(Coordination)
            .where(Coordination.id == 1)
            .values(revision=Coordination.revision + 1)
        )
        assert "UPDATE coordination SET revision=" in str(
            lock.compile(bind=session.bind.sync_engine)
        )
        assert database.engine.pool.checkedout() == 0


async def test_memory_migration_builds_full_schema_without_treating_url_as_file(
    memory_migration_probe,
):
    """Migrations can build an ephemeral SQLite schema with both resource types, without treating :memory: as a directory or database file."""
    await anyio.to_thread.run_sync(migrate, memory_migration_probe["settings"])
    assert memory_migration_probe["revision"] == "0006"
    assert memory_migration_probe["types"] == [("cpu", "millicore"), ("memory", "byte")]
    assert {
        "computer_resources",
        "computer_resource_changes",
        "resource_types",
    } <= memory_migration_probe["tables"]
    assert (
        not {"cpu_millis", "reserved_cpu", "memory_bytes", "reserved_memory"}
        & memory_migration_probe["columns"]
    )


async def test_alembic_cli_reads_environment_database_url(
    cli_environment, migration_database, alembic_config
):
    """The plain Alembic entry point resolves its omitted URL from THRONG_DATABASE_URL and migrates the intended database."""
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0001")
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        ALEMBIC_INI,
        "upgrade",
        "head",
        env=cli_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0, (stdout.decode(), stderr.decode())
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            ScriptDirectory.from_config(alembic_config).get_current_head()
        )


async def test_offline_migration_fails_before_emitting_partial_sql(
    cli_environment, migration_database
):
    """Unsupported offline generation fails before any DDL is printed, rather than emitting a misleading partial migration script."""
    async with migration_database.engine.connect() as connection:
        before = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        ALEMBIC_INI,
        "upgrade",
        "head",
        "--sql",
        env=cli_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode != 0
    output = stdout.decode() + stderr.decode()
    assert "require a live database" in output and "--sql is not supported" in output
    assert (
        "CREATE TABLE" not in output and "ALTER TABLE" not in output and "Traceback" not in output
    )
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == before
