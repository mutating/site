"""Persistence and coordination across independently running HTTP servers."""

import asyncio
from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio


async def test_api_shutdown_preserves_delivered_commands_and_results(
    api, agent, command, run_task, user, http_server, secondary_client
):
    """After one server stops, another instance reconciles the same task ID and persists its result."""
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    response = await secondary_client.post(
        "/agent/v1/tasks/claim", headers=agent["headers"], json={"wait_seconds": 0}
    )
    assert response.status_code == 200 and response.json()["tasks"][0]["id"] == run_task["id"]
    response = await secondary_client.post(
        f"/agent/v1/tasks/{run_task['id']}/started", headers=agent["headers"]
    )
    assert response.status_code == 200
    response = await secondary_client.post(
        f"/agent/v1/tasks/{run_task['id']}/complete",
        headers=agent["headers"],
        json={"returncode": 0, "stdout": "survived restart"},
    )
    assert response.status_code == 200
    result = await secondary_client.get(
        f"/v1/commands/{command['id']}/result", headers=user["headers"]
    )
    assert result.status_code == 200 and result.json()["result"]["stdout"] == "survived restart"
    assert (
        await secondary_client.post(
            f"/agent/v1/tasks/{run_task['id']}/complete",
            headers=agent["headers"],
            json={"returncode": 0, "stdout": "survived restart"},
        )
    ).status_code == 200


@pytest.mark.parametrize(
    "computer", [{"cpu_millis": 1000, "memory_bytes": 1024, "disk_bytes": 4096}], indirect=True
)
async def test_two_controllers_cannot_overreserve_capacity(
    api, agent_api, computer, isolate_payload, secondary_client, user, clock
):
    """Concurrent API instances reserve a worker once and allocate the next isolate only after cleanup."""
    responses = await asyncio.gather(
        api.post("/v1/isolates", headers={"Idempotency-Key": "first"}, json=isolate_payload),
        secondary_client.post(
            "/v1/isolates",
            headers={**user["headers"], "Idempotency-Key": "second"},
            json=isolate_payload,
        ),
    )
    assert all(response.status_code == 202 for response in responses)
    ids = {response.json()["id"] for response in responses}
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] in ids
    first = tasks[0]
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000
    assert (
        await agent_api.post(f"/agent/v1/tasks/{first['id']}/complete", json={})
    ).status_code == 200
    assert (await api.delete(f"/v1/isolates/{first['isolate_id']}")).status_code == 202
    destroy = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ][0]
    assert destroy["kind"] == "destroy"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
    ).status_code == 200
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] in ids - {first["isolate_id"]}
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000
    response = await api.get(
        f"/v1/users/{user['id']}/resources/history",
        params={
            "from": (clock.now - timedelta(seconds=1)).isoformat(),
            "to": (clock.now + timedelta(seconds=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == 4
    assert [{row["code"]: row["occupied"] for row in point["resources"]} for point in points] == [
        {"cpu": 0, "memory": 0},
        {"cpu": 1000, "memory": 1024},
        {"cpu": 0, "memory": 0},
        {"cpu": 1000, "memory": 1024},
    ]


async def test_health_and_openapi_expose_public_contracts(client):
    """Real server health and OpenAPI describe typed results without publishing secret storage fields."""
    assert (await client.get("/health/live")).json()["status"] == "ok"
    for _ in range(100):
        response = await client.get("/health/ready")
        if response.status_code == 200:
            break
        await asyncio.sleep(0.01)
    assert response.json() == {"status": "ready"}
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    result = schema["paths"]["/v1/commands/results"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert result["items"]["$ref"].endswith("/ResultEnvelope")
    assert "password_hash" not in schema["components"]["schemas"]["UserOut"]["properties"]
    assert "token_hash" not in schema["components"]["schemas"]["AccessToken"]["properties"]
    assert "storage_key" not in schema["components"]["schemas"]["Artifact"]["properties"]
    assert (await client.get("/docs")).status_code == 200


async def test_cli_migrates_and_serves_a_working_public_api(cli_server):
    """The installed CLI creates its database, starts the embedded controller and serves usable account endpoints."""
    assert (await cli_server.get("/health/live")).status_code == 200
    assert (await cli_server.get("/health/ready")).status_code == 200
    response = await cli_server.post(
        "/v1/users",
        json={
            "email": "cli@example.com",
            "password": "CLI integration password",
            "first_name": "CLI",
            "last_name": "User",
            "nickname": "cli",
        },
    )
    assert response.status_code == 201
    response = await cli_server.post(
        "/v1/auth/login",
        json={"email": "cli@example.com", "password": "CLI integration password"},
    )
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert (await cli_server.get("/v1/users/me", headers=headers)).json()["nickname"] == "cli"


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_explicitly_disabled_controller_keeps_api_ready_without_dispatch(
    client, agent_api, provisioning_isolate
):
    """The API can run with its controller disabled without reporting false scheduler failures or dispatching work."""
    assert (await client.get("/health/ready")).status_code == 200
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})
    assert response.status_code == 200 and response.json()["tasks"] == []
