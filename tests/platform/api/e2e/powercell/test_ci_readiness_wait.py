"""Readiness latency contracts through real HTTP/SQLite and mocked GitHub."""

import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from starlette.requests import Request

from throng_api import ci_controller
from throng_api.models import CIReservation, Command

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner, reserve
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: only the first ten seconds after JIT command creation get faster
# checks; concurrent reservations share that burst, older preparations return to
# five seconds, and claiming still fences competing workers with a durable lease.
@pytest.mark.parametrize(
    "age,peers,interval", [(0, 1, 1), (9.9, 1, 1), (10, 1, 5), (60, 1, 5), (0, 3, 3), (0, 8, 5)]
)
async def test_startup_probe_is_short_bounded_and_shared(
    client, route, ci_peer, powercell, app, clock, age, peers, interval
):
    """Bound the startup readiness probe and share it between waiting requests."""
    result, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.status, row.next_check = "preparing", clock.now
        command = await session.get(Command, row.command_id)
        command.created_at = clock.now - timedelta(seconds=age)
        for index in range(peers - 1):
            session.add(
                CIReservation(
                    route_id=route["id"],
                    run_id=2000 + index,
                    run_attempt=1,
                    instance=str(index),
                    label="powercell-peer-" + str(index),
                    status="running",
                    deadline=clock.now + timedelta(minutes=1),
                    expires_at=clock.now + timedelta(hours=1),
                    next_check=clock.now + timedelta(hours=1),
                )
            )
    work, _ = await ci_controller.claim_work(app)
    assert work.id == result["id"]
    assert (work.next_check - clock.now).total_seconds() == interval
    assert work.lease_nonce and work.lease_until == clock.now + timedelta(seconds=60)
    assert await ci_controller.claim_work(app) is None


# Assertions: one waiting HTTP request observes ready, failure, cancellation or
# deletion without client sleeps or another reservation; other writes remain
# possible during the wait and the wait itself never polls GitHub.
@pytest.mark.parametrize("transition", ["ready", "failed", "cancelled", "deleted"])
async def test_wait_observes_state_changes_without_holding_transaction(
    client, route, ci_peer, powercell, app, clock, transition
):
    """Observe readiness changes without keeping a database transaction open."""
    result, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.status = "preparing"
    calls = len(ci_peer.calls)
    pending = asyncio.create_task(
        client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds=0.5",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={"instance": "default"},
        )
    )
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), timeout=0.08)
        async with asyncio.timeout(0.5), app.state.db.write() as session:
            row = await session.get(CIReservation, result["id"])
            if transition == "deleted":
                await session.delete(row)
            else:
                row.status = transition
                row.reason = None if transition == "ready" else "test_transition"
        response = await asyncio.wait_for(pending, timeout=0.5)
        if transition == "deleted":
            assert response.status_code == 404
        else:
            assert response.status_code == 200
            expected = "ready" if transition == "ready" else "failed"
            assert response.json()["status"] == expected
            assert response.json()["id"] == result["id"]
        assert len(ci_peer.calls) == calls
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


# Assertions: a timeout returns the same preparing reservation within its bound;
# ready/failed responses and immediate-poll clients do not wait; invalid bounds cannot allocate.
async def test_wait_timeout_and_legacy_poll_share_one_reservation(
    client, route, ci_peer, powercell, app
):
    """Keep one reservation across a readiness timeout and a legacy poll."""
    first = await reserve(client, route, ci_peer)
    started = time.monotonic()
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds=0.06",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    elapsed = time.monotonic() - started
    assert 0.05 <= elapsed < 1
    assert response.status_code == 200 and response.json() == first.json()
    async with app.state.db.read() as session:
        assert len((await session.scalars(select(CIReservation))).all()) == 1


@pytest.mark.parametrize("wait", [-1, 20.1, "nan", "inf"])
async def test_wait_bound_is_validated_before_reserving(client, route, ci_peer, app, wait):
    """Reject an invalid readiness wait bound before allocating a reservation."""
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds={wait}",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 422
    async with app.state.db.read() as session:
        assert (await session.scalars(select(CIReservation))).all() == []


# Assertions: an already known outcome returns immediately even when the caller
# permits twenty seconds of waiting. Client disconnect stops waiting promptly.
async def test_ready_response_does_not_wait(client, route, ci_peer, powercell, app, clock):
    """Return an already ready reservation without waiting for a notification."""
    result, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    response = await asyncio.wait_for(
        client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds=20",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        ),
        timeout=0.5,
    )
    assert response.status_code == 200 and response.json() == result


async def test_disconnected_client_does_not_leave_waiting_handler(
    client, route, ci_peer, powercell, monkeypatch
):
    """Release the readiness waiter when its HTTP client disconnects."""
    checked = []

    async def disconnected(self):
        checked.append(True)
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    response = await asyncio.wait_for(
        client.post(
            f"/v1/github/ci/routes/{route['id']}/reserve?wait_seconds=20",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        ),
        timeout=0.5,
    )
    assert response.status_code == 200 and response.json()["status"] == "preparing"
    assert checked
