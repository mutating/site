"""Real resources and valid requests for every protected HTTP operation."""

from datetime import timedelta
from uuid import uuid4

import pytest


@pytest.fixture
async def company_artifact(api, company):
    """Create initial state owned exclusively by Alice's company."""
    response = await api.post(
        "/v1/artifacts", params={"company_id": company["id"]}, content=b"company state"
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def company_isolate_payload(company, company_artifact):
    """Provide a valid explicit company-pool request with matching state ownership."""
    return {
        "company_id": company["id"],
        "state_artifact_id": company_artifact["id"],
        "image": "python:3.12",
        "os": "linux",
        "architecture": "amd64",
        "runtime": "oci",
        "cpu_millis": 1000,
        "memory_bytes": 1024,
        "disk_bytes": 4096,
    }


@pytest.fixture
async def company_isolate(api, client, company_agent, company_isolate_payload):
    """Provision a company isolate through its actual assigned worker's HTTP protocol."""
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json=company_isolate_payload,
    )
    assert response.status_code == 202, response.text
    isolate = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] == isolate["id"], tasks
    assert tasks[0]["kind"] == "create"
    response = await client.post(
        f"/agent/v1/tasks/{tasks[0]['id']}/complete", headers=company_agent["headers"], json={}
    )
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/isolates/{isolate['id']}")
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    assert response.json()["computer_id"] == company_agent["computer"]["id"]
    return response.json()


@pytest.fixture
async def other_company_agent(other_api, client):
    """Enroll a suitable idle computer in Bob's independent company, inaccessible to Alice."""
    response = await other_api.post("/v1/companies", json={"name": "Bob's company"})
    assert response.status_code == 201, response.text
    company = response.json()
    response = await other_api.post(
        "/v1/computers",
        json={
            "company_id": company["id"],
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    computer = response.json()
    response = await other_api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"clean_start": True},
    )
    assert response.status_code == 200, response.text
    return {
        "computer": computer,
        "headers": {**headers, "X-Agent-Session": response.json()["session_id"]},
    }


@pytest.fixture
async def member_execution(other_api, client, membership, company_agent, company_isolate_payload):
    """Let a current member provision an isolate and finish a command, preserving their retry keys."""
    isolate_key, command_key = str(uuid4()), str(uuid4())
    response = await other_api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": isolate_key},
        json=company_isolate_payload,
    )
    assert response.status_code == 202, response.text
    isolate = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] == isolate["id"], tasks
    response = await client.post(
        f"/agent/v1/tasks/{tasks[0]['id']}/complete", headers=company_agent["headers"], json={}
    )
    assert response.status_code == 200, response.text
    payload = {"command": "pytest -q"}
    response = await other_api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": command_key},
        json=payload,
    )
    assert response.status_code == 202, response.text
    command = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=company_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["command_id"] == command["id"], tasks
    response = await client.post(
        f"/agent/v1/tasks/{tasks[0]['id']}/complete",
        headers=company_agent["headers"],
        json={"returncode": 0},
    )
    assert response.status_code == 200, response.text
    return {
        "isolate": isolate,
        "isolate_key": isolate_key,
        "command": command,
        "command_key": command_key,
        "command_payload": payload,
    }


