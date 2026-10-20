"""A CI cancellation must retain its full native-stop deadline after lock contention."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from starlette.requests import Request

from throng_api import ci
from throng_api.models import AgentTask, CIReservation, Computer

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner, schedule
from .test_ci import route as route
from .test_ci_controller_lock_time import blocked_write

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def delayed_cancel(client, route, ci_peer, app, clock, monkeypatch, *, component=False):
    """Let the actual SQLite Coordination lock delay a signed cancellation by two seconds."""

    async def cancel():
        if component:
            request = Request(
                {
                    "type": "http",
                    "app": app,
                    "headers": [(b"authorization", ("Bearer " + ci_peer.token()).encode())],
                }
            )
            return await ci.cancel_selection(UUID(route["id"]), ci.Reserve(), request)
        return await client.post(
            f"/v1/github/ci/routes/{route['id']}/cancel",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={},
        )

    response = await blocked_write(app, clock, monkeypatch, cancel, 2)
    if component:
        assert response["status"] == "failed" and response["reason"] == "bootstrap_cancelled"
    else:
        assert response.status_code == 200, response.text
    return clock.now


async def test_cancel_grants_full_cleanup_deadline_after_lock_wait(
    client, route, ci_peer, powercell, app, clock, monkeypatch
):
    """A successful cancellation cannot backdate new destroy/cancel tasks or its cleanup grace."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    cancelled_at = await delayed_cancel(
        client, route, ci_peer, app, clock, monkeypatch, component=True
    )
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        tasks = (
            await session.scalars(
                select(AgentTask).where(
                    AgentTask.isolate_id == row.isolate_id,
                    AgentTask.kind.in_(["cancel", "destroy"]),
                )
            )
        ).all()
        assert {task.kind for task in tasks} == {"cancel", "destroy"}
        assert all(
            task.deadline
            == cancelled_at + timedelta(seconds=app.state.settings.cancellation_timeout_seconds)
            for task in tasks
        )
        assert row.finished_at == cancelled_at


async def test_native_stop_is_deliverable_for_the_configured_window_after_ci_cancel(
    client, route, ci_peer, powercell, app, clock, monkeypatch
):
    """A brief API/lock delay followed by a 29-second agent outage stays within the 30-second stop window.

    The device lease remains valid, the agent uses real signed HTTP, and no
    scheduler tick has rewritten task state. Old receive-time deadlines make
    every new stop task disappear before this valid agent retry.
    """
    await ready_runner(client, route, ci_peer, powercell, app, clock)
    cancelled_at = await delayed_cancel(client, route, ci_peer, app, clock, monkeypatch)
    clock.now = cancelled_at + timedelta(
        seconds=app.state.settings.cancellation_timeout_seconds - 1
    )
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    kinds = {task["kind"] for task in response.json()["tasks"]}
    assert "destroy" in kinds
    await schedule(app, clock)
    async with app.state.db.read() as session:
        assert (await session.get(Computer, powercell.computer_id)).online
