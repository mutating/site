"""Computer inventory, ownership, capacity and enrollment exercised through HTTP."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_computer_crud_and_ownership_lifecycle(
    api, other_api, computer, company, user, agent_api
):
    """Update a computer, transfer and detach ownership, reattach it and remove the idle machine."""
    path = f"/v1/computers/{computer['id']}"
    owner_path = f"/v1/computer-ownerships/{computer['ownership']['id']}"
    assert (await api.get(path)).status_code == 200
    assert (await api.get("/v1/computers")).json()[0]["id"] == computer["id"]
    assert (await api.get(owner_path)).json() == computer["ownership"]
    assert (await api.get("/v1/computer-ownerships")).json() == [computer["ownership"]]
    response = await api.patch(
        path,
        json={
            "name": None,
            "enabled": False,
            "cpu_millis": 4000,
            "memory_bytes": 4096,
            "disk_bytes": 16384,
        },
    )
    assert response.status_code == 200 and response.json()["name"] is None
    assert response.json()["cpu_millis"] == 4000 and not response.json()["enabled"]
    response = await api.patch(owner_path, json={"company_id": company["id"]})
    assert response.status_code == 200 and response.json()["user_id"] is None
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 401
    assert (await api.delete(owner_path)).status_code == 204
    assert (await api.get("/v1/computers")).json() == []
    assert (await api.get(path)).status_code == 200
    assert (await other_api.get(path)).status_code == 404
    assert (await api.post(path + "/credentials")).status_code == 409
    response = await api.post(
        "/v1/computer-ownerships", json={"computer_id": computer["id"], "user_id": user["id"]}
    )
    assert response.status_code == 201
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    assert (await api.get(f"/v1/computer-ownerships/{response.json()['id']}")).status_code == 404


@pytest.mark.parametrize("field", ["enabled", "cpu_millis", "memory_bytes", "disk_bytes"])
async def test_nullable_patch_fields_do_not_erase_required_capacity(api, computer, field):
    """Only a computer's optional name accepts null; required capacity and enabled state remain intact."""
    path = f"/v1/computers/{computer['id']}"
    assert (await api.patch(path, json={field: None})).status_code == 422
    assert (await api.get(path)).json()[field] == computer[field]


async def test_busy_computer_cannot_lose_owner_or_reserved_capacity(
    api, agent_api, computer, isolate, company
):
    """Rejected ownership, deletion and capacity changes preserve the active worker and reservation."""
    path = f"/v1/computers/{computer['id']}"
    owner_path = f"/v1/computer-ownerships/{computer['ownership']['id']}"
    for field in ["cpu_millis", "memory_bytes", "disk_bytes"]:
        assert (await api.patch(path, json={field: 1})).status_code == 409
    assert (await api.delete(path)).status_code == 409
    assert (await api.delete(owner_path)).status_code == 409
    assert (await api.patch(owner_path, json={"company_id": company["id"]})).status_code == 409
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 200
    assert (await api.get(path)).json()["reserved_cpu"] == 1000
    assert (await api.get(owner_path)).json() == computer["ownership"]


async def test_credential_rotation_retains_capacity_until_full_clean_start(
    api, client, agent_api, computer, running_command
):
    """Credential rotation revokes the old agent and fails work without releasing uncleaned sandboxes."""
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 401
    current = (await client.get("/agent/v1/session", headers=headers)).json()
    assert current["session_id"] is None and not current["active"]
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000
    result = (await api.get(f"/v1/commands/{running_command['command']['id']}/result")).json()
    assert result["status"] == "isolate_failed"
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": "rotated-start"},
        json={"clean_start": True, "previous_session_id": None},
    )
    assert response.status_code == 200
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 0


async def test_duplicate_and_invalid_computer_ownership_is_rejected(
    api, computer, user, company, other_user
):
    """Computer ownership enforces uniqueness, exactly one owner and authorization of the target owner."""
    base = {"computer_id": computer["id"]}
    assert (
        await api.post("/v1/computer-ownerships", json={**base, "user_id": user["id"]})
    ).status_code == 409
    for payload in [base, {**base, "user_id": user["id"], "company_id": company["id"]}]:
        assert (await api.post("/v1/computer-ownerships", json=payload)).status_code == 422
    path = f"/v1/computer-ownerships/{computer['ownership']['id']}"
    assert (await api.patch(path, json={"user_id": other_user["id"]})).status_code == 404
    assert (await api.get(path)).json() == computer["ownership"]


async def test_company_deletion_failure_preserves_memberships_and_tokens(
    api, client, company, company_token
):
    """A company referenced by a computer cannot be deleted and rollback preserves its members and tokens."""
    response = await api.post(
        "/v1/computers",
        json={
            "company_id": company["id"],
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 201
    assert (await api.delete(f"/v1/companies/{company['id']}")).status_code == 409
    assert len((await api.get("/v1/memberships", params={"company_id": company["id"]})).json()) == 1
    headers = {"Authorization": "Bearer " + company_token["token"]}
    assert (await client.get(f"/v1/companies/{company['id']}", headers=headers)).status_code == 200


@pytest.mark.parametrize(
    "computer",
    [
        {"architecture": "arm64"},
        {"os": "windows"},
        {"runtime": "other"},
        {"cpu_millis": 500},
        {"memory_bytes": 512},
        {"disk_bytes": 2048},
    ],
    indirect=True,
)
async def test_scheduler_requires_matching_capabilities_and_sufficient_capacity(
    api, agent_api, computer, provisioning_isolate
):
    """No assignment is sent to a computer with incompatible capabilities or insufficient resources."""
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})
    assert response.status_code == 200 and response.json()["tasks"] == []
    state = (await api.get(f"/v1/isolates/{provisioning_isolate['id']}")).json()
    assert state["status"] == "provisioning" and state["computer_id"] is None
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 0


async def test_disabled_worker_finishes_work_but_receives_no_new_isolates(
    api, agent_api, computer, isolate, isolate_payload, run_task
):
    """Disabling a worker stops new allocation while still allowing current results and cleanup."""
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": False})
    ).status_code == 200
    assert (
        await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0})
    ).status_code == 200
    response = await api.post(
        "/v1/isolates", headers={"Idempotency-Key": "waiting"}, json=isolate_payload
    )
    assert response.status_code == 202
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})).json()[
        "tasks"
    ] == []
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": True})
    ).status_code == 200
    task = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ][0]
    assert task["isolate_id"] == response.json()["id"]


async def test_other_owner_worker_does_not_join_personal_pool(
    client, api, other_agent, provisioning_isolate
):
    """An otherwise suitable computer owned by another user cannot claim a personal isolate."""
    response = await client.post(
        "/agent/v1/tasks/claim", headers=other_agent["headers"], json={"wait_seconds": 0.05}
    )
    assert response.status_code == 200 and response.json()["tasks"] == []
    assert (await api.get(f"/v1/isolates/{provisioning_isolate['id']}")).json()[
        "computer_id"
    ] is None


async def test_unknown_computer_resources_return_not_found(api):
    """Unknown computers and ownership IDs return 404 for inventory and credential operations."""
    key = str(uuid4())
    for path in [f"/v1/computers/{key}", f"/v1/computer-ownerships/{key}"]:
        assert (await api.get(path)).status_code == 404
    assert (await api.post(f"/v1/computers/{key}/credentials")).status_code == 404
