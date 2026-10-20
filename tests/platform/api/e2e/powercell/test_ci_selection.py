"""Single-request selection preserves the former finalization guarantees."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from throng_api import ci
from throng_api.models import CIReservation, CIRoute, Command, Computer, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner, reserve
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: cached and long-polled readiness are selected before returning the
# label; response replay keeps the original lease, and requires no second request.
@pytest.mark.parametrize("held", [False, True])
async def test_reserve_finalizes_selection_without_another_request(
    client, route, ci_peer, powercell, app, clock, monkeypatch, held
):
    """Publish one confirmed selection with a bounded, nonrenewable lease."""
    prepared, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, prepared["id"])
        row.status = "preparing" if held else "ready"

    observed_preparing = asyncio.Event()
    original_decision = ci.decision

    def observe(row):
        if row.id == prepared["id"] and row.status == "preparing":
            observed_preparing.set()
        return original_decision(row)

    monkeypatch.setattr(ci, "decision", observe)

    async def request():
        return await client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds={1 if held else 0}",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        )

    pending = asyncio.create_task(request())
    if held:
        await asyncio.wait_for(observed_preparing.wait(), 2)
        assert not pending.done()
        async with app.state.db.write() as session:
            (await session.get(CIReservation, prepared["id"])).status = "ready"
    response = await pending
    assert response.status_code == 200 and response.json() == prepared
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, prepared["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert row.status == "selected" and row.expires_at == clock.now + timedelta(seconds=300)
        assert isolate.expires_at == row.expires_at
        expiry = row.expires_at
    clock.now += timedelta(seconds=1)
    assert (await reserve(client, route, ci_peer)).json() == prepared
    async with app.state.db.read() as session:
        assert (await session.get(CIReservation, prepared["id"])).expires_at == expiry


# Assertions: after observing ready, a final write must re-read authorization,
# status, deadlines, command and assigned session. No label or renewed lease may
# escape when any of them changed, whether ready came from cache or long polling.
@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "authorization",
        "deadline",
        "expiry",
        "cancelled",
        "isolate",
        "command",
        "session",
        "lease",
        "deleted",
    ],
)
async def test_reserve_rechecks_changes_before_publishing_ready(
    client, route, ci_peer, powercell, app, clock, monkeypatch, held, change
):
    """Fence a state change between readiness observation and final publication."""
    prepared, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    original_write, original_decision = app.state.db.write, ci.decision
    observed = False
    observed_preparing = asyncio.Event()
    async with original_write() as session:
        row = await session.get(CIReservation, prepared["id"])
        row.status = "preparing" if held else "ready"
        isolate_id = row.isolate_id
        before = row.expires_at, (await session.get(Isolate, row.isolate_id)).expires_at

    def decision(row):
        nonlocal observed
        if row.id == prepared["id"]:
            if row.status == "ready":
                observed = True
            elif row.status == "preparing":
                observed_preparing.set()
        return original_decision(row)

    injected = False

    @asynccontextmanager
    async def write():
        nonlocal injected
        if observed and not injected:
            injected = True
            async with original_write() as session:
                row = await session.get(CIReservation, prepared["id"])
                isolate = await session.get(Isolate, row.isolate_id)
                computer = await session.get(Computer, isolate.computer_id)
                if change == "deleted":
                    await session.delete(row)
                elif change == "authorization":
                    (await session.get(CIRoute, row.route_id)).enabled = False
                elif change == "deadline":
                    clock.now += timedelta(seconds=6)
                elif change == "expiry":
                    row.expires_at = clock.now
                elif change == "cancelled":
                    row.status = "cancelled"
                elif change == "isolate":
                    isolate.status = "destroyed"
                elif change == "command":
                    (await session.get(Command, row.command_id)).status = "completed"
                elif change == "session":
                    computer.session_id = None
                else:
                    computer.lease_expires_at = clock.now
        async with original_write() as session:
            yield session

    monkeypatch.setattr(ci, "decision", decision)
    monkeypatch.setattr(app.state.db, "write", write)
    pending = asyncio.create_task(
        client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds={1 if held else 0}",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        )
    )
    if held:
        await asyncio.wait_for(observed_preparing.wait(), 2)
        async with original_write() as session:
            (await session.get(CIReservation, prepared["id"])).status = "ready"
    response = await pending
    assert response.status_code == (404 if change == "deleted" else 409)
    assert "runs_on" not in response.text
    assert injected
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, prepared["id"])
        if change == "deleted":
            assert row is None
        else:
            assert row.status != "selected"
        assert (await session.get(Isolate, isolate_id)).expires_at == before[1]


async def test_reserve_route_deleted_after_identity_is_unavailable(client, route, ci_peer, app, monkeypatch):
    """OIDC authorization cannot keep a route alive across the next DB read.
    Deletion is a normal unavailable route, never an attribute-error HTTP 500.
    """
    original, reads = app.state.db.read, 0
    @asynccontextmanager
    async def deleted_before_state_read():
        nonlocal reads
        reads += 1
        if reads == 2:
            async with app.state.db.write() as session:
                await session.delete(await session.get(CIRoute, route['id']))
        async with original() as session:
            yield session
    monkeypatch.setattr(app.state.db, 'read', deleted_before_state_read)
    response = await reserve(client, route, ci_peer)
    assert reads == 2
    assert response.status_code == 404, response.text

async def test_reserve_route_deleted_before_final_lock_is_unavailable(client, route, ci_peer, app, monkeypatch):
    """The provider call releases the DB snapshot; deletion before locking is normal."""
    from throng_api import ci
    original = ci.verify_repository
    async def verified_then_deleted(*args, **kwargs):
        result = await original(*args, **kwargs)
        async with app.state.db.write() as session:
            await session.delete(await session.get(CIRoute, route['id']))
        return result
    monkeypatch.setattr(ci, 'verify_repository', verified_then_deleted)
    response = await reserve(client, route, ci_peer)
    assert response.status_code == 404, response.text

async def test_cancel_route_deleted_after_identity_is_unavailable(client, route, ci_peer, app, monkeypatch):
    """Cancel must also handle route deletion between OIDC authorization and locking."""
    from throng_api import ci
    original = ci.routing_identity
    async def authenticated_then_deleted(*args, **kwargs):
        claims = await original(*args, **kwargs)
        async with app.state.db.write() as session:
            await session.delete(await session.get(CIRoute, route['id']))
        return claims
    monkeypatch.setattr(ci, 'routing_identity', authenticated_then_deleted)
    response = await client.post(f"/v1/github/ci/routes/{route['id']}/cancel", headers={'Authorization':'Bearer '+ci_peer.token()}, json={})
    assert response.status_code == 404, response.text
