"""Access regressions exercise real HTTP requests and committed concurrent SQL changes."""

import asyncio
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_computer_list_does_not_mix_authorized_rows_with_new_private_capacities(
    api, other_api, user, shared_review_computer, paused_computer_list
):
    """A list started before ownership transfer may show its authorized snapshot, but must not reveal the new private owner's resource changes; later reads use the same hidden 404 as a missing computer."""
    computer = shared_review_computer
    entered, resume = paused_computer_list
    pending = asyncio.create_task(other_api.get("/api/v1/computers"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.patch(
            f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
            json={"user_id": user["id"]},
        )
        assert response.status_code == 200, response.text
        response = await api.patch(
            f"/api/v1/computers/{computer['id']}", json={"cpu_millis": 777000}
        )
        assert response.status_code == 200, response.text
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == computer["id"]
    assert response.json()[0]["cpu_millis"] == computer["cpu_millis"]
    assert response.json()[0]["memory_bytes"] == computer["memory_bytes"]
    response = await other_api.get(f"/api/v1/computers/{computer['id']}")
    assert response.status_code == 404 and response.json() == {"detail": "Resource not found"}
    response = await other_api.get("/api/v1/computers")
    assert response.status_code == 200 and response.json() == []


@pytest.mark.parametrize("paused_agent_computer_lookup", [1, 2], indirect=True)
async def test_deleted_agent_computer_invalidates_an_authentication_already_in_progress(
    api, agent_api, computer, paused_agent_computer_lookup
):
    """Deleting an idle computer after its credential was read produces 401 rather than dereferencing a missing computer and returning 500."""
    entered, resume = paused_agent_computer_lookup
    pending = asyncio.create_task(agent_api.get("/agent/v1/session"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.delete(f"/api/v1/computers/{computer['id']}")
        assert response.status_code == 204, response.text
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Invalid agent credential"}


@pytest.mark.parametrize("route", ["/users", "/users/me", "/users/{user_id}"])
async def test_user_deletion_between_authentication_and_profile_read_returns_401(
    api, user, paused_profile_lookup, route
):
    """Each profile endpoint rejects an issuer deleted after authentication instead of returning a missing user through its required response schema and causing 500."""
    entered, resume = paused_profile_lookup
    path = "/api/v1" + route.format(user_id=user["id"])
    pending = asyncio.create_task(api.get(path))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await api.delete(f"/api/v1/users/{user['id']}")
        assert response.status_code == 204, response.text
    finally:
        resume.set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Invalid token issuer"}


@pytest.mark.parametrize("limit, offset", [(1, 0), (2, 1), (2, 3)])
async def test_computer_list_paginates_computers_without_splitting_their_resources(
    api, paginated_review_computers, limit, offset
):
    """The joined resource read preserves computer-level limits, ordering, empty pages and complete CPU/memory values without duplicate computers."""
    response = await api.get("/api/v1/computers", params={"limit": limit, "offset": offset})
    assert response.status_code == 200, response.text
    expected = paginated_review_computers[offset : offset + limit]
    assert [row["id"] for row in response.json()] == [row["id"] for row in expected]
    assert [(row["cpu_millis"], row["memory_bytes"]) for row in response.json()] == [
        (row["cpu_millis"], row["memory_bytes"]) for row in expected
    ]


@pytest.mark.parametrize("access_token", [{"scopes": ["manage"]}], indirect=True)
@pytest.mark.parametrize("mode, status", [("anonymous", 401), ("foreign", 404), ("limited", 403)])
async def test_rejected_password_changes_do_not_perform_argon2_work(
    client, user, other_user, access_token, password_hash_observer, mode, status
):
    """Anonymous, foreign-profile and insufficient-scope password changes fail before expensive hashing; foreign profiles retain the same 404 body as absent resources."""
    headers = {}
    target = str(uuid4())
    if mode == "foreign":
        headers = user["headers"]
        target = other_user["id"]
    elif mode == "limited":
        headers = {"Authorization": "Bearer " + access_token["token"]}
        target = user["id"]
    response = await client.patch(
        f"/api/v1/users/{target}", headers=headers, json={"password": "replacement long password"}
    )
    assert response.status_code == status, response.text
    assert password_hash_observer["calls"] == 0
    if mode == "foreign":
        assert response.json() == {"detail": "Resource not found"}


async def test_authorized_password_change_hashes_once_and_keeps_login_and_revocation_semantics(
    api, client, user, access_token, password_hash_observer
):
    """A permitted password update performs one real hash, accepts the new password and revokes both login and delegated tokens."""
    replacement = "replacement long password"
    response = await api.patch(f"/api/v1/users/{user['id']}", json={"password": replacement})
    assert response.status_code == 200, response.text
    assert password_hash_observer["calls"] == 1
    assert (await api.get("/api/v1/users/me")).status_code == 401
    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + access_token["token"]}
    )
    assert response.status_code == 401, response.text
    response = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == 401, response.text
    response = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": replacement}
    )
    assert response.status_code == 200, response.text


async def test_password_change_rechecks_revocation_after_hashing_without_holding_write_lock(
    api, client, user, access_token, password_hash_observer
):
    """Revocation can commit while a real password hash is in progress, and its final authorization rejects the update without changing the original password."""
    observer = password_hash_observer
    observer["pause"] = True
    replacement = "replacement long password"
    pending = asyncio.create_task(
        client.patch(
            f"/api/v1/users/{user['id']}",
            headers={"Authorization": "Bearer " + access_token["token"]},
            json={"password": replacement},
        )
    )
    try:
        await asyncio.wait_for(observer["entered"].wait(), timeout=5)
        response = await asyncio.wait_for(
            api.patch(f"/api/v1/access-tokens/{access_token['id']}", json={"revoke": True}),
            timeout=3,
        )
        assert response.status_code == 200, response.text
    finally:
        observer["resume"].set()
        response = await asyncio.wait_for(pending, timeout=5)
    assert response.status_code == 401, response.text
    assert observer["calls"] == 1
    assert (await api.get("/api/v1/users/me")).status_code == 200
    response = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": replacement}
    )
    assert response.status_code == 401, response.text
    response = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == 200, response.text
