"""Owner recovery after a device key is lost, exercised through the live HTTP API."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_owner_cleanup_releases_work_and_preserves_history(
    api, other_api, agent_api, computer, running_command
):
    """Only the owner can confirm stopped sandboxes; cleanup fences the worker and retains results."""
    target = f"/api/v1/computers/{computer['id']}/unbind"
    headers = {"Idempotency-Key": str(uuid4())}
    payload = {"cleanup_confirmed": True}
    response = await other_api.post(target, headers=headers, json=payload)
    assert response.status_code == 404
    response = await api.post(target, headers=headers, json={"cleanup_confirmed": False})
    assert response.status_code == 422
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 200
    for _ in range(2):
        response = await api.post(target, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert response.json() == {"unbound": True, "computer_id": computer["id"]}
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 401
    state = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert state["reserved_cpu"] == state["reserved_memory"] == state["reserved_disk"] == 0
    assert state["session_id"] is None and not state["accepting_work"] and not state["online"]
    assert (await api.get("/api/v1/computer-ownerships")).json() == []
    result = (await api.get(f"/api/v1/commands/{running_command['command']['id']}/result")).json()
    assert result["status"] == "isolate_failed"
    assert (
        await api.post(target, headers={"Idempotency-Key": str(uuid4())}, json=payload)
    ).status_code == 200
    assert (await other_api.post(target, headers=headers, json=payload)).status_code == 404


async def test_company_member_cannot_recover_but_admin_can_retry_after_detach(
    api, other_api, client, computer, company, membership
):
    """Recovery requires administration, and an exact retry still succeeds after ownership disappears."""
    owner = f"/api/v1/computer-ownerships/{computer['ownership']['id']}"
    response = await api.patch(owner, json={"company_id": company["id"]})
    assert response.status_code == 200
    target = f"/api/v1/computers/{computer['id']}/unbind"
    headers = {"Idempotency-Key": str(uuid4())}
    payload = {"cleanup_confirmed": True}
    assert (await other_api.post(target, headers=headers, json=payload)).status_code == 403
    response = await api.patch(f"/api/v1/memberships/{membership['id']}", json={"role": "admin"})
    assert response.status_code == 200
    for _ in range(2):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": "bob's sufficiently long password"},
        )
        assert login.status_code == 200
        other_api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        response = await other_api.post(target, headers=headers, json=payload)
        assert response.status_code == 200, response.text
    assert (
        await other_api.post(target, headers={"Idempotency-Key": str(uuid4())}, json=payload)
    ).status_code == 404
