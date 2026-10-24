"""Owner-aware token issuance and registry over the real HTTP API."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from throng_api.models import AccessToken, TokenOwnership

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("owner", ["personal", "company"])
async def test_registry_issues_for_explicit_owner_without_revealing_secret_again(
    api, client, user, company, owner, clock
):
    """Issue for the explicitly selected personal or company owner, disclose the secret once, and enforce revocation."""
    payload = {"user_id": user["id"]} if owner == "personal" else {"company_id": company["id"]}
    response = await api.post(
        "/v1/api-tokens",
        json={
            **payload,
            "name": " login ",
            "scopes": ["read", "execute"],
            "expires_in_seconds": 30 * 86400,
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()
    assert token["purpose"] == "api" and token["name"] == "login"
    assert datetime.fromisoformat(token["expires_at"]) == clock.now + timedelta(days=30)
    assert token["issuer"]["id"] == user["id"] and "email" not in token["issuer"]
    assert token["created_by_user_id"] == user["id"]
    assert all(token[key] == value for key, value in payload.items())
    assert response.headers["cache-control"] == "no-store"
    listed = await api.get("/v1/api-tokens", params=payload)
    assert listed.status_code == 200 and listed.headers["x-total-count"] == "1"
    row = listed.json()[0]
    assert row["id"] == token["id"] and "token" not in row and "token_hash" not in row
    assert "email" not in row["issuer"] and "password_hash" not in row["issuer"]
    assert listed.headers["cache-control"] == "no-store"
    auth = {"Authorization": "Bearer " + token["token"]}
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 200
    assert (
        await api.patch(f"/v1/access-tokens/{token['id']}", json={"revoke": True})
    ).status_code == 200
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 401
    assert (await api.get("/v1/api-tokens", params=payload)).json()[0]["revoked_at"] is not None


async def test_registry_paginates_after_owner_authorization_and_excludes_login_tokens(
    api, other_api, user, other_user, company, client
):
    """Filter by authorized owners before pagination and never expose browser login credentials in the API registry."""
    issued = []
    for owner in ({"user_id": user["id"]}, {"company_id": company["id"]}):
        for index in range(3):
            response = await api.post(
                "/v1/api-tokens",
                json={**owner, "name": f"Token {index}", "scopes": ["read"]},
            )
            assert response.status_code == 201
            issued.append(response.json()["id"])
    await other_api.post(
        "/v1/api-tokens",
        json={"user_id": other_user["id"], "name": "Private", "scopes": ["read"]},
    )
    first = await api.get("/v1/api-tokens?limit=2")
    second = await api.get("/v1/api-tokens?limit=2&offset=2")
    third = await api.get("/v1/api-tokens?limit=2&offset=4")
    assert first.headers["x-total-count"] == "6"
    assert {row["id"] for result in (first, second, third) for row in result.json()} == set(issued)
    assert all(row["purpose"] == "api" for row in first.json())
    assert (await api.get("/v1/api-tokens?offset=999")).json() == []
    assert (
        await api.get("/v1/api-tokens", params={"user_id": other_user["id"]})
    ).status_code == 404
    assert (
        await other_api.get("/v1/api-tokens", params={"company_id": company["id"]})
    ).status_code == 404
    assert (await client.get("/v1/api-tokens")).status_code == 401
    assert (
        await client.post(
            "/v1/api-tokens",
            json={"user_id": user["id"], "name": "Anonymous", "scopes": ["read"]},
        )
    ).status_code == 401


@pytest.mark.parametrize(
    "permissions,read_status,create_status",
    [
        (["tokens.read"], 200, 403),
        (["tokens.manage"], 403, 201),
        ([], 403, 403),
        (["tokens.read", "tokens.manage"], 200, 201),
    ],
)
async def test_registry_permissions_are_independent_of_member_permissions(
    api, other_api, company, membership, permissions, read_status, create_status
):
    """Token read/create rights must follow the dedicated token permissions rather than member-management authority."""
    assert (
        await api.patch(f"/v1/memberships/{membership['id']}", json={"permissions": permissions})
    ).status_code == 200
    token = (
        await api.post(
            "/v1/api-tokens",
            json={"company_id": company["id"], "name": "Shared", "scopes": ["read"]},
        )
    ).json()
    result = await other_api.get("/v1/api-tokens", params={"company_id": company["id"]})
    assert result.status_code == read_status
    if read_status == 200:
        assert result.json()[0]["issuer"]["id"] == company["created_by_user_id"]
        assert result.json()[0]["id"] == token["id"]
    assert (
        await other_api.post(
            "/v1/api-tokens",
            json={"company_id": company["id"], "name": "Delegated", "scopes": ["read"]},
        )
    ).status_code == create_status
    assert (
        await other_api.patch(f"/v1/access-tokens/{token['id']}", json={"revoke": True})
    ).status_code == (200 if create_status == 201 else 403)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": str(uuid4())},
        {"company_id": str(uuid4())},
        {"user_id": str(uuid4()), "company_id": str(uuid4())},
    ],
)
async def test_rejected_creation_does_not_leave_an_ownership(api, app, payload):
    """Rejected issuance must roll back the provisional ownership instead of leaving orphaned registry entries."""
    async with app.state.db.read() as session:
        before = await session.scalar(select(func.count()).select_from(TokenOwnership))
    response = await api.post(
        "/v1/api-tokens", json={**payload, "name": "Denied", "scopes": ["read"]}
    )
    assert response.status_code in (404, 422)
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(TokenOwnership)) == before


@pytest.mark.parametrize("access_token", [{"scopes": ["read", "manage"]}], indirect=True)
async def test_cannot_escalate_scopes_or_create_as_company(
    api, client, access_token, company_token, user
):
    """Issuance cannot grant more scopes than its caller or use a company credential as a personal issuing session."""
    payload = {
        "user_id": user["id"],
        "name": "Escalation",
        "scopes": ["read", "execute"],
    }
    response = await client.post(
        "/v1/api-tokens",
        headers={"Authorization": "Bearer " + access_token["token"]},
        json=payload,
    )
    assert response.status_code == 403
    response = await client.post(
        "/v1/api-tokens",
        headers={"Authorization": "Bearer " + company_token["token"]},
        json=payload,
    )
    assert response.status_code == 403
    assert (
        await api.get("/v1/api-tokens", params={"user_id": user["id"], "company_id": str(uuid4())})
    ).status_code == 422


async def test_legacy_purpose_is_preserved_without_guessing_from_names(
    api, app, user, access_token
):
    """Historical credentials keep their legacy classification instead of inferring security purpose from editable names."""
    async with app.state.db.write() as session:
        await session.execute(update(AccessToken).values(purpose="legacy"))
    result = await api.get("/v1/api-tokens")
    assert [row["id"] for row in result.json()] == [access_token["id"]]
    assert result.json()[0]["purpose"] == "legacy"
    # Current legacy login is hidden, but explicit API tokens called login are valid entries.
    response = await api.post(
        "/v1/api-tokens",
        json={"user_id": user["id"], "name": "login", "scopes": ["read"]},
    )
    assert response.status_code == 201
    assert {row["purpose"] for row in (await api.get("/v1/api-tokens")).json()} == {
        "api",
        "legacy",
    }
