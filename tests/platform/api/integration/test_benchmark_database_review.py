"""Persistence, transactional failures and populated benchmark migration regressions."""

import asyncio
import sys
from uuid import UUID

import anyio
import pytest
from alembic import command as alembic
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, inspect, select
from sqlalchemy.schema import CreateTable

from throng_api.app import create_app
from throng_api.models import BenchmarkResult, Computer, ComputerOwnership, ComputerResource
from throng_api.serialization import public

pytestmark = pytest.mark.anyio


async def create_result(api, computer, *, name="single_core", version="0.0.2", number=12.5):
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": name,
            "benchmarker_version": version,
            "number": number,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_older_version_upsert_retains_identity_after_application_restart(
    api, computer, http_server, settings, clock, user
):
    """After API restart, overwriting an older version keeps its row identity and does not change which newer version is listed."""
    older = await create_result(api, computer, version="0.0.2")
    newer = await create_result(api, computer, version="0.0.3", number=90)
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)

    application = create_app(
        settings.model_copy(update={"controller_enabled": False}), clock=lambda: clock.now
    )
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://restarted.test",
            headers=user["headers"],
        ) as restarted:
            assert (await restarted.get(f"/v1/benchmark-results/{older['id']}")).json() == older
            updated = await create_result(restarted, computer, version="0.0.2", number=7)
            assert updated == {**older, "number": 7}
            listing = f"/v1/computers/{computer['id']}/benchmark-results"
            assert (await restarted.get(listing)).json() == [newer]
            assert (await restarted.get(listing, params={"latest_only": False})).json() == [
                updated,
                newer,
            ]


