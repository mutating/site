"""Client-generated device identity replaces hardware attestation.

These tests pin the externally visible contract after the attestation cut:
login records a device, enrollment is a plain idempotent registration, the
computer starts without a pool, and agents authenticate with their credential
only. They deliberately avoid the old ACME/profile/proof fixtures so a
regression cannot hide behind compatibility helpers.
"""

import hashlib
import secrets
from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


def registration(device_id, **overrides):
    """Return the minimal valid Power Cell registration payload."""
    return {
        "device_id": str(device_id),
        "name": "My Mac",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
        **overrides,
    }


async def login_with_device(client, user, device_id):
    """Sign in again with the same password, now identifying this installation."""
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": user["email"],
            "password": user["password"],
            "device_id": str(device_id),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def register(api, device_id, key=None, **overrides):
    """Create and finish one enrollment, returning the binding and exact retry key."""
    key = key or str(uuid4())
    response = await api.post(
        "/v1/agent-enrollments",
        headers={"Idempotency-Key": key},
        json=registration(device_id, **overrides),
    )
    assert response.status_code == 201, response.text
    enrollment = response.json()
    assert enrollment["status"] == "verified"
    assert "certificate_sha256" not in enrollment
    assert "relay" not in enrollment
    credential = "agt_" + secrets.token_urlsafe(32)
    response = await api.post(
        f"/v1/agent-enrollments/{enrollment['id']}/finish",
        headers={"Idempotency-Key": "finish-" + enrollment["id"]},
        json={"credential_hash": hashlib.sha256(credential.encode()).hexdigest()},
    )
    assert response.status_code == 200, response.text
    binding = response.json()
    assert "credential" not in binding
    binding["credential"] = credential
    return enrollment, binding, key


async def start_agent(client, binding):
    """Open a normal agent session using only the issued bearer credential."""
    headers = {"Authorization": "Bearer " + binding["credential"]}
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"clean_start": True},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    return {**headers, "X-Agent-Session": session["session_id"]}, session


