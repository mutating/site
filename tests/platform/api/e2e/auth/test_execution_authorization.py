"""Ownership and live membership control the entire path from an API request to worker delivery."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("POST", "/commands"),
        ("POST", "/renew"),
        ("POST", "/snapshots"),
        ("DELETE", ""),
    ],
)
async def test_missing_and_foreign_isolates_have_indistinguishable_errors(
    other_api, isolate, company_isolate, method, suffix
):
    """Hide foreign resource existence by returning identical 404 responses.

    A nonexistent UUID, another user's isolate and an inaccessible company's
    isolate must have the same status and body. Returning 'Company not found'
    only for the last case would let callers discover an isolate's existence
    and ownership type, despite the shared 404 status intended to conceal it.
    """
    responses = []
    for identifier in [str(uuid4()), isolate["id"], company_isolate["id"]]:
        kwargs = {"headers": {"Idempotency-Key": str(uuid4())}}
        if suffix == "/commands":
            kwargs["json"] = {"command": "pytest -q"}
        response = await other_api.request(
            method, f"/api/v1/isolates/{identifier}{suffix}", **kwargs
        )
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "Resource not found"}
        responses.append(response)
    assert all(response.content == responses[0].content for response in responses)
    assert all(
        response.headers["content-type"] == responses[0].headers["content-type"]
        and response.headers["content-length"] == responses[0].headers["content-length"]
        for response in responses
    )


async def test_command_and_operation_errors_do_not_reveal_foreign_resource_existence(
    other_api, command, run_task
):
    """Conceal foreign commands and operations even when access checks traverse their parent isolate.

    Different messages for an unknown command and a known but forbidden command
    would reveal valid resource IDs. Single-resource and batch endpoints must
    therefore return the same generic 404 body for both cases.
    """
    for method, template, existing_id in [
        ("GET", "/api/v1/commands/{id}", command["id"]),
        ("GET", "/api/v1/commands/{id}/result", command["id"]),
        ("POST", "/api/v1/commands/{id}/cancel", command["id"]),
        ("GET", "/api/v1/operations/{id}", run_task["id"]),
        ("POST", "/api/v1/commands/statuses", command["id"]),
        ("POST", "/api/v1/commands/results", command["id"]),
        ("POST", "/api/v1/commands/cancel", command["id"]),
    ]:
        responses = []
        for identifier in [str(uuid4()), existing_id]:
            kwargs = {} if "{id}" in template else {"json": {"ids": [identifier]}}
            response = await other_api.request(method, template.format(id=identifier), **kwargs)
            assert response.status_code == 404, (method, template, response.text)
            assert response.json() == {"detail": "Resource not found"}
            responses.append(response)
        assert responses[0].content == responses[1].content


@pytest.mark.parametrize("pool", ["personal", "company"])
async def test_outsider_cannot_submit_or_replay_commands_in_foreign_isolate(
    api, other_api, client, isolate, company_isolate, agent, company_agent, pool
):
    """Knowing an isolate ID and a successful request's retry key grants no execution rights."""
    target, worker = (isolate, agent) if pool == "personal" else (company_isolate, company_agent)
    path = f"/api/v1/isolates/{target['id']}"
    key = str(uuid4())
    payload = {"command": "pytest -q"}
    response = await api.post(path + "/commands", headers={"Idempotency-Key": key}, json=payload)
    assert response.status_code == 202, response.text
    command = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=worker["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["command_id"] == command["id"], tasks
    response = await client.post(
        f"/agent/v1/tasks/{tasks[0]['id']}/complete",
        headers=worker["headers"],
        json={"returncode": 0},
    )
    assert response.status_code == 200, response.text

    for retry_key in [key, str(uuid4())]:
        response = await other_api.post(
            path + "/commands", headers={"Idempotency-Key": retry_key}, json=payload
        )
        assert response.status_code == 404, response.text
    for method, url, kwargs in [
        ("POST", path + "/renew", {}),
        ("POST", path + "/snapshots", {"headers": {"Idempotency-Key": str(uuid4())}}),
        ("DELETE", path, {}),
        ("POST", f"/api/v1/commands/{command['id']}/cancel", {}),
        ("POST", "/api/v1/commands/cancel", {"json": {"ids": [command["id"]]}}),
    ]:
        response = await other_api.request(method, url, **kwargs)
        assert response.status_code == 404, (method, url, response.text)
    response = await api.get(path)
    assert response.status_code == 200
    assert response.json()["status"] == "ready" and response.json()["next_sequence"] == 1
    response = await client.post(
        "/agent/v1/tasks/claim", headers=worker["headers"], json={"wait_seconds": 0.05}
    )
    assert response.status_code == 200 and response.json()["tasks"] == []


@pytest.mark.parametrize("membership", ["user", "admin", "creator"], indirect=True)
async def test_shared_company_does_not_grant_access_to_colleagues_personal_computer(
    other_api, api, agent_api, isolate, computer, membership, other_user
):
    """Even company creators cannot execute on or take over another member's personal computer."""
    response = await other_api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest"},
    )
    assert response.status_code == 404, response.text
    response = await other_api.patch(
        f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"user_id": other_user["id"]},
    )
    assert response.status_code == 404, response.text
    response = await other_api.post(f"/api/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 404, response.text
    response = await api.get(f"/api/v1/isolates/{isolate['id']}")
    assert response.json()["next_sequence"] == 0
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})
    assert response.status_code == 200 and response.json()["tasks"] == []


