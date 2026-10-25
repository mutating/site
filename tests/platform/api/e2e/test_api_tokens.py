"""Owner-aware token issuance and registry over the real HTTP API."""

import re
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tests.platform.api.token_helpers import move_to_legacy
from throng_api.models import AccessToken, TokenOwnership
from throng_api.security import digest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("owner", ["personal", "company"])
@pytest.mark.parametrize("endpoint", ["api-tokens", "access-tokens"])
async def test_api_token_format_identifies_owner_and_uses_only_ascii_alphanumerics(
    api, client, user, company, owner, endpoint
):
    """Both issuance APIs must use the selected owner's ORG-/SELF- prefix.

    The random body must contain only ASCII letters and digits, without the
    hyphens or underscores of the old URL-safe format, and still authenticate.
    """
    ownership = {"user_id": user["id"]} if owner == "personal" else {"company_id": company["id"]}
    if endpoint == "access-tokens":
        response = await api.post("/v1/token-ownerships", json=ownership)
        assert response.status_code == 201, response.text
        ownership = {"ownership_id": response.json()["id"]}
    response = await api.post(
        f"/v1/{endpoint}",
        json={**ownership, "name": "Formatted credential", "scopes": ["read", "execute"]},
    )
    assert response.status_code == 201, response.text
    raw = response.json()["token"]
    prefix = "SELF-" if owner == "personal" else "ORG-"
    assert re.fullmatch(prefix + r"[A-Za-z0-9]+", raw)
    assert (
        await client.get("/v1/auth/check", headers={"Authorization": "Bearer " + raw})
    ).status_code == 200


@pytest.mark.parametrize("owner", ["personal", "company"])
@pytest.mark.parametrize("historical", [False, True])
async def test_existing_thr_credentials_keep_authenticating(
    api, client, app, user, company, owner, historical
):
    """Changing newly issued token formats must not invalidate stored old hashes.

    Existing API and historical secrets may contain URL-safe punctuation. They
    must keep their access until revoked, even though new secrets use ORG-/SELF-.
    """
    ownership = {"user_id": user["id"]} if owner == "personal" else {"company_id": company["id"]}
    response = await api.post(
        "/v1/api-tokens",
        json={**ownership, "name": "Before format change", "scopes": ["read", "execute"]},
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    old_secret = "thr_" + "Ab0_" * 10 + "-X9"
    async with app.state.db.write() as session:
        row = await session.get(AccessToken, identifier)
        row.token_hash = digest(old_secret)
        if historical:
            await move_to_legacy(session, identifier)
    headers = {"Authorization": "Bearer " + old_secret}
    assert (await client.get("/v1/auth/check", headers=headers)).status_code == 200
    assert (
        await api.patch(f"/v1/access-tokens/{identifier}", json={"revoke": True})
    ).status_code == 200
    revoked = await client.get("/v1/auth/check", headers=headers)
    assert revoked.status_code == 401 and revoked.json()["code"] == "token_invalid"


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
    api, client, app, user, access_token
):
    """Classify registry entries by recorded purpose, never by their names.

    An unclassified historical secret stays usable through the compatibility API
    but is excluded from the API-token registry. A newly issued API token named
    ``login`` must still appear there.
    """
    async with app.state.db.write() as session:
        await move_to_legacy(session, access_token["id"])
    result = await api.get("/v1/api-tokens")
    assert result.json() == [] and result.headers["x-total-count"] == "0"
    # Classification changes the registry only: old API credentials and logins
    # remain usable and accessible through the existing low-level endpoint.
    auth = {"Authorization": "Bearer " + access_token["token"]}
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 200
    response = await api.get("/v1/access-tokens")
    assert response.status_code == 200, response.text
    historical = response.json()
    assert {row["purpose"] for row in historical} == {"legacy"}
    assert access_token["id"] in {row["id"] for row in historical}
    response = await api.post(
        "/v1/api-tokens",
        json={"user_id": user["id"], "name": "login", "scopes": ["read"]},
    )
    assert response.status_code == 201
    listed = await api.get("/v1/api-tokens")
    assert listed.headers["x-total-count"] == "1"
    assert [(row["id"], row["name"], row["purpose"]) for row in listed.json()] == [
        (response.json()["id"], "login", "api")
    ]


