"""Queue observation must use one coherent lifecycle state and clock snapshot."""

import asyncio
from datetime import timedelta

import pytest

from throng_api import ci
from throng_api.models import CIReservation, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import route as route
from .test_ci_watch import platform_state, watch

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: a real start/completion before selection expiry cannot be turned
# into a queue failure by combining an earlier row with a later clock. A stale
# response may conservatively wait; the next poll observes the confirmed start.
@pytest.mark.parametrize("event", ["in_progress", "completed"])
async def test_watch_does_not_fail_a_concurrently_started_or_completed_job(
    client, route, ci_peer, github, powercell, app, clock, monkeypatch, event
):
    """Keep a valid start safe when a watch read overlaps its old selection deadline.

    The watch pauses only after loading its actual database row. A separately
    signed GitHub webhook starts or completes the job before that lease expires.
    Resuming the read after the old expiry must not fail-fast the healthy job.
    Both watch responses leave every lifecycle row and write revision unchanged.
    """
    selection, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, selection["id"])
        row.expires_at = clock.now + timedelta(seconds=1)
        (await session.get(Isolate, row.isolate_id)).expires_at = row.expires_at
    authorized, proceed = asyncio.Event(), asyncio.Event()
    creator_authorized = ci.creator_authorized

    async def delayed_authorization(session, binding):
        result = await creator_authorized(session, binding)
        authorized.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(ci, "creator_authorized", delayed_authorization)
    pending = asyncio.create_task(watch(client, route, ci_peer))
    try:
        await asyncio.wait_for(authorized.wait(), 3)
        response = await deliver(
            client,
            github,
            {
                "action": event,
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": {
                    "run_id": 1234,
                    "run_attempt": 1,
                    "runner_id": 55,
                    "runner_name": selection["runs_on"][0],
                    "conclusion": "success",
                },
            },
        )
        assert response.status_code == 202, response.text
        clock.now += timedelta(seconds=2)
        async with app.state.db.read() as session:
            row = await session.get(CIReservation, selection["id"])
            assert row.status == ("running" if event == "in_progress" else "completed")
            if event == "in_progress":
                assert row.expires_at > clock.now
            else:
                assert row.reason == "github_success"
        before = await platform_state(app)
        proceed.set()
        response = await asyncio.wait_for(pending, 3)
        assert response.status_code == 200, response.text
        assert response.json() in ({"status": "waiting"}, {"status": "started"})
        assert await platform_state(app) == before
        response = await watch(client, route, ci_peer)
        assert response.status_code == 200 and response.json() == {"status": "started"}
        assert await platform_state(app) == before
    finally:
        proceed.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
