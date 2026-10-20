"""CI lifecycle deadlines remain authoritative across real coordination-lock waits."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from throng_api import ci_controller
from throng_api.models import CIReservation, CIRoute, Computer, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci_review import review_route as review_route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def blocked_write(app, clock, monkeypatch, operation, seconds):
    """Make a real SQLite coordination wait cross a clock boundary deterministically."""
    attempted = asyncio.Event()
    original = app.state.db.write

    @asynccontextmanager
    async def observed_write():
        attempted.set()
        async with original() as session:
            yield session

    async with original():
        monkeypatch.setattr(app.state.db, "write", observed_write)
        task = asyncio.create_task(operation())
        await asyncio.wait_for(attempted.wait(), 3)
        assert not task.done()
        clock.now += timedelta(seconds=seconds)
    return await task


@pytest.mark.parametrize("boundary", ["selection", "preparation", "isolate", "agent", "live"])
@pytest.mark.parametrize("entrypoint", ["webhook", "component"])
async def test_start_transition_obeys_current_runner_leases(
    client, review_route, ci_peer, github, powercell, app, clock, monkeypatch, boundary, entrypoint
):
    """A start delivery waiting for the write lock must use the processing time.

    The signature, HTTP server, SQL lock and runner/VM state are real. A delivery
    starts just before the selection expires, then obtains the coordination lock
    after expiry; neither lease may be renewed from that stale receive time.
    """
    result, _ = await ready_runner(
        client,
        review_route,
        ci_peer,
        powercell,
        app,
        clock,
        select_runner=boundary != "preparation",
    )
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        computer = await session.get(Computer, isolate.computer_id)
        row.expires_at = isolate.expires_at = computer.lease_expires_at = clock.now + timedelta(
            hours=1
        )
        if boundary == "selection":
            row.expires_at = clock.now + timedelta(seconds=1)
        elif boundary == "preparation":
            row.deadline = clock.now + timedelta(seconds=1)
        elif boundary == "isolate":
            isolate.expires_at = clock.now + timedelta(seconds=1)
        elif boundary == "agent":
            computer.lease_expires_at = clock.now + timedelta(seconds=1)
        before = row.expires_at, isolate.expires_at

    async def delivery():
        return await deliver(
            client,
            github,
            {
                "action": "in_progress",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": {
                    "run_id": 1234,
                    "run_attempt": 1,
                    "runner_id": 55,
                    "runner_name": result["runs_on"][0],
                },
            },
        )

    if entrypoint == "webhook":
        response = await blocked_write(app, clock, monkeypatch, delivery, 2)
        assert response.status_code == 202
    else:
        clock.now += timedelta(seconds=2)
        async with app.state.db.write() as session:
            row = await session.get(CIReservation, result["id"])
            route = await session.get(CIRoute, row.route_id)
            await ci_controller.mark_running(session, row, route, clock.now)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        if boundary == "live":
            assert row.status == "running"
            assert row.expires_at == isolate.expires_at == clock.now + timedelta(hours=1)
            assert row.started_at == clock.now
        else:
            assert row.status == ("ready" if boundary == "preparation" else "selected")
            assert (row.expires_at, isolate.expires_at) == before
            assert row.started_at is None


@pytest.mark.parametrize("wait_seconds", [2, 61])
async def test_claim_lifetime_begins_after_coordination_wait(
    client, review_route, ci_peer, powercell, app, clock, monkeypatch, wait_seconds
):
    """A durable claim must not already be expired when its transaction commits.

    A controller blocked for longer than its 60-second lease must obtain a fresh
    lease after the lock. Otherwise another worker immediately claims the same
    registration work and starts competing GitHub-side registration operations.
    """
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.next_check = clock.now
    claimed = await blocked_write(
        app, clock, monkeypatch, lambda: ci_controller.claim_work(app), wait_seconds
    )
    assert claimed is not None
    assert claimed[0].lease_until == clock.now + timedelta(seconds=60)
    assert claimed[0].next_check > clock.now
    assert await ci_controller.claim_work(app) is None
    clock.now = claimed[0].next_check
    assert await ci_controller.claim_work(app) is None
    clock.now = claimed[0].lease_until
    replacement, _ = await ci_controller.claim_work(app)
    assert replacement.id == claimed[0].id
    assert replacement.lease_nonce != claimed[0].lease_nonce


@pytest.mark.parametrize("expired", [False, True])
async def test_local_tick_expires_after_coordination_wait(
    client, review_route, ci_peer, powercell, app, clock, monkeypatch, expired
):
    """The first tick after a lock stall must expire leases using current time."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.expires_at = clock.now + timedelta(seconds=1 if expired else 3)
    await blocked_write(app, clock, monkeypatch, lambda: ci_controller.local_tick(app), 2)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        if expired:
            assert (row.status, row.reason) == ("timed_out", "runner_timeout")
            assert row.finished_at == clock.now and row.credentials is None
        else:
            assert row.status == "selected" and row.finished_at is None