@pytest.fixture
def api_requests(
    user,
    company,
    membership,
    computer,
    token_ownership,
    access_token,
    artifact,
    isolate_payload,
    isolate,
    running_command,
    computer_resources,
    clock,
):
    """Enumerate protected client operations independently of the application's auth declarations."""
    requests = [
        ("GET", "/v1/users", "read", {}),
        ("GET", "/v1/users/me", "read", {}),
        ("GET", "/v1/users/me/avatar", "read", {}),
        ("PUT", "/v1/users/me/avatar", "manage", {"content": b"avatar"}),
        ("DELETE", "/v1/users/me/avatar", "manage", {}),
        ("GET", "/v1/users/{user_id}", "read", {}),
        ("PATCH", "/v1/users/{user_id}", "manage", {"json": {"first_name": "Changed"}}),
        ("DELETE", "/v1/users/{user_id}", "manage", {}),
    ]
    resources = [
        ("companies", company["id"], "company_id", {"name": "New company"}, {"name": "Changed"}),
        (
            "memberships",
            membership["id"],
            "membership_id",
            {"user_id": membership["user_id"], "company_id": company["id"], "role": "user"},
            {"role": "admin"},
        ),
        (
            "computers",
            computer["id"],
            "computer_id",
            {
                "os": "linux",
                "architecture": "amd64",
                "runtime": "oci",
                "cpu_millis": 2000,
                "memory_bytes": 2048,
                "disk_bytes": 8192,
            },
            {"name": "Changed"},
        ),
        (
            "computer-ownerships",
            computer["ownership"]["id"],
            "ownership_id",
            {"computer_id": computer["id"], "user_id": user["id"]},
            {"company_id": company["id"]},
        ),
        (
            "token-ownerships",
            token_ownership["id"],
            "ownership_id",
            {"user_id": user["id"]},
            {"company_id": company["id"]},
        ),
        (
            "access-tokens",
            access_token["id"],
            "token_id",
            {"ownership_id": token_ownership["id"], "name": "New token", "scopes": ["read"]},
            {"name": "Changed"},
        ),
    ]
    identifiers = {"user_id": user["id"]}
    for resource, identifier, parameter, create, update in resources:
        collection = f"/v1/{resource}"
        item = collection + "/{" + parameter + "}"
        identifiers[item] = {parameter: identifier}
        params = {"params": {"company_id": company["id"]}} if resource == "memberships" else {}
        requests.extend(
            [
                ("GET", collection, "read", params),
                ("POST", collection, "manage", {"json": create}),
                ("GET", item, "read", {}),
                ("PATCH", item, "manage", {"json": update}),
                ("DELETE", item, "manage", {}),
            ]
        )
    requests.extend(
        [
            (
                "GET",
                "/v1/companies/{company_id}/users/lookup",
                "manage",
                {"params": {"identifier": "alice"}},
            ),
            ("GET", "/v1/memberships/{membership_id}/avatar", "read", {}),
            ("POST", "/v1/invitations", "manage", {"json": {"company_id": company["id"]}}),
            ("GET", "/v1/invitations", "read", {}),
            ("GET", "/v1/invitations/link/{token}", "read", {}),
            ("POST", "/v1/invitations/link/{token}/accept", "manage", {}),
            ("POST", "/v1/invitations/{invitation_id}/accept", "manage", {}),
            ("POST", "/v1/invitations/{invitation_id}/decline", "manage", {}),
            ("POST", "/v1/invitations/{invitation_id}/link", "manage", {}),
            ("DELETE", "/v1/invitations/{invitation_id}", "manage", {}),
            ("POST", "/v1/computers/{computer_id}/credentials", "manage", {}),
            (
                "POST",
                "/v1/computers/{computer_id}/unbind",
                "manage",
                {"json": {"cleanup_confirmed": True}},
            ),
            ("GET", "/v1/artifacts", "read", {}),
            ("POST", "/v1/artifacts", "execute", {"content": b"unauthorized state"}),
            ("GET", "/v1/artifacts/{artifact_id}", "read", {}),
            ("GET", "/v1/artifacts/{artifact_id}/content", "read", {}),
            ("DELETE", "/v1/artifacts/{artifact_id}", "execute", {}),
            ("GET", "/v1/isolates", "read", {}),
            ("POST", "/v1/isolates", "execute", {"json": isolate_payload}),
            ("GET", "/v1/isolates/{isolate_id}", "read", {}),
            ("DELETE", "/v1/isolates/{isolate_id}", "execute", {}),
            ("POST", "/v1/isolates/{isolate_id}/renew", "execute", {}),
            (
                "POST",
                "/v1/isolates/{isolate_id}/commands",
                "execute",
                {"json": {"command": "pytest -q"}},
            ),
            (
                "POST",
                "/v1/commands/statuses",
                "read",
                {"json": {"ids": [running_command["command"]["id"]]}},
            ),
            (
                "POST",
                "/v1/commands/results",
                "read",
                {"json": {"ids": [running_command["command"]["id"]]}},
            ),
            (
                "POST",
                "/v1/commands/cancel",
                "execute",
                {"json": {"ids": [running_command["command"]["id"]]}},
            ),
            ("GET", "/v1/commands/{command_id}", "read", {}),
            ("GET", "/v1/commands/{command_id}/result", "read", {}),
            ("POST", "/v1/commands/{command_id}/cancel", "execute", {}),
            ("POST", "/v1/isolates/{isolate_id}/snapshots", "execute", {}),
            ("GET", "/v1/operations/{operation_id}", "read", {}),
        ]
    )
    identifiers.update(
        computer_id=computer["id"],
        artifact_id=artifact["id"],
        isolate_id=isolate["id"],
        command_id=running_command["command"]["id"],
        operation_id=running_command["task"]["id"],
        resource_id=computer_resources["cpu"]["id"],
        type_id=computer_resources["cpu"]["resource_type_id"],
    )
    period = {"from": clock.now.isoformat(), "to": (clock.now + timedelta(hours=1)).isoformat()}
    requests.extend(
        [
            ("GET", "/v1/resource-types", "read", {}),
            (
                "POST",
                "/v1/resource-types",
                "manage",
                {"json": {"code": "gpu", "name": "GPU", "unit": "device"}},
            ),
            ("GET", "/v1/resource-types/{type_id}", "read", {}),
            ("PATCH", "/v1/resource-types/{type_id}", "manage", {"json": {"name": "Changed"}}),
            ("DELETE", "/v1/resource-types/{type_id}", "manage", {}),
            ("GET", "/v1/computers/{computer_id}/resources", "read", {}),
            (
                "POST",
                "/v1/computers/{computer_id}/resources",
                "manage",
                {"json": {"resource_type_id": identifiers["type_id"], "total": 2000}},
            ),
            ("GET", "/v1/computer-resources/{resource_id}", "read", {}),
            (
                "PATCH",
                "/v1/computer-resources/{resource_id}",
                "manage",
                {"json": {"total": 2000}},
            ),
            ("DELETE", "/v1/computer-resources/{resource_id}", "manage", {}),
            ("GET", "/v1/computer-resources/{resource_id}/history", "read", {"params": period}),
            ("GET", "/v1/users/{user_id}/resources/summary", "read", {}),
            ("GET", "/v1/users/{user_id}/resources/history", "read", {"params": period}),
            (
                "GET",
                "/v1/users/{user_id}/resources/availability-history",
                "read",
                {"params": period},
            ),
            ("GET", "/v1/companies/{company_id}/resources/summary", "read", {}),
            ("GET", "/v1/companies/{company_id}/resources/history", "read", {"params": period}),
            (
                "GET",
                "/v1/companies/{company_id}/resources/availability-history",
                "read",
                {"params": period},
            ),
        ]
    )
    identifiers["company_id"] = company["id"]
    identifiers["membership_id"] = membership["id"]
    identifiers["invitation_id"] = str(uuid4())
    identifiers["token"] = "inv_" + "x" * 43
    identifiers["enrollment_id"] = str(uuid4())
    identifiers["diagnostic_id"] = str(uuid4())
    requests.extend(
        [
            (
                "POST",
                "/v1/agent-enrollments",
                "manage",
                {
                    "json": {
                        "installation_id": str(uuid4()),
                        "isolation_modes": ["vm", "shared"],
                        "cpu_millis": 2000,
                        "memory_bytes": 2048,
                        "disk_bytes": 8192,
                    }
                },
            ),
            ("GET", "/v1/agent-enrollments/{enrollment_id}", "read", {}),
            ("GET", "/v1/agent-enrollments/{enrollment_id}/profile", "read", {}),
            ("GET", "/v1/agent-enrollments/{enrollment_id}/verification", "read", {}),
            ("POST", "/v1/agent-enrollments/{enrollment_id}/finish", "manage", {}),
            ("GET", "/v1/computers/{computer_id}/diagnostics/{diagnostic_id}", "read", {}),
        ]
    )
    return [
        {
            "method": method,
            "template": template,
            "url": template.format(**identifiers.get(template, identifiers)),
            "scope": scope,
            "kwargs": {"headers": {"Idempotency-Key": str(uuid4())}, **kwargs},
        }
        for method, template, scope, kwargs in requests
    ]