async def test_login_registers_device_and_enrollment_needs_no_attestation(client, api, user):
    """A device id supplied at login authorizes a plain, immediately verified registration."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        enrollment, binding, _ = await register(device_api, device_id)
    assert binding["device_id"]
    assert "certificate_sha256" not in binding
    assert "relay" not in binding
    assert binding["computer_id"]

    response = await api.get(f"/v1/agent-enrollments/{enrollment['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    for retired in ("profile", "verification"):
        response = await api.get(f"/v1/agent-enrollments/{enrollment['id']}/{retired}")
        assert response.status_code in {404, 405}


async def test_enrollment_requires_the_device_to_have_signed_in(client, api, user):
    """A caller cannot register an arbitrary UUID that never authenticated."""
    device_id = uuid4()
    response = await api.post(
        "/v1/agent-enrollments",
        headers={"Idempotency-Key": str(uuid4())},
        json=registration(device_id),
    )
    assert response.status_code == 409
    assert "sign in" in response.json()["detail"].lower()


async def test_registration_is_unassigned_until_a_pool_is_explicitly_chosen(
    client, api, user, company
):
    """A new computer is visible to its account with no owner and can be assigned later."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        _, binding, _ = await register(device_api, device_id)
    computer_id = binding["computer_id"]

    response = await api.get("/v1/connected-computers")
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["items"] if item["id"] == computer_id)
    assert row["pool"] is None
    assert row["can_move"] is True

    response = await api.patch(
        f"/v1/connected-computers/{computer_id}/pool",
        headers={"Idempotency-Key": str(uuid4())},
        json={"company_id": company["id"], "expected_company_id": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == company["id"]

    response = await api.get("/v1/connected-computers")
    row = next(item for item in response.json()["items"] if item["id"] == computer_id)
    assert row["pool"]["company_id"] == company["id"]


async def test_finish_is_idempotent_and_agent_uses_only_its_credential(client, api, user):
    """An exact retry returns the same computer; a changed key or hash conflicts."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        enrollment, binding, _ = await register(device_api, device_id)
        retry = await device_api.post(
            f"/v1/agent-enrollments/{enrollment['id']}/finish",
            headers={"Idempotency-Key": "finish-" + enrollment["id"]},
            json={"credential_hash": hashlib.sha256(binding["credential"].encode()).hexdigest()},
        )
        credential_hash = hashlib.sha256(binding["credential"].encode()).hexdigest()
        assert retry.status_code == 200
        assert retry.json() == {
            "computer_id": binding["computer_id"],
            "device_id": binding["device_id"],
        }
        different_key = await device_api.post(
            f"/v1/agent-enrollments/{enrollment['id']}/finish",
            headers={"Idempotency-Key": str(uuid4())},
            json={"credential_hash": credential_hash},
        )
        assert different_key.status_code == 409
        different_hash = await device_api.post(
            f"/v1/agent-enrollments/{enrollment['id']}/finish",
            headers={"Idempotency-Key": "finish-" + enrollment["id"]},
            json={"credential_hash": hashlib.sha256(b"another credential").hexdigest()},
        )
        assert different_hash.status_code == 409

    agent_headers, session = await start_agent(client, binding)
    response = await client.post("/agent/v1/heartbeat", headers=agent_headers)
    assert response.status_code == 200, response.text
    assert response.json()["computer_id"] == binding["computer_id"]
    assert response.json()["session_id"] == session["session_id"]

    response = await client.post("/agent/v1/heartbeat")
    assert response.status_code == 401


async def test_unassigned_computer_receives_no_work_until_pool_assignment(
    client, api, user, artifact
):
    """The device is the routing unit only after an explicit pool binding."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        _, binding, _ = await register(device_api, device_id)
    agent_headers, _ = await start_agent(client, binding)
    response = await client.post(
        "/agent/v1/capacity",
        headers={**agent_headers, "Idempotency-Key": str(uuid4())},
        json={
            "revision": 0,
            "accepting_work": True,
            "mode": "idle",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
            "running_commands": 0,
            "cpu_used_millis": 0,
            "memory_used_bytes": 0,
        },
    )
    assert response.status_code == 200, response.text
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "state_artifact_id": artifact["id"],
            "image": "ubuntu:24.04",
            "os": "linux",
            "architecture": "arm64",
            "runtime": "powercell-ubuntu-arm64-v1",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 202, response.text
    response = await client.post(
        "/agent/v1/tasks/claim", headers=agent_headers, json={"wait_seconds": 1}
    )
    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []

    assigned = await api.patch(
        f"/v1/connected-computers/{binding['computer_id']}/pool",
        headers={"Idempotency-Key": str(uuid4())},
        json={"company_id": None, "expected_company_id": None},
    )
    assert assigned.status_code == 200, assigned.text
    response = await client.post(
        "/agent/v1/tasks/claim", headers=agent_headers, json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    assert [task["kind"] for task in response.json()["tasks"]] == ["create"]


async def test_restarting_a_registration_replaces_the_unfinished_attempt(client, api, user):
    """A new explicit attempt must not be blocked by an earlier pending row."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        first = await device_api.post(
            "/v1/agent-enrollments",
            headers={"Idempotency-Key": "first"},
            json=registration(device_id, name="First"),
        )
        assert first.status_code == 201, first.text
        second = await device_api.post(
            "/v1/agent-enrollments",
            headers={"Idempotency-Key": "second"},
            json=registration(device_id, name="Second"),
        )
        assert second.status_code == 201, second.text
        assert second.json()["id"] != first.json()["id"]
        credential = "agt_" + secrets.token_urlsafe(32)
        response = await device_api.post(
            f"/v1/agent-enrollments/{second.json()['id']}/finish",
            headers={"Idempotency-Key": "finish-second"},
            json={"credential_hash": hashlib.sha256(credential.encode()).hexdigest()},
        )
        assert response.status_code == 200, response.text


async def test_finish_rejects_missing_hashes_and_reused_credentials(client, api, user):
    """The body is mandatory and a credential cannot authenticate two computers."""
    device_id = uuid4()
    headers = await login_with_device(client, user, device_id)
    async with AsyncClient(
        base_url=str(client.base_url), headers=headers, timeout=10, trust_env=False
    ) as device_api:
        _, first, _ = await register(device_api, device_id)
        second = await device_api.post(
            "/v1/agent-enrollments",
            headers={"Idempotency-Key": "second-enrollment"},
            json=registration(device_id, name="Second"),
        )
        assert second.status_code == 201, second.text
        target = f"/v1/agent-enrollments/{second.json()['id']}/finish"

        empty = await device_api.post(
            target,
            headers={"Idempotency-Key": "empty-finish"},
            json={},
        )
        assert empty.status_code == 422
        malformed = await device_api.post(
            target,
            headers={"Idempotency-Key": "malformed-finish"},
            json={"credential_hash": first["credential"]},
        )
        assert malformed.status_code == 422
        reused = await device_api.post(
            target,
            headers={"Idempotency-Key": "reused-finish"},
            json={"credential_hash": hashlib.sha256(first["credential"].encode()).hexdigest()},
        )
        assert reused.status_code == 409


async def test_capabilities_advertise_client_device_identity_without_attestation(client):
    """Clients can distinguish the simplified server before sending a registration."""
    response = await client.get("/v1/agent-capabilities")
    assert response.status_code == 200
    body = response.json()
    assert "powercell.device.v1" in body["extensions"]
    assert "attestation_required" not in body
