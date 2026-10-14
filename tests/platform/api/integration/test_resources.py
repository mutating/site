"""Database constraints, atomic history writes and migration of occupied legacy workers."""

from datetime import timedelta

import anyio
import pytest
from alembic import command as alembic
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from throng_api.models import (
    CPU_RESOURCE_ID,
    AgentTask,
    Computer,
    ComputerResource,
    ComputerResourceChange,
    ResourceType,
    User,
)
from throng_api.resource_history import history

pytestmark = pytest.mark.anyio


async def test_computer_type_uniqueness_is_a_database_constraint(app, computer, computer_resources):
    """Bypassing HTTP cannot insert a second resource of the same type for one computer."""
    with pytest.raises(IntegrityError):
        async with app.state.db.write() as session:
            session.add(
                ComputerResource(
                    computer_id=computer["id"], resource_type_id=CPU_RESOURCE_ID, total=1
                )
            )
            await session.flush()
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(ComputerResource)) == 2


@pytest.mark.parametrize(
    "values",
    [
        {"total": -1},
        {"total": 2**60 + 1},
        {"occupied": -1},
        {"occupied": 2001},
        {"version": 0},
        {"occupied": 1, "deleted_at": True},
    ],
)
async def test_capacity_invariants_are_enforced_in_database(app, computer_resources, clock, values):
    """SQL writes cannot create negative, excessive or archived occupied capacity or an invalid record version."""
    values = dict(values)
    if values.get("deleted_at"):
        values["deleted_at"] = clock.now
    with pytest.raises(IntegrityError):
        async with app.state.db.write() as session:
            await session.execute(
                update(ComputerResource)
                .where(ComputerResource.id == computer_resources["cpu"]["id"])
                .values(**values)
            )
    async with app.state.db.read() as session:
        row = await session.get(ComputerResource, computer_resources["cpu"]["id"])
        assert (
            row.total == 2000 and row.occupied == 0 and row.version == 1 and row.deleted_at is None
        )


async def test_history_failure_rolls_back_flushed_resource_changes(
    api, app, computer_resources, rejected_resource_history
):
    """A failed history INSERT rolls back the already-flushed capacity update and version, preventing gaps between inventory and charts."""
    path = f"/api/v1/computer-resources/{computer_resources['cpu']['id']}"
    response = await api.patch(path, json={"total": 3000})
    assert response.status_code == 500
    assert (await api.get(path)).json() == computer_resources["cpu"]
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(ComputerResourceChange)) == 2


async def test_populated_migration_preserves_reservations_foreign_keys_and_history(
    alembic_config, legacy_occupied_database, migration_database, user
):
    """Upgrading a legacy database preserves active work and capacity, seeds one baseline per resource and explicitly bounds historical availability."""
    database = migration_database
    async with database.engine.connect() as connection:
        legacy = (
            await connection.execute(
                text(
                    "SELECT cpu_millis, memory_bytes, reserved_cpu, reserved_memory FROM computers WHERE reserved_cpu > 0"
                )
            )
        ).one()
        assert tuple(legacy) == (2000, 2048, 1000, 1024)
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    async with database.read() as session:
        computer = await session.get(Computer, legacy_occupied_database["computer"]["id"])
        assert (
            computer.cpu_millis,
            computer.memory_bytes,
            computer.reserved_cpu,
            computer.reserved_memory,
        ) == (2000, 2048, 1000, 1024)
        assert (await session.get(User, user["id"])).is_system_admin is False
        assert (
            await session.get(AgentTask, legacy_occupied_database["task"]["id"])
        ).isolate_id == legacy_occupied_database["isolate"]["id"]
        assert await session.scalar(select(func.count()).select_from(ResourceType)) == 2
        events = (await session.scalars(select(ComputerResourceChange))).all()
        assert len(events) == 6
        assert {event.reason for event in events} == {"migration"}
        assert {event.after_user_id for event in events} == {user["id"], None}
        company_computer, detached_computer = legacy_occupied_database["extra_computers"]
        assert {
            event.after_company_id
            for event in events
            if event.computer_id == company_computer["id"]
        } == {company_computer["ownership"]["company_id"]}
        assert {
            (event.after_user_id, event.after_company_id)
            for event in events
            if event.computer_id == detached_computer["id"]
        } == {(None, None)}
        assert len({event.revision for event in events}) == 1
        start = events[0].created_at
        report = await history(
            session, (user["id"], None), start - timedelta(days=1), start + timedelta(days=1)
        )
        assert report["available_from"] == report["initial_at"] == start
        assert report["points"] == []
        assert {row["code"]: (row["total"], row["occupied"]) for row in report["initial"]} == {
            "cpu": (2000, 1000),
            "memory": (2048, 1024),
        }
        unavailable = await history(
            session, (user["id"], None), start - timedelta(days=2), start - timedelta(days=1)
        )
        assert (
            unavailable["initial_at"] is None
            and unavailable["initial"] == unavailable["points"] == []
        )
    async with database.engine.connect() as connection:
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("computers"))
        assert not {"cpu_millis", "memory_bytes", "reserved_cpu", "reserved_memory"} & {
            column["name"] for column in columns
        }
        if connection.dialect.name == "sqlite":
            assert (await connection.execute(text("PRAGMA foreign_key_check"))).all() == []
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    async with database.read() as session:
        assert await session.scalar(select(func.count()).select_from(ComputerResourceChange)) == 6


@pytest.mark.parametrize("total", [0, 1_000_001])
async def test_downgrade_refuses_capacities_that_legacy_schema_cannot_represent(
    alembic_config, api, app, computer_resources, http_server, total
):
    """The resource migration refuses an incompatible legacy downgrade before changing capacity data; the latest indexes can then be restored."""
    import asyncio

    assert (
        await api.patch(
            f"/api/v1/computer-resources/{computer_resources['cpu']['id']}", json={"total": total}
        )
    ).status_code == 200
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0002")
    with pytest.raises(RuntimeError, match="positive CPU|legacy limit"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0001")
    async with app.state.db.read() as session:
        row = await session.get(ComputerResource, computer_resources["cpu"]["id"])
        assert row.total == total
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