@pytest.mark.parametrize("owner_filter", ["all", "personal", "company"])
async def test_only_explicit_api_tokens_count_towards_filtered_registry_pages(
    api, app, user, company, owner_filter
):
    """Old login-like rows must be excluded before owner filtering, counts and pagination, without hiding real API tokens with identical names."""
    issued = []
    for owner in ({"user_id": user["id"]}, {"company_id": company["id"]}):
        for index in range(3):
            response = await api.post(
                "/v1/api-tokens", json={**owner, "name": "login", "scopes": ["read"]}
            )
            assert response.status_code == 201
            token = response.json()
            if index == 1:
                async with app.state.db.write() as session:
                    await move_to_legacy(session, token["id"])
            else:
                issued.append(token)
    # A further modern login must stay outside the API registry as well.
    signed_in = await api.post(
        "/v1/auth/login", json={"login": user["email"], "password": user["password"]}
    )
    assert signed_in.status_code == 200
    filters = (
        {"user_id": user["id"]}
        if owner_filter == "personal"
        else ({"company_id": company["id"]} if owner_filter == "company" else {})
    )
    expected = {
        row["id"] for row in issued if all(row[key] == value for key, value in filters.items())
    }
    actual = []
    for offset in range(len(expected) + 1):
        page = await api.get("/v1/api-tokens", params={**filters, "limit": 1, "offset": offset})
        assert page.status_code == 200
        assert page.headers["x-total-count"] == str(len(expected))
        assert len(page.json()) == (1 if offset < len(expected) else 0)
        actual.extend(row["id"] for row in page.json())
    assert len(actual) == len(expected) and set(actual) == expected