@pytest.mark.parametrize("membership", ["user", "admin", "creator"], indirect=True)
async def test_each_company_role_can_execute_on_company_owned_computer(
    other_api, client, agent, other_agent, company_isolate, company_agent, membership
):
    """All three membership roles can submit work that reaches the company's worker and returns a result."""
    response = await other_api.post(
        f"/api/v1/isolates/{company_isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest -q"},
    )
    assert response.status_code == 202, response.text
    command = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["command_id"] == command["id"], tasks
    assert tasks[0]["computer_id"] == company_agent["computer"]["id"]
    for worker in [agent, other_agent]:
        response = await client.post(
            "/agent/v1/tasks/claim", headers=worker["headers"], json={"wait_seconds": 0}
        )
        assert response.status_code == 200 and response.json()["tasks"] == []
    response = await client.post(
        f"/agent/v1/tasks/{tasks[0]['id']}/complete",
        headers=company_agent["headers"],
        json={"returncode": 0, "stdout": "tests passed"},
    )
    assert response.status_code == 200, response.text
    response = await other_api.get(f"/api/v1/commands/{command['id']}/result")
    assert response.status_code == 200 and response.json()["result"]["success"] is True
    assert response.json()["result"]["stdout"] == "tests passed"


async def test_outsider_cannot_grant_themself_company_execution_access(
    other_api, api, other_user, company, company_agent, company_token, company_isolate_payload
):
    """An outsider cannot self-enroll, mint a company token, seize its computer or provision its isolate."""
    computer = company_agent["computer"]
    for method, path, kwargs in [
        (
            "POST",
            "/api/v1/memberships",
            {"json": {"user_id": other_user["id"], "company_id": company["id"], "role": "creator"}},
        ),
        ("POST", "/api/v1/token-ownerships", {"json": {"company_id": company["id"]}}),
        (
            "POST",
            "/api/v1/access-tokens",
            {
                "json": {
                    "ownership_id": company_token["ownership_id"],
                    "name": "Forged",
                    "scopes": ["execute"],
                }
            },
        ),
        (
            "PATCH",
            f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
            {"json": {"user_id": other_user["id"]}},
        ),
        (
            "POST",
            "/api/v1/computer-ownerships",
            {"json": {"computer_id": computer["id"], "user_id": other_user["id"]}},
        ),
        ("POST", f"/api/v1/computers/{computer['id']}/credentials", {}),
        (
            "POST",
            "/api/v1/artifacts",
            {"params": {"company_id": company["id"]}, "content": b"intruder state"},
        ),
        (
            "POST",
            "/api/v1/isolates",
            {"headers": {"Idempotency-Key": str(uuid4())}, "json": company_isolate_payload},
        ),
    ]:
        response = await other_api.request(method, path, **kwargs)
        assert response.status_code == 404, (method, path, response.text)
    response = await api.get("/api/v1/memberships", params={"company_id": company["id"]})
    assert response.status_code == 200
    assert all(row["user_id"] != other_user["id"] for row in response.json())
    response = await api.get(f"/api/v1/computer-ownerships/{computer['ownership']['id']}")
    assert response.status_code == 200 and response.json() == computer["ownership"]
    assert (await api.get("/api/v1/isolates")).json() == []


