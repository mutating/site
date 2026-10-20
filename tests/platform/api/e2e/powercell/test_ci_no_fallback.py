"""Fail closed at allocation, rather than waiting for a hosted alternative."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from throng_api.models import AgentTask, CIReservation, CIRoute, Computer, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import reserve, schedule
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: even a long-poll request fails immediately without a Mac; retries
# are durable, leave no isolate/task/JIT behind, and cannot start later.
async def test_no_mac_fails_immediately_and_cannot_be_scheduled_later(
    client, route, ci_peer, app, clock
):
    """Decline immediately and retain an immutable, resource-free decision."""
    response = await asyncio.wait_for(
        client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds=20",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        ),
        3,
    )
    first = response.json()
    assert response.status_code == 200 and first["status"] == "failed"
    assert first["reason"] == "no_capacity" and "runs_on" not in first
    assert (await reserve(client, route, ci_peer)).json() == first
    await schedule(app, clock)
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CIReservation)) == 1
        assert await session.scalar(select(func.count()).select_from(Isolate)) == 0
        assert await session.scalar(select(func.count()).select_from(AgentTask)) == 0
    assert ci_peer.jit_count == 0


# Assertions: allocation applies every existing scheduler constraint before
# acknowledging preparation, including exact machine and runtime restrictions.
@pytest.mark.parametrize(
    "constraint",
    ["offline", "disabled", "lease", "cpu", "memory", "disk", "image", "isolation", "computer"],
)
async def test_ineligible_mac_fails_without_waiting(
    client, route, ci_peer, powercell, app, clock, constraint
):
    """Apply each scheduler eligibility constraint before returning preparation."""
    async with app.state.db.write() as session:
        computer = await session.get(Computer, powercell.computer_id)
        if constraint == "offline":
            computer.online = False
        elif constraint == "disabled":
            computer.enabled = False
        elif constraint == "lease":
            computer.lease_expires_at = clock.now
        elif constraint == "cpu":
            computer.target_cpu = 1999
        elif constraint == "memory":
            computer.target_memory = 4 * 1024**3 - 1
        elif constraint == "disk":
            computer.target_disk = 10 * 1024**3 - 1
        elif constraint == "image":
            computer.capacity_report = {**computer.capacity_report, "image_families": ["ubuntu"]}
        elif constraint == "isolation":
            computer.isolation_modes = ["container"]
        else:
            (await session.get(CIRoute, route["id"])).computer_ids = [str(uuid4())]
    result = (await reserve(client, route, ci_peer)).json()
    assert result["status"] == "failed" and result["reason"] == "no_capacity", result
    assert "runs_on" not in result and ci_peer.jit_count == 0


# Assertions: concurrent requests reserve capacity atomically; the loser fails
# without a queued isolate or duplicate create task. No overbooking is possible.
async def test_capacity_is_reserved_in_the_reserve_transaction(
    client, route, ci_peer, powercell, app
):
    """Serialize competing allocations without overbooking or pending work."""
    async with app.state.db.write() as session:
        (await session.get(CIRoute, route["id"])).max_parallel = 2
    responses = await asyncio.gather(
        *(reserve(client, route, ci_peer, instance=str(i)) for i in range(2))
    )
    results = [r.json() for r in responses]
    assert sorted(r["status"] for r in results) == ["failed", "preparing"], results
    assert next(r for r in results if r["status"] == "failed")["reason"] == "no_capacity"
    async with app.state.db.read() as session:
        [isolate] = (await session.scalars(select(Isolate))).all()
        assert isolate.reserved and isolate.computer_id == powercell.computer_id
        assert await session.scalar(select(func.count()).select_from(AgentTask)) == 1
        computer = await session.get(Computer, powercell.computer_id)
        assert computer.reserved_disk == route["disk_bytes"]


# Assertions: the public contract has no fallback option or hosted-size limits;
# workflow retains rerun fencing and fails its preparation job without outputs.
async def test_route_and_workflow_have_only_powercell_policy(api, route, ci_peer):
    """Expose a single execution policy and preserve attempt fencing."""
    assert "fallback" not in route
    policy = {k: route[k] for k in ["installation_id", "repository_id", "workflow_path", "job_key"]}
    response = await api.post(
        "/v1/github/routes",
        json={
            **policy,
            "cpu_millis": 3000,
            "memory_bytes": 9 * 1024**3,
            "disk_bytes": 15 * 1024**3,
        },
    )
    assert response.status_code == 201, response.text
    assert (
        await api.post("/v1/github/routes", json={**policy, "fallback": "none"})
    ).status_code == 422
    workflow = (await api.get(f"/v1/github/routes/{route['id']}/workflow")).json()["yaml"]
    assert "fallback" not in workflow.lower()
    assert "backend" not in workflow and "run_attempt == github.run_attempt" in workflow
    assert "Re-run the routing job or all jobs" in workflow


# Assertions: upgrade removes both persisted policies without losing route/run
# identity, failure reason, active runner credentials or cleanup obligations.
async def test_upgrade_preserves_history_and_pending_cleanup(route, ci_peer, app, clock):
    """Remove old policies while retaining history and cleanup obligations."""
    import anyio
    from alembic import command
    from sqlalchemy import inspect, text

    from throng_api.cli import migration_config

    async with app.state.db.write() as session:
        row = CIReservation(
            route_id=route["id"],
            run_id=1234,
            run_attempt=1,
            instance="default",
            label="powercell-legacy",
            status="failed",
            reason="no_capacity",
            deadline=clock.now,
            expires_at=clock.now,
            next_check=clock.now,
            credentials="encrypted-cleanup-state",
        )
        session.add(row)
        await session.flush()
        reservation_id = row.id
    await app.state.db.close()
    configuration = migration_config(app.state.settings)
    await anyio.to_thread.run_sync(command.downgrade, configuration, "0010")
    async with app.state.db.engine.begin() as connection:
        await connection.execute(
            text("UPDATE ci_reservations SET status='fallback', fallback='ubuntu-24.04-arm'")
        )
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.upgrade, configuration, "head")
    async with app.state.db.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda c: {
                name: [column["name"] for column in inspect(c).get_columns(name)]
                for name in ["ci_routes", "ci_reservations"]
            }
        )
        assert all("fallback" not in names for names in columns.values())
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, reservation_id)
        assert row.status == "failed" and row.reason == "no_capacity"
        assert row.route_id == route["id"] and row.credentials == "encrypted-cleanup-state"
        assert row.cleaned_at is None
        assert (await session.get(CIRoute, route["id"])).repository_id == route["repository_id"]