@pytest.fixture
def agent_requests(agent, artifact, running_command):
    """Enumerate every worker operation with real IDs and schema-valid bodies and headers."""
    requests = [
        ("GET", "/agent/v1/session", {}),
        (
            "POST",
            "/agent/v1/session",
            {"json": {"clean_start": True, "previous_session_id": agent["session"]["session_id"]}},
        ),
        ("POST", "/agent/v1/heartbeat", {}),
        ("POST", "/agent/v1/tasks/claim", {"json": {"wait_seconds": 0}}),
        ("GET", "/agent/v1/tasks/{task_id}", {}),
        ("POST", "/agent/v1/tasks/{task_id}/ack", {}),
        ("POST", "/agent/v1/tasks/{task_id}/started", {}),
        ("POST", "/agent/v1/tasks/{task_id}/complete", {"json": {"returncode": 0}}),
        (
            "PUT",
            "/agent/v1/tasks/{task_id}/artifacts/{purpose}",
            {"content": b"unauthorized output"},
        ),
        ("GET", "/agent/v1/artifacts/{artifact_id}/content", {}),
        (
            "POST",
            "/agent/v1/capacity",
            {
                "json": {
                    "revision": 0,
                    "accepting_work": True,
                    "mode": "idle",
                    "cpu_millis": 2000,
                    "memory_bytes": 2048,
                    "disk_bytes": 8192,
                    "running_commands": 0,
                    "cpu_used_millis": 0,
                    "memory_used_bytes": 0,
                }
            },
        ),
        (
            "POST",
            "/agent/v1/isolates/{isolate_id}/evict",
            {"json": {"cleanup_confirmed": True, "reason": "isolate_lost"}},
        ),
        (
            "POST",
            "/agent/v1/disconnect",
            {"json": {"cleanup_confirmed": True, "reason": "disabled"}},
        ),
        ("POST", "/agent/v1/unbind", {"json": {"cleanup_confirmed": True}}),
        ("PUT", "/agent/v1/diagnostics/{diagnostic_id}", {"content": b"unauthorized diagnostic"}),
    ]
    return [
        {
            "method": method,
            "template": template,
            "url": template.format(
                task_id=running_command["task"]["id"],
                artifact_id=artifact["id"],
                purpose="stdout",
                isolate_id=running_command["command"]["isolate_id"],
                diagnostic_id=str(uuid4()),
            ),
            "kwargs": {"headers": {"Idempotency-Key": str(uuid4())}, **kwargs},
        }
        for method, template, kwargs in requests
    ]