@pytest.mark.parametrize("pool", ["personal", "company"])
async def test_scheduler_never_borrows_capacity_from_another_owner(
    api,
    client,
    agent,
    other_agent,
    company_agent,
    other_company_agent,
    membership,
    isolate_payload,
    company_isolate_payload,
    pool,
):
    """Unavailable authorized capacity never falls back to another person or company, even with shared membership."""
    worker = agent if pool == "personal" else company_agent
    payload = isolate_payload if pool == "personal" else company_isolate_payload
    foreign_workers = [
        candidate
        for candidate in [agent, other_agent, company_agent, other_company_agent]
        if candidate is not worker
    ]
    computer_path = f"/api/v1/computers/{worker['computer']['id']}"
    response = await api.patch(computer_path, json={"enabled": False})
    assert response.status_code == 200, response.text
    response = await api.post(
        "/api/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 202, response.text
    isolate = response.json()
    for candidate in foreign_workers:
        response = await client.post(
            "/agent/v1/tasks/claim", headers=candidate["headers"], json={"wait_seconds": 0.05}
        )
        assert response.status_code == 200 and response.json()["tasks"] == []
    response = await api.get(f"/api/v1/isolates/{isolate['id']}")
    assert response.status_code == 200
    assert response.json()["computer_id"] is None and response.json()["reserved"] is False

    response = await api.patch(computer_path, json={"enabled": True})
    assert response.status_code == 200, response.text
    response = await client.post(
        "/agent/v1/tasks/claim", headers=worker["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] == isolate["id"], tasks
    assert tasks[0]["computer_id"] == worker["computer"]["id"]
    response = await api.get(f"/api/v1/isolates/{isolate['id']}")
    assert response.json()["computer_id"] == worker["computer"]["id"]


@pytest.mark.parametrize("membership", ["user", "admin", "creator"], indirect=True)
async def test_membership_removal_blocks_new_commands_and_previously_authorized_retries(
    api, other_api, client, membership, member_execution, company_isolate_payload, company_agent
):
    """Removing membership immediately forbids both fresh execution and replay using the member's still-valid personal token."""
    response = await api.delete(f"/api/v1/memberships/{membership['id']}")
    assert response.status_code == 204, response.text
    assert (await other_api.get("/api/v1/users/me")).status_code == 200
    path = f"/api/v1/isolates/{member_execution['isolate']['id']}"
    for key in [member_execution["isolate_key"], str(uuid4())]:
        response = await other_api.post(
            "/api/v1/isolates", headers={"Idempotency-Key": key}, json=company_isolate_payload
        )
        assert response.status_code == 404, response.text
    for key in [member_execution["command_key"], str(uuid4())]:
        response = await other_api.post(
            path + "/commands",
            headers={"Idempotency-Key": key},
            json=member_execution["command_payload"],
        )
        assert response.status_code == 404, response.text
    response = await api.get(path)
    assert response.status_code == 200 and response.json()["next_sequence"] == 1
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 0.05}
    )
    assert response.status_code == 200 and response.json()["tasks"] == []
    response = await api.post(
        path + "/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest"},
    )
    assert response.status_code == 202 and response.json()["sequence"] == 2


async def test_company_token_does_not_inherit_issuers_personal_execution_rights(
    client, other_api, company_token, isolate, company_isolate, company_agent
):
    """A delegated company bearer token grants company execution, independently of the caller's personal membership, but no personal rights."""
    response = await other_api.post(
        f"/api/v1/isolates/{company_isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest"},
    )
    assert response.status_code == 404, response.text
    headers = {"Authorization": "Bearer " + company_token["token"], "Idempotency-Key": str(uuid4())}
    response = await client.post(
        f"/api/v1/isolates/{isolate['id']}/commands", headers=headers, json={"command": "pytest"}
    )
    assert response.status_code == 404, response.text
    response = await other_api.post(
        f"/api/v1/isolates/{company_isolate['id']}/commands",
        headers=headers,
        json={"command": "pytest"},
    )
    assert response.status_code == 202, response.text
    command = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["command_id"] == command["id"], tasks


@pytest.mark.parametrize(
    "operation,field",
    [
        ("isolate", "computer_id"),
        ("isolate", "user_id"),
        ("isolate", "session_id"),
        ("command", "computer_id"),
        ("command", "user_id"),
        ("command", "isolate_id"),
        ("command", "company_id"),
    ],
)
async def test_payload_cannot_override_execution_owner_or_target_computer(
    api, agent_api, isolate, isolate_payload, other_agent, other_user, company, operation, field
):
    """Client-supplied routing or ownership fields are rejected before they can redirect valid work."""
    values = {
        "computer_id": other_agent["computer"]["id"],
        "user_id": other_user["id"],
        "session_id": other_agent["headers"]["X-Agent-Session"],
        "isolate_id": isolate["id"],
        "company_id": company["id"],
    }
    path = (
        "/api/v1/isolates"
        if operation == "isolate"
        else f"/api/v1/isolates/{isolate['id']}/commands"
    )
    payload = isolate_payload if operation == "isolate" else {"command": "pytest"}
    response = await api.post(
        path, headers={"Idempotency-Key": str(uuid4())}, json={**payload, field: values[field]}
    )
    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == ["body", field] and error["type"] == "extra_forbidden"
        for error in response.json()["detail"]
    )
    response = await api.get("/api/v1/isolates")
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["next_sequence"] == 0
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})
    assert response.status_code == 200 and response.json()["tasks"] == []