@pytest.mark.parametrize("owner", ["personal", "company"])
async def test_api_tokens_support_exactly_one_year_and_expire_at_the_boundary(
    api, client, user, company, clock, owner
):
    """The year option must use the API's full 365-day budget for either owner, with access ending exactly at expires_at."""
    ownership = {"user_id": user["id"]} if owner == "personal" else {"company_id": company["id"]}
    response = await api.post(
        "/v1/api-tokens",
        json={
            **ownership,
            "name": "Annual CI",
            "scopes": ["read", "execute"],
            "expires_in_seconds": 365 * 86400,
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()
    expires = datetime.fromisoformat(token["expires_at"])
    assert expires == clock.now + timedelta(days=365)
    auth = {"Authorization": "Bearer " + token["token"]}
    clock.now = expires - timedelta(microseconds=1)
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 200
    clock.now = expires
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 401


@pytest.mark.parametrize("duration", [0, -1, 365 * 86400 + 1])
async def test_invalid_finite_duration_is_rejected(api, user, duration):
    """Explicit unlimited duration must not turn zero, negative or over-budget finite lifetimes into valid inputs."""
    response = await api.post(
        "/v1/api-tokens",
        json={
            "user_id": user["id"],
            "name": "Invalid duration",
            "scopes": ["read"],
            "expires_in_seconds": duration,
        },
    )
    assert response.status_code == 422, response.text
    assert (await api.get("/v1/api-tokens")).json() == []


@pytest.mark.parametrize("owner", ["personal", "company"])
@pytest.mark.parametrize("endpoint", ["api-tokens", "access-tokens"])
async def test_unlimited_api_tokens_remain_valid_until_explicit_revocation(
    api, client, user, company, clock, owner, endpoint
):
    """Null expiry is a real non-expiring credential for both owners/endpoints, retains scopes and can still be revoked after decades."""
    ownership = {"user_id": user["id"]} if owner == "personal" else {"company_id": company["id"]}
    if endpoint == "access-tokens":
        created = await api.post("/v1/token-ownerships", json=ownership)
        assert created.status_code == 201
        ownership = {"ownership_id": created.json()["id"]}
    response = await api.post(
        f"/v1/{endpoint}",
        json={
            **ownership,
            "name": "Permanent CI",
            "scopes": ["read", "execute"],
            "expires_in_seconds": None,
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()
    assert token["expires_at"] is None
    listed = (await api.get("/v1/api-tokens")).json()
    assert len(listed) == 1 and listed[0]["expires_at"] is None and "token" not in listed[0]
    auth = {"Authorization": "Bearer " + token["token"]}
    clock.now += timedelta(days=365 * 100)
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 200
    assert (
        await client.post("/v1/companies", headers=auth, json={"name": "Denied"})
    ).status_code == 403
    # A fresh login can revoke the indefinite credential after the old login expired.
    signed = await client.post(
        "/v1/auth/login", json={"login": user["email"], "password": user["password"]}
    )
    assert signed.status_code == 200
    admin = {"Authorization": "Bearer " + signed.json()["access_token"]}
    assert (
        await client.patch(f"/v1/access-tokens/{token['id']}", headers=admin, json={"revoke": True})
    ).status_code == 200
    assert (await client.get("/v1/auth/check", headers=auth)).status_code == 401


async def test_omitted_lifetime_keeps_the_finite_default(api, user, clock):
    """Only explicit null requests unlimited duration; omitting the field must retain the existing one-day API default."""
    response = await api.post(
        "/v1/api-tokens", json={"user_id": user["id"], "name": "Default", "scopes": ["read"]}
    )
    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["expires_at"]) == clock.now + timedelta(days=1)


@pytest.mark.parametrize("offset", [0, 1, 2, 2**63 - 1])
async def test_compatibility_registry_paginates_across_independent_stores(
    api, other_api, app, user, other_user, company, offset
):
    """Apply ownership permissions to both credential stores before pagination.

    Foreign personal tokens and company tokens without tokens.read must never
    occupy a page or leak metadata. The combined list must keep global ID order,
    and the largest legal offset must return an empty page without overflowing.
    Public fields must match detail responses, including unlimited API expiry
    and a revoked historical credential, without exposing stored hashes.
    """
    membership = await api.post(
        "/v1/memberships",
        json={"company_id": company["id"], "user_id": other_user["id"], "permissions": []},
    )
    assert membership.status_code == 201, membership.text
    expected = {}
    for issuer, owner in (
        (api, {"user_id": user["id"]}),
        (api, {"company_id": company["id"]}),
        (other_api, {"user_id": other_user["id"]}),
    ):
        for legacy in (False, True):
            response = await issuer.post(
                "/v1/api-tokens", json={
                    **owner, "name": "Page", "scopes": ["read", "execute"],
                    "expires_in_seconds": 86400 if legacy else None,
                }
            )
            assert response.status_code == 201, response.text
            identifier = response.json()["id"]
            if legacy:
                async with app.state.db.write() as session:
                    await move_to_legacy(session, identifier)
            if owner.get("user_id") == other_user["id"]:
                path = f"/v1/access-tokens/{identifier}"
                if legacy:
                    assert (await other_api.patch(path, json={"revoke": True})).status_code == 200
                detail = await other_api.get(path)
                assert detail.status_code == 200
                expected[identifier] = detail.json()
    response = await other_api.get("/v1/access-tokens", params={"limit": 1, "offset": offset})
    assert response.status_code == 200, response.text
    assert response.json() == [expected[key] for key in sorted(expected)[offset : offset + 1]]
    assert all("token" not in row and "token_hash" not in row for row in response.json())


@pytest.mark.parametrize(
    "operation",
    [
        "rename",
        "revoke",
        "delete",
        "delete_owner",
        "reassign_owner",
        "password",
        "delete_user",
        "logout",
    ],
)
async def test_historical_credentials_keep_their_management_lifecycle(
    api, client, app, user, access_token, operation
):
    """Preserve management of historical credentials after separating storage.

    Rename keeps the secret usable; revocation, deletion and logout invalidate
    it, and attached ownership cannot be reassigned. Credential-only operations
    preserve the independent login, while password change or account deletion
    must invalidate that login as well.
    """
    async with app.state.db.write() as session:
        await move_to_legacy(session, access_token["id"])
    path = f"/v1/access-tokens/{access_token['id']}"
    response = await api.get(path)
    assert response.status_code == 200 and response.json()["purpose"] == "legacy"
    status = 401
    if operation == "rename":
        response = await api.patch(path, json={"name": "Renamed history"})
        assert response.status_code == 200 and response.json()["name"] == "Renamed history"
        status = 200
    elif operation == "revoke":
        assert (await api.patch(path, json={"revoke": True})).status_code == 200
    elif operation == "delete":
        assert (await api.delete(path)).status_code == 204
    elif operation in {"delete_owner", "reassign_owner"}:
        owner_path = f"/v1/token-ownerships/{access_token['ownership_id']}"
        if operation == "delete_owner":
            assert (await api.delete(owner_path)).status_code == 204
        else:
            assert (await api.patch(owner_path, json={"user_id": user["id"]})).status_code == 409
            status = 200
    elif operation == "password":
        assert (
            await api.patch(
                f"/v1/users/{user['id']}", json={"password": "a new sufficiently long password"}
            )
        ).status_code == 200
    elif operation == "delete_user":
        assert (await api.delete(f"/v1/users/{user['id']}")).status_code == 204
    else:
        assert (
            await client.delete(
                "/v1/auth/session",
                headers={
                    "Origin": "https://platform.mutating.tech",
                    "X-Requested-With": "ThrongDashboard",
                    "Cookie": "tmc_session=" + access_token["token"],
                },
            )
        ).status_code == 204
    assert (
        await client.get(
            "/v1/auth/check", headers={"Authorization": "Bearer " + access_token["token"]}
        )
    ).status_code == status
    assert (await api.get("/v1/users/me")).status_code == (
        401 if operation in {"password", "delete_user"} else 200
    )
