"""Account recovery detaches an agent while preserving the installation identity.

The credential can be lost or revoked without invalidating the client-generated
device id. A later login from the same installation can register again and must
receive the same server device row.
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_account_recovery_revokes_credential_and_reuses_device(api, client, bound_device, user):
    """Unbinding is idempotent, revokes the agent, and a fresh registration keeps the device."""
    computer_id = bound_device.binding["computer_id"]
    target = f"/v1/computers/{computer_id}/unbind"
    for _ in range(2):
        response = await api.post(
            target, headers={"Idempotency-Key": "owner-recovery"}, json={"cleanup_confirmed": True}
        )
        assert response.status_code == 200, response.text

    state = (await api.get(f"/v1/computers/{computer_id}")).json()
    assert state["device_id"] == bound_device.binding["device_id"]
    assert "certificate_sha256" not in state
    assert state["session_id"] is None

    response = await client.post(
        "/agent/v1/session",
        headers={
            "Authorization": "Bearer " + bound_device.binding["credential"],
            "Idempotency-Key": str(uuid4()),
        },
        json={"clean_start": True},
    )
    assert response.status_code == 401

    login = await client.post(
        "/v1/auth/login",
        json={
            "login": user["email"],
            "password": user["password"],
            "device_id": bound_device.device_id,
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"], "Idempotency-Key": str(uuid4())}
    payload = {
        "device_id": bound_device.device_id,
        "name": "Recovered Mac",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
    }
    response = await client.post("/v1/agent-enrollments", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    enrollment = response.json()
    response = await client.post(
        f"/v1/agent-enrollments/{enrollment['id']}/finish",
        headers={"Authorization": headers["Authorization"], "Idempotency-Key": "recover-finish"},
        json={},
    )
    assert response.status_code == 200, response.text
    replacement = response.json()
    assert replacement["computer_id"] != computer_id
    assert replacement["device_id"] == bound_device.binding["device_id"]
    assert replacement["credential"] != bound_device.binding["credential"]


async def test_same_installation_can_join_another_account(client, api, other_api, user, other_user):
    """Switching accounts adds a device association instead of duplicating the device row."""
    device_id = uuid4()
    first_login = await client.post(
        "/v1/auth/login",
        json={"login": user["email"], "password": user["password"], "device_id": str(device_id)},
    )
    assert first_login.status_code == 200, first_login.text
    second_login = await client.post(
        "/v1/auth/login",
        json={
            "login": other_user["email"],
            "password": "bob's sufficiently long password",
            "device_id": str(device_id),
        },
    )
    assert second_login.status_code == 200, second_login.text

    async def register(api_client, token, name):
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
        payload = {
            "device_id": str(device_id),
            "name": name,
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
            "isolation_modes": ["vm", "shared"],
        }
        created = await api_client.post("/v1/agent-enrollments", headers=headers, json=payload)
        assert created.status_code == 201, created.text
        row = created.json()
        finished = await api_client.post(
            f"/v1/agent-enrollments/{row['id']}/finish",
            headers={
                "Authorization": headers["Authorization"],
                "Idempotency-Key": "finish-" + row["id"],
            },
            json={},
        )
        assert finished.status_code == 200, finished.text
        return finished.json()

    alice = await register(api, first_login.json()["access_token"], "Alice Mac")
    bob = await register(other_api, second_login.json()["access_token"], "Bob Mac")
    assert alice["computer_id"] != bob["computer_id"]
    assert alice["device_id"] == bob["device_id"]
