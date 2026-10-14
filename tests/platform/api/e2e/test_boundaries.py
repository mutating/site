"""Validation and tenant boundaries for public command and inventory endpoints."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "payload",
    [
        {"command": ""},
        {"command": " \n "},
        {"command": "echo\x00bad"},
        {"command": "x" * 65537},
        {"cwd": "/etc"},
        {"cwd": "../outside"},
        {"cwd": "a/../../b"},
        {"cwd": "C:drive"},
        {"cwd": "a\\b"},
        {"cwd": "\x00"},
        {"env": {"": "value"}},
        {"env": {"A=B": "x"}},
        {"env": {"A\x00": "x"}},
        {"env": {"A": "\x00"}},
        {"env": {"A": "x" * 8193}},
        {"env": {"A" * 201: "x"}},
        {"env": {str(i): "x" for i in range(101)}},
        {"timeout_seconds": 0},
        {"timeout_seconds": 86401},
        {"unexpected": True},
    ],
)
async def test_invalid_commands_never_enter_the_queue(api, agent_api, isolate, payload):
    """Malformed commands, escaping paths, invalid environments and unsupported fields never reach a worker."""
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest", **payload},
    )
    assert response.status_code == 422
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"returncode": 2**31},
        {"returncode": -(2**31) - 1},
        {"stdout": "x" * 1_048_577},
        {"stderr": "x" * 1_048_577},
        {"error": "x" * 8193},
        {"outcome": "unknown"},
        {"unexpected": True},
    ],
)
async def test_invalid_results_do_not_consume_the_task(agent_api, run_task, payload):
    """Invalid result data is rejected without preventing a later valid completion of the same task."""
    path = f"/agent/v1/tasks/{run_task['id']}/complete"
    assert (await agent_api.post(path, json={"returncode": 0, **payload})).status_code == 422
    assert (await agent_api.post(path, json={"returncode": 0})).status_code == 200


@pytest.mark.parametrize("endpoint", ["statuses", "results", "cancel"])
@pytest.mark.parametrize(
    "ids", [[], ["not-a-uuid"], ["00000000-0000-0000-0000-000000000000"] * 201]
)
async def test_batch_command_limits_are_enforced(api, endpoint, ids):
    """Batch endpoints require one to two hundred valid command UUIDs."""
    assert (await api.post(f"/api/v1/commands/{endpoint}", json={"ids": ids})).status_code == 422


async def test_foreign_resources_are_hidden_from_reads_writes_and_lists(
    other_api, user, computer, isolate, run_task, command, token_ownership, access_token, company
):
    """A different tenant cannot read, mutate or enumerate any of another user's owned resources."""
    resources = {
        "computers": computer["id"],
        "computer-ownerships": computer["ownership"]["id"],
        "isolates": isolate["id"],
        "artifacts": isolate["state_artifact_id"],
        "token-ownerships": token_ownership["id"],
        "access-tokens": access_token["id"],
        "companies": company["id"],
    }
    for collection, key in resources.items():
        assert (await other_api.get(f"/api/v1/{collection}/{key}")).status_code == 404
        rows = (await other_api.get(f"/api/v1/{collection}")).json()
        assert all(row["id"] != key for row in rows)
    for path in [
        f"/api/v1/commands/{command['id']}",
        f"/api/v1/commands/{command['id']}/result",
        f"/api/v1/operations/{run_task['id']}",
        f"/api/v1/artifacts/{isolate['state_artifact_id']}/content",
    ]:
        assert (await other_api.get(path)).status_code == 404
    assert (await other_api.delete(f"/api/v1/isolates/{isolate['id']}")).status_code == 404
    assert (
        await other_api.delete(f"/api/v1/artifacts/{isolate['state_artifact_id']}")
    ).status_code == 404
    assert (
        await other_api.patch(f"/api/v1/computers/{computer['id']}", json={"enabled": False})
    ).status_code == 404
    assert (
        await other_api.post(f"/api/v1/computers/{computer['id']}/credentials")
    ).status_code == 404
    assert (
        await other_api.post("/api/v1/token-ownerships", json={"user_id": user["id"]})
    ).status_code == 404


@pytest.mark.parametrize("endpoint", ["statuses", "results", "cancel"])
async def test_mixed_owner_batches_fail_atomically(api, command, foreign_command, endpoint):
    """A batch containing a foreign command fails as a whole, including cancellation of owned commands."""
    response = await api.post(
        f"/api/v1/commands/{endpoint}", json={"ids": [command["id"], foreign_command["id"]]}
    )
    assert response.status_code == 404
    own = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert own["status"] in {"queued", "dispatched"} and own["result"] is None


