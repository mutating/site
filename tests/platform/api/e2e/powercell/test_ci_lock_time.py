"""CI admission checks time after waiting for the serialized database writer."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from throng_api import ci
from throng_api.models import AgentTask, CIReservation, Computer, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: admission uses the clock after acquiring the coordination lock.
# Expired preparation and expired Mac leases both fail without reserving capacity,
# creating a VM/task, or allowing the same decision to become work on retry.
@pytest.mark.parametrize("boundary", ["http", "component"])
@pytest.mark.parametrize("expired", ["preparation", "agent_lease"])
@pytest.mark.parametrize("late_by", [0, 1])
async def test_admission_rechecks_time_after_database_lock(
    client, route, ci_peer, powercell, app, clock, monkeypatch, boundary, expired, late_by
):
    """A delayed admission cannot allocate past preparation or Mac lease expiry.

    Exercise the admission component directly and through real HTTP with the same
    signed OIDC identity, enrolled device, and SQLite database. Events place the
    request behind a genuine coordination lock after repository verification;
    advancing the explicit clock models a slow writer without timing sleeps.
    """
    write = app.state.db.write
    if expired == "agent_lease":
        async with write() as session:
            (await session.get(Computer, powercell.computer_id)).lease_expires_at = (
                clock.now + timedelta(seconds=1)
            )
    waiting, verified, proceed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    verify_repository = ci.verify_repository

    async def verified_repository(*args):
        result = await verify_repository(*args)
        verified.set()
        await proceed.wait()
        return result

    @asynccontextmanager
    async def observed_write():
        if verified.is_set() and proceed.is_set():
            waiting.set()
        async with write() as session:
            yield session

    monkeypatch.setattr(ci, "verify_repository", verified_repository)
    monkeypatch.setattr(app.state.db, "write", observed_write)

    async def request():
        token = ci_peer.token()
        if boundary == "component":
            scope = {
                "type": "http",
                "app": app,
                "headers": [(b"authorization", ("Bearer " + token).encode())],
            }
            return await ci.reserve_once(UUID(route["id"]), ci.Reserve(), Request(scope))
        response = await client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve",
            headers={"Authorization": "Bearer " + token},
            json={},
        )
        assert response.status_code == 200, response.text
        return response.json()

    pending = asyncio.create_task(request())
    try:
        await asyncio.wait_for(verified.wait(), 3)
        async with write():
            proceed.set()
            await asyncio.wait_for(waiting.wait(), 3)
            assert not pending.done()
            clock.now += timedelta(seconds=(10 if expired == "preparation" else 1) + late_by)
        result = await asyncio.wait_for(pending, 3)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    assert result["status"] == "failed", result
    assert result["reason"] == (
        "preparation_timeout" if expired == "preparation" else "no_capacity"
    )
    assert "runs_on" not in result and await request() == result
    async with write() as session:
        assert await session.scalar(select(func.count()).select_from(Isolate)) == 0
        assert await session.scalar(select(func.count()).select_from(AgentTask)) == 0
        computer = await session.get(Computer, powercell.computer_id)
        assert computer.reserved_disk == computer.reserved_cpu == computer.reserved_memory == 0
        row = await session.get(CIReservation, result["id"])
        assert row.created_at == row.finished_at == row.cleaned_at == clock.now
    assert ci_peer.jit_count == 0