async def test_mid_suite_database_failure_rolls_back_flushed_updates_and_inserts(
    api, agent_api, app, computer
):
    """A storage failure after partial flushing rolls back every update and insert from the failed upload."""
    existing = await create_result(api, computer)
    untouched = await create_result(api, computer, version="0.0.1", number=2)
    payload = {
        "benchmarker_version": "0.0.2",
        "results": [
            {"benchmark_name": "single_core", "number": 99},
            {"benchmark_name": "new_core", "number": 100},
            {"benchmark_name": "failing_core", "number": 101},
        ],
    }
    attempted = []

    def fail_third_write(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("INSERT INTO benchmark_results", "UPDATE benchmark_results")):
            attempted.append(statement)
            if len(attempted) == 3:
                raise RuntimeError("benchmark storage interrupted after two flushed writes")

    engine = app.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", fail_third_write)
    try:
        response = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    finally:
        event.remove(engine, "before_cursor_execute", fail_third_write)
    assert response.status_code == 500
    assert len(attempted) == 3
    assert attempted[0].startswith("UPDATE") and attempted[1].startswith("INSERT")
    listing = f"/v1/computers/{computer['id']}/benchmark-results"
    assert (await api.get(listing, params={"latest_only": False})).json() == [untouched, existing]
    async with app.state.db.read() as session:
        stored = list(await session.scalars(select(BenchmarkResult)))
        assert {row.id for row in stored} == {existing["id"], untouched["id"]}

    retry = await agent_api.put("/agent/v1/benchmark-results", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()[0] == {**existing, "number": 99}
    assert len((await api.get(listing, params={"latest_only": False})).json()) == 4


@pytest.mark.parametrize("number", [0.0, 5e-324, 0.1, 1.0000000000000002, sys.float_info.max])
async def test_uuid_and_finite_double_values_round_trip_through_new_database_connection(
    api, computer, migration_database, number
):
    """Fresh database connections retrieve the same UUID identities and finite double values written by the API."""
    created = await create_result(api, computer, number=number)
    assert str(UUID(created["id"])) == created["id"]
    assert str(UUID(created["computer_id"])) == computer["id"]
    async with migration_database.read() as session:
        stored = await session.get(BenchmarkResult, created["id"])
        assert stored.number == number
        assert stored.computer_id == computer["id"]
        assert stored.created_at.tzinfo is not None
    response = await api.get(f"/v1/benchmark-results/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == created


async def test_populated_downgrade_reupgrade_preserves_workers_and_recreates_usable_table(
    api, computer, http_server, migration_database, alembic_config
):
    """Downgrading removes benchmark history without altering workers; re-upgrading recreates a usable table aligned with model metadata."""
    removed = await create_result(api, computer)
    await create_result(api, computer, name="multi_core", number=50)
    async with migration_database.read() as session:
        worker_before = public(await session.get(Computer, computer["id"]))
        resources_before = [
            public(row)
            for row in await session.scalars(
                select(ComputerResource)
                .where(ComputerResource.computer_id == computer["id"])
                .order_by(ComputerResource.id)
            )
        ]
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
    async with migration_database.write() as session:
        assert await session.scalar(select(BenchmarkResult.id)) is None
        replacement = BenchmarkResult(
            computer_id=computer["id"],
            benchmark_name=removed["benchmark_name"],
            benchmarker_version=removed["benchmarker_version"],
            number=0.125,
        )
        session.add(replacement)
        await session.flush()
        replacement_before = public(replacement)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with migration_database.read() as session:
        assert public(await session.get(Computer, computer["id"])) == worker_before
        assert [
            public(row)
            for row in await session.scalars(
                select(ComputerResource)
                .where(ComputerResource.computer_id == computer["id"])
                .order_by(ComputerResource.id)
            )
        ] == resources_before
        assert public(await session.get(BenchmarkResult, replacement.id)) == replacement_before
        assert await session.get(BenchmarkResult, removed["id"]) is None


async def test_company_deletion_and_ownership_detach_preserve_computer_results(
    api, app, computer, company, user
):
    """Changing or removing ownership preserves the computer's benchmark history, which becomes accessible again to a later valid owner."""
    result = await create_result(api, computer)
    response = await api.patch(
        f"/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    response = await api.delete(f"/v1/companies/{company['id']}")
    assert response.status_code == 409, response.text
    assert (await api.get(f"/v1/benchmark-results/{result['id']}")).json() == result
    response = await api.delete(f"/v1/computer-ownerships/{computer['ownership']['id']}")
    assert response.status_code == 204, response.text
    response = await api.delete(f"/v1/companies/{company['id']}")
    assert response.status_code == 204, response.text
    async with app.state.db.read() as session:
        assert await session.get(ComputerOwnership, computer["ownership"]["id"]) is None
        assert (await session.get(BenchmarkResult, result["id"])).computer_id == computer["id"]
    response = await api.post(
        "/v1/computer-ownerships", json={"computer_id": computer["id"], "user_id": user["id"]}
    )
    assert response.status_code == 201, response.text
    assert (await api.get(f"/v1/benchmark-results/{result['id']}")).json() == result


async def test_bulk_computer_delete_cascade_rolls_back_and_is_limited_to_that_computer(
    api, app, computer, other_agent
):
    """Computer deletion cascades only to that computer's results and restores them if the transaction rolls back."""
    rows = [
        await create_result(api, computer),
        await create_result(api, computer, version="0.0.3"),
        await create_result(api, computer, name="multi_core"),
    ]
    async with app.state.db.write() as session:
        other = BenchmarkResult(
            computer_id=other_agent["computer"]["id"],
            benchmark_name="single_core",
            benchmarker_version="0.0.2",
            number=3,
        )
        session.add(other)
        await session.flush()
        other_before = public(other)

    async def remove_computer(session):
        await session.execute(
            delete(ComputerOwnership).where(ComputerOwnership.computer_id == computer["id"])
        )
        await session.execute(delete(Computer).where(Computer.id == computer["id"]))

    with pytest.raises(RuntimeError, match="abort computer deletion"):
        async with app.state.db.write() as session:
            await remove_computer(session)
            assert await session.get(BenchmarkResult, rows[0]["id"]) is None
            raise RuntimeError("abort computer deletion")
    async with app.state.db.read() as session:
        assert await session.get(Computer, computer["id"]) is not None
        for row in rows:
            assert await session.get(BenchmarkResult, row["id"]) is not None

    async with app.state.db.write() as session:
        await remove_computer(session)
    async with app.state.db.read() as session:
        for row in rows:
            assert await session.get(BenchmarkResult, row["id"]) is None
        assert public(await session.get(BenchmarkResult, other.id)) == other_before


async def test_postgres_benchmark_schema_uses_native_uuid_and_cascading_foreign_key(
    postgres_configuration,
):
    """PostgreSQL compilation uses native UUID columns, the benchmark identity constraint, and a cascading computer foreign key."""
    engine = postgres_configuration.engine.sync_engine
    ddl = str(CreateTable(BenchmarkResult.__table__).compile(bind=engine))
    assert "id UUID NOT NULL" in ddl
    assert "computer_id UUID NOT NULL" in ddl
    assert "number FLOAT NOT NULL" in ddl
    assert "FOREIGN KEY(computer_id) REFERENCES computers (id) ON DELETE CASCADE" in ddl
    assert "UNIQUE (computer_id, benchmark_name, benchmarker_version)" in ddl
    compiled = select(BenchmarkResult).where(BenchmarkResult.id == str(UUID(int=1)))
    assert "::UUID" in str(compiled.compile(bind=engine))
    assert postgres_configuration.engine.pool.checkedout() == 0