@pytest.mark.parametrize("access_token", [{"scopes": ["read"]}], indirect=True)
async def test_read_only_token_cannot_submit_cancel_upload_or_manage(
    client, access_token, isolate, command, company, computer
):
    """Read-only access permits inspection but rejects every execution or management mutation."""
    client.headers["Authorization"] = "Bearer " + access_token["token"]
    assert (await client.get(f"/api/v1/isolates/{isolate['id']}")).status_code == 200
    assert (
        await client.post(
            f"/api/v1/isolates/{isolate['id']}/commands",
            headers={"Idempotency-Key": "read-only"},
            json={"command": "pytest"},
        )
    ).status_code == 403
    assert (await client.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 403
    assert (await client.post("/api/v1/artifacts", content=b"state")).status_code == 403
    assert (await client.delete(f"/api/v1/isolates/{isolate['id']}")).status_code == 403
    assert (
        await client.patch(f"/api/v1/companies/{company['id']}", json={"name": "hacked"})
    ).status_code == 403
    assert (await client.post(f"/api/v1/computers/{computer['id']}/credentials")).status_code == 403


async def test_company_member_executes_only_on_company_pool(
    api, other_api, client, company, membership, company_agent
):
    """An ordinary member may execute on the company pool while lacking computer administration rights."""
    response = await other_api.post(
        "/api/v1/artifacts", params={"company_id": company["id"]}, content=b"company state"
    )
    assert response.status_code == 201
    artifact = response.json()
    assert artifact["user_id"] is None and artifact["company_id"] == company["id"]
    response = await other_api.post(
        "/api/v1/isolates",
        headers={"Idempotency-Key": "company-isolate"},
        json={
            "company_id": company["id"],
            "state_artifact_id": artifact["id"],
            "image": "python:3.12",
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 202
    isolate = response.json()
    headers = company_agent["headers"]
    tasks = (
        await client.post("/agent/v1/tasks/claim", headers=headers, json={"wait_seconds": 2})
    ).json()["tasks"]
    assert tasks[0]["isolate_id"] == isolate["id"]
    assert (
        await client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", headers=headers, json={})
    ).status_code == 200
    response = await other_api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": "company-command"},
        json={"command": "pytest"},
    )
    assert response.status_code == 202
    command = response.json()
    run = (
        await client.post("/agent/v1/tasks/claim", headers=headers, json={"wait_seconds": 2})
    ).json()["tasks"][0]
    assert (
        await client.post(
            f"/agent/v1/tasks/{run['id']}/complete", headers=headers, json={"returncode": 0}
        )
    ).status_code == 200
    assert (await other_api.get(f"/api/v1/commands/{command['id']}/result")).json()["result"][
        "success"
    ]
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).status_code == 200
    assert (
        await other_api.patch(
            f"/api/v1/computers/{company_agent['computer']['id']}", json={"enabled": False}
        )
    ).status_code == 403
    assert (await api.delete(f"/api/v1/memberships/{membership['id']}")).status_code == 204
    assert (await other_api.get(f"/api/v1/commands/{command['id']}/result")).status_code == 404
    assert (await other_api.get("/api/v1/isolates")).json() == []


@pytest.mark.parametrize(
    "collection",
    [
        "companies",
        "computers",
        "computer-ownerships",
        "token-ownerships",
        "access-tokens",
        "artifacts",
        "isolates",
    ],
)
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
async def test_collection_pagination_rejects_invalid_bounds(api, collection, params):
    """Inventory endpoints reject negative offsets and limits outside the documented range."""
    assert (await api.get(f"/api/v1/{collection}", params=params)).status_code == 422


@pytest.mark.parametrize(
    "collection", ["computers", "isolates", "artifacts", "commands", "operations"]
)
async def test_invalid_and_unknown_resource_ids_have_stable_http_errors(api, collection):
    """Malformed UUIDs produce 422, while valid but unknown resource IDs produce 404."""
    assert (await api.get(f"/api/v1/{collection}/not-a-uuid")).status_code == 422
    assert (await api.get(f"/api/v1/{collection}/{uuid4()}")).status_code == 404


async def test_corporate_token_defaults_resources_to_its_own_company(
    client, api, company, company_token, artifact
):
    """Company tokens create and list only corporate resources when no explicit owner is supplied."""
    client.headers["Authorization"] = "Bearer " + company_token["token"]
    response = await client.post("/api/v1/artifacts", content=b"corporate state")
    assert response.status_code == 201
    corporate = response.json()
    assert corporate["company_id"] == company["id"] and corporate["user_id"] is None
    assert (await client.get("/api/v1/artifacts")).json() == [corporate]
    assert (await client.get(f"/api/v1/artifacts/{artifact['id']}")).status_code == 404
    response = await client.post(
        "/api/v1/computers",
        json={
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 201
    assert response.json()["ownership"]["company_id"] == company["id"]
    assert response.json()["ownership"]["user_id"] is None
    response = await client.post(
        "/api/v1/isolates",
        headers={"Idempotency-Key": "corporate-default"},
        json={
            "state_artifact_id": corporate["id"],
            "image": "python:3.12",
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 202
    assert response.json()["company_id"] == company["id"] and response.json()["user_id"] is None
    assert (await api.get(f"/api/v1/isolates/{response.json()['id']}")).status_code == 200
