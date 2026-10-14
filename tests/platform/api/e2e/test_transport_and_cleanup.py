"""HTTP scenarios for disconnects and overlapping lifecycle deadlines."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from throng_api.models import Coordination

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_disconnected_long_poll_releases_handler_before_its_timeout(
    agent, upload_connection, http_server, app
):
    """Closing the real TCP connection interrupts an empty 25-second poll promptly and leaves no request task running."""
    _, writer = upload_connection
    async with app.state.db.read() as session:
        revision = await session.scalar(select(Coordination.revision))
    body = json.dumps({"wait_seconds": 25}).encode()
    headers = "\r\n".join(f"{key}: {value}" for key, value in agent["headers"].items())
    writer.write(
        (
            f"POST /agent/v1/tasks/claim HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode()
        + body
    )
    await writer.drain()
    async with asyncio.timeout(3):
        while True:
            async with app.state.db.read() as session:
                current = await session.scalar(select(Coordination.revision))
            if current > revision:
                break
            await asyncio.sleep(0.01)
    handlers = list(http_server.server.server_state.tasks)
    assert handlers
    writer.close()
    await writer.wait_closed()
    await asyncio.wait_for(asyncio.gather(*handlers), timeout=2)
    assert all(
        task.done() and not task.cancelled() and task.exception() is None for task in handlers
    )


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
@pytest.mark.parametrize("control_task", ["cancel", "destroy"], indirect=True)
async def test_cleanup_acknowledgement_remains_possible_after_isolate_expiry(
    agent_api, control_task, clock
):
    """Expired isolate lifetime forbids new work but still permits acknowledging its pending stop and destroy operations."""
    clock.now += timedelta(seconds=11)
    response = await agent_api.post(f"/agent/v1/tasks/{control_task['id']}/ack")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{control_task['id']}/complete", json={})
    ).status_code == 200


@pytest.mark.parametrize(
    "settings", [{"agent_lease_seconds": 3600, "cancellation_timeout_seconds": 5}], indirect=True
)
async def test_simultaneous_cleanup_timeouts_invalidate_worker_once_without_releasing_reserves(
    api, agent_api, paired_isolates, computer, clock
):
    """When two destroy tasks expire together, failing the worker obsoletes both and the scheduler safely skips the second stale task."""
    for isolate in paired_isolates:
        assert (await api.delete(f"/api/v1/isolates/{isolate['id']}")).status_code == 202
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert len(tasks) == 2 and all(task["kind"] == "destroy" for task in tasks)
    clock.now += timedelta(seconds=5)
    async with asyncio.timeout(3):
        while True:
            states = [
                (await api.get(f"/api/v1/isolates/{isolate['id']}")).json()
                for isolate in paired_isolates
            ]
            if all(row["status"] == "failed" for row in states):
                break
            await asyncio.sleep(0.01)
    assert all(row["error"] == "cleanup_unconfirmed" and row["reserved"] for row in states)
    response = await api.get(f"/api/v1/computers/{computer['id']}")
    assert response.json()["online"] is False
    assert response.json()["reserved_cpu"] == 2000 and response.json()["reserved_memory"] == 2048
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
