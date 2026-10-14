"""Session fencing, durable delivery, heartbeats and worker isolation over HTTP."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_session_handshake_retries_cannot_clean_up_live_work(
    api, agent_api, agent, clock, running_command, computer
):
    """Retrying the same handshake preserves active work and never extends its original lease."""
    clock.now += timedelta(seconds=5)
    response = await agent_api.post(
        "/agent/v1/session", headers={"Idempotency-Key": agent["key"]}, json=agent["handshake"]
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == agent["session"]["session_id"]
    assert response.json()["lease_expires_at"] == agent["session"]["lease_expires_at"]
    assert (await api.get(f"/api/v1/commands/{running_command['command']['id']}")).json()[
        "status"
    ] == "running"
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000
    response = await agent_api.post(
        "/agent/v1/session", headers={"Idempotency-Key": str(uuid4())}, json=agent["handshake"]
    )
    assert response.status_code == 409


async def test_new_session_fences_old_tasks_and_handshakes(
    api, agent_api, agent, running_command, computer
):
    """A confirmed clean restart releases capacity, fails old work and rejects old-generation messages."""
    response = await agent_api.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": "new-generation"},
        json={"clean_start": True, "previous_session_id": agent["session"]["session_id"]},
    )
    assert response.status_code == 200
    current = response.json()
    assert current["session_id"] != agent["session"]["session_id"]
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
    assert (
        await agent_api.post(
            "/agent/v1/session", headers={"Idempotency-Key": agent["key"]}, json=agent["handshake"]
        )
    ).status_code == 409
    assert (
        await agent_api.post(
            f"/agent/v1/tasks/{running_command['task']['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 409
    agent_api.headers["X-Agent-Session"] = current["session_id"]
    assert (
        await agent_api.get(f"/agent/v1/tasks/{running_command['task']['id']}")
    ).status_code == 409
    assert (await api.get(f"/api/v1/commands/{running_command['command']['id']}/result")).json()[
        "status"
    ] == "isolate_failed"
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 0
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 200


async def test_heartbeat_renews_lease_but_claim_does_not(agent_api, agent, clock):
    """Only heartbeats renew the lease; current-session reads and task polling do not."""
    clock.now += timedelta(seconds=10)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200
    assert response.json()["lease_expires_at"] == agent["session"]["lease_expires_at"]
    current = (await agent_api.get("/agent/v1/session")).json()
    assert current["active"] and current["session_id"] == agent["session"]["session_id"]
    response = await agent_api.post("/agent/v1/heartbeat")
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["lease_expires_at"]) == clock.now + timedelta(
        seconds=60
    )


async def test_expired_worker_fails_commands_without_freeing_unconfirmed_capacity(
    api, agent_api, agent, clock, running_command, computer
):
    """Losing a lease quarantines capacity and results stay failed until an explicit clean restart."""
    clock.now += timedelta(seconds=61)
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
    current = (await agent_api.get("/agent/v1/session")).json()
    assert not current["active"] and current["session_id"] == agent["session"]["session_id"]
    for _ in range(100):
        response = await api.get(f"/api/v1/commands/{running_command['command']['id']}/result")
        if response.json()["result"] is not None:
            break
        await asyncio.sleep(0.01)
    assert response.json()["status"] == "isolate_failed"
    assert response.json()["result"]["error"] == "agent_lease_expired"
    state = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert not state["online"] and state["reserved_cpu"] == 1000
    assert (
        await agent_api.post(
            "/agent/v1/session", headers={"Idempotency-Key": agent["key"]}, json=agent["handshake"]
        )
    ).status_code == 409
    response = await agent_api.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": "recover"},
        json={"clean_start": True, "previous_session_id": current["session_id"]},
    )
    assert response.status_code == 200
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 0
    assert (await api.get(f"/api/v1/commands/{running_command['command']['id']}/result")).json()[
        "status"
    ] == "isolate_failed"


async def test_worker_and_user_credentials_are_not_interchangeable(client, api, agent, user):
    """User tokens cannot act as workers and worker secrets cannot read user resources."""
    assert (await api.get("/agent/v1/session")).status_code == 401
    assert (await client.get("/api/v1/users/me", headers=agent["headers"])).status_code == 401
    credential_only = {"Authorization": agent["headers"]["Authorization"]}
    assert (await client.get("/agent/v1/session", headers=credential_only)).status_code == 200
    assert (
        await client.post(
            "/agent/v1/tasks/claim", headers=credential_only, json={"wait_seconds": 0}
        )
    ).status_code == 409


@pytest.mark.parametrize(
    "payload", [{"clean_start": False}, {"clean_start": True, "protocol_version": 2}, {}]
)
async def test_handshake_requires_cleanup_and_supported_protocol(agent_api, payload):
    """Handshake rejects unsupported protocols and a missing or false clean-start assertion."""
    assert (
        await agent_api.post(
            "/agent/v1/session", headers={"Idempotency-Key": "invalid"}, json=payload
        )
    ).status_code == 422


async def test_command_idempotency_redelivery_and_completion_replay(api, agent_api, isolate):
    """Concurrent submissions and repeated deliveries use stable IDs, while conflicting reports fail."""
    path = f"/api/v1/isolates/{isolate['id']}/commands"
    headers, body = {"Idempotency-Key": "one-command"}, {"command": "pytest"}
    first, duplicate = await asyncio.gather(
        api.post(path, headers=headers, json=body), api.post(path, headers=headers, json=body)
    )
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    assert (await api.post(path, headers=headers, json={"command": "other"})).status_code == 409
    task = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ][0]
    repeated = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ][0]
    assert repeated["id"] == task["id"] and repeated["delivered_at"] == task["delivered_at"]
    completion = {"returncode": 0, "stdout": "done"}
    response = await agent_api.post(f"/agent/v1/tasks/{task['id']}/complete", json=completion)
    assert response.status_code == 200
    replay = await agent_api.post(f"/agent/v1/tasks/{task['id']}/complete", json=completion)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert (
        await agent_api.post(f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 7})
    ).status_code == 409
    assert (await agent_api.post(f"/agent/v1/tasks/{task['id']}/ack")).status_code == 409
    assert (await agent_api.post(f"/agent/v1/tasks/{task['id']}/started")).status_code == 409
    assert (await api.get(f"/api/v1/commands/{first.json()['id']}/result")).json()["result"][
        "stdout"
    ] == "done"


async def test_ack_and_started_retries_preserve_times(agent_api, api, clock, command, run_task):
    """Repeated acknowledgements and started notifications retain the original timestamps and deadline."""
    path = f"/agent/v1/tasks/{run_task['id']}"
    acknowledged = await agent_api.post(path + "/ack")
    assert acknowledged.status_code == 200
    clock.now += timedelta(seconds=1)
    repeated = await agent_api.post(path + "/ack")
    assert repeated.json()["accepted_at"] == acknowledged.json()["accepted_at"]
    started = await agent_api.post(path + "/started")
    assert started.status_code == 200
    first = (await api.get(f"/api/v1/commands/{command['id']}")).json()
    clock.now += timedelta(seconds=1)
    repeated = await agent_api.post(path + "/started")
    assert repeated.json()["deadline"] == started.json()["deadline"]
    assert (await api.get(f"/api/v1/commands/{command['id']}")).json()["started_at"] == first[
        "started_at"
    ]
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ] == []
    assert (await agent_api.get(path)).json()["status"] == "accepted"


async def test_long_poll_does_not_block_heartbeat_or_command_dispatch(api, agent_api, isolate):
    """A waiting claim allows concurrent heartbeat and command submission, then returns the new task."""
    # Bound responsiveness separately from the poll's idle timeout, including under load.
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        pending = group.create_task(
            agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 25})
        )
        await asyncio.sleep(0.03)
        assert not pending.done()
        assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 200
        response = await api.post(
            f"/api/v1/isolates/{isolate['id']}/commands",
            headers={"Idempotency-Key": "wakeup"},
            json={"command": "pytest"},
        )
        assert response.status_code == 202
    assert pending.result().status_code == 200
    assert pending.result().json()["tasks"][0]["command_id"] == response.json()["id"]


async def test_foreign_worker_cannot_read_or_finish_assignments(
    client, other_agent, run_task, isolate
):
    """A worker cannot inspect another worker's task, upload output or fetch its initial state."""
    headers = other_agent["headers"]
    path = f"/agent/v1/tasks/{run_task['id']}"
    assert (await client.get(path, headers=headers)).status_code == 404
    for suffix in ["ack", "started", "complete"]:
        assert (
            await client.post(path + "/" + suffix, headers=headers, json={"returncode": 0})
        ).status_code == 404
    assert (
        await client.put(path + "/artifacts/stdout", headers=headers, content=b"forged")
    ).status_code == 404
    assert (
        await client.get(
            f"/agent/v1/artifacts/{isolate['state_artifact_id']}/content", headers=headers
        )
    ).status_code == 404


async def test_unknown_worker_task_and_operation_return_not_found(agent_api, api):
    """Unknown task and operation UUIDs return stable not-found responses."""
    key = str(uuid4())
    assert (await agent_api.get(f"/agent/v1/tasks/{key}")).status_code == 404
    assert (await api.get(f"/api/v1/operations/{key}")).status_code == 404


@pytest.mark.parametrize("key", [None, "", "x" * 129])
async def test_idempotency_keys_are_required_and_bounded(api, agent_api, isolate, agent, key):
    """Creation, snapshot and handshake endpoints reject missing, empty or oversized idempotency keys."""
    headers = {"Idempotency-Key": key} if key is not None else {}
    assert (
        await api.post(
            f"/api/v1/isolates/{isolate['id']}/commands",
            headers=headers,
            json={"command": "pytest"},
        )
    ).status_code == 422
    assert (
        await api.post(f"/api/v1/isolates/{isolate['id']}/snapshots", headers=headers)
    ).status_code == 422
    assert (
        await agent_api.post("/agent/v1/session", headers=headers, json=agent["handshake"])
    ).status_code == 422
