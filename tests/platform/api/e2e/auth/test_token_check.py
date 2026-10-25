"""Execution-token preflight uses the same live authentication as actual API work."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from throng_api.models import Base
from throng_api.security import SESSION_COOKIE

pytestmark = pytest.mark.anyio
CHECK = "/v1/auth/check"


@pytest.fixture
async def token_pair(api, token_ownership, company, request):
    """Issue matching personal and company tokens through the real public API."""
    response = await api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201, response.text
    owners = {"personal": token_ownership["id"], "company": response.json()["id"]}
    tokens = {}
    for kind, ownership_id in owners.items():
        payload = {
            "ownership_id": ownership_id,
            "name": "Execution preflight",
            "scopes": ["read", "execute"],
            "expires_in_seconds": 60,
            **getattr(request, "param", {}),
        }
        response = await api.post("/v1/access-tokens", json=payload)
        assert response.status_code == 201, response.text
        tokens[kind] = response.json()
    return tokens


@pytest.mark.parametrize(
    "token_pair",
    [
        {"scopes": ["read"]},
        {"scopes": ["execute"]},
        {"scopes": ["manage"]},
        {"scopes": ["read", "manage"]},
        {"scopes": ["execute", "manage"]},
        {"scopes": ["read", "execute"]},
        {"scopes": ["manage", "read", "execute"]},
    ],
    indirect=True,
)
async def test_token_check_requires_both_scopes_for_each_owner(client, token_pair, caplog):
    """Require read and execute together without requiring manage or an existing computer."""
    caplog.clear()
    for kind, token in token_pair.items():
        response = await client.get(CHECK, headers={"Authorization": "Bearer " + token["token"]})
        allowed = {"read", "execute"} <= set(token["scopes"])
        assert response.status_code == (200 if allowed else 403), (kind, response.text)
        assert response.json() == (
            {"valid": True, "scopes": sorted(token["scopes"])}
            if allowed
            else {"detail": "Token lacks required scope"}
        )
        assert response.headers["cache-control"] == "no-store"
        assert token["token"] not in response.text
        assert token["token"] not in caplog.text


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer", "Bearer thr_unknown"])
async def test_token_check_rejects_missing_and_invalid_credentials(client, authorization):
    """Return an uncached token_invalid preflight result for unusable credentials.

    Missing or malformed Bearer headers must also retain WWW-Authenticate, so
    external clients can recognize an authentication failure consistently.
    """
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await client.get(CHECK, headers=headers)
    assert response.status_code == 401, response.text
    assert response.headers["cache-control"] == "no-store"
    if authorization == "Bearer thr_unknown":
        assert response.json() == {"detail": "Invalid token", "code": "token_invalid"}
    else:
        assert response.json() == {"detail": "Bearer token required", "code": "token_invalid"}
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("mode", ["revoked", "deleted", "password_changed"])
async def test_token_check_rechecks_revocation_and_deletion(api, client, user, token_pair, mode):
    """Recheck personal and company tokens after revoke, delete or password reset.

    A previous successful preflight must not mask invalidation: the next call
    returns an uncached 401 with token_invalid.
    """
    for token in token_pair.values():
        response = await client.get(CHECK, headers={"Authorization": "Bearer " + token["token"]})
        assert response.status_code == 200, response.text
    if mode == "password_changed":
        response = await api.patch(
            f"/v1/users/{user['id']}", json={"password": "a new sufficiently long password"}
        )
        assert response.status_code == 200, response.text
    else:
        for token in token_pair.values():
            path = f"/v1/access-tokens/{token['id']}"
            if mode == "revoked":
                response = await api.patch(path, json={"revoke": True})
                assert response.status_code == 200, response.text
            else:
                response = await api.delete(path)
                assert response.status_code == 204, response.text
    for token in token_pair.values():
        response = await client.get(CHECK, headers={"Authorization": "Bearer " + token["token"]})
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "Invalid token", "code": "token_invalid"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("offset_microseconds", [-1, 0, 1])
async def test_token_check_enforces_the_exact_expiration_boundary(
    client, token_pair, clock, offset_microseconds
):
    """Expire both owners' tokens exactly at the server-side deadline.

    Preflight succeeds one microsecond before expiry, then returns an uncached
    401 with token_expired at and after expiry, rather than token_invalid.
    """
    expiry = datetime.fromisoformat(token_pair["personal"]["expires_at"])
    clock.now = expiry + timedelta(microseconds=offset_microseconds)
    for token in token_pair.values():
        response = await client.get(CHECK, headers={"Authorization": "Bearer " + token["token"]})
        assert response.status_code == (200 if offset_microseconds < 0 else 401), response.text
        if offset_microseconds >= 0:
            assert response.json() == {"detail": "Token has expired", "code": "token_expired"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("membership", ["creator"], indirect=True)
@pytest.mark.parametrize(
    "mode,permissions,status",
    [
        ("removed", None, 401),
        ("demoted", None, 401),
        ("restricted", ["execution.read", "execution.run"], 401),
        ("restricted", ["tokens.manage", "execution.run"], 403),
        ("restricted", ["tokens.manage", "execution.read"], 403),
        ("restricted", ["tokens.manage"], 403),
        ("restricted", ["tokens.manage", "execution.read", "execution.run"], 200),
    ],
)
async def test_company_token_check_follows_current_issuer_permissions(
    api, other_api, client, company, membership, user, token_pair, mode, permissions, status
):
    """Validate execution permissions in the token's company without borrowing personal or other-company rights."""
    response = await api.post("/v1/companies", json={"name": "Unrelated full-access company"})
    assert response.status_code == 201, response.text
    response = await api.get("/v1/memberships", params={"company_id": company["id"]})
    assert response.status_code == 200, response.text
    issuer = next(row for row in response.json() if row["user_id"] == user["id"])
    headers = {"Authorization": "Bearer " + token_pair["company"]["token"]}
    response = await client.get(CHECK, headers=headers)
    assert response.status_code == 200, response.text
    if mode == "removed":
        response = await other_api.delete(f"/v1/memberships/{issuer['id']}")
        assert response.status_code == 204, response.text
    else:
        change = (
            {"role": "user"} if mode == "demoted" else {"role": "admin", "permissions": permissions}
        )
        response = await other_api.patch(f"/v1/memberships/{issuer['id']}", json=change)
        assert response.status_code == 200, response.text
    response = await client.get(CHECK, headers=headers)
    assert response.status_code == 401, response.text
    assert response.headers["cache-control"] == "no-store"
    if status != 401:
        # Reducing membership rights revokes old tokens even when tokens.manage
        # remains. A fresh token exercises the current execution permissions.
        response = await api.post(
            "/v1/access-tokens",
            json={
                "ownership_id": token_pair["company"]["ownership_id"],
                "name": "Issued with current permissions",
                "scopes": ["read", "execute"],
            },
        )
        assert response.status_code == 201, response.text
        headers = {"Authorization": "Bearer " + response.json()["token"]}
        response = await client.get(CHECK, headers=headers)
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
    if status == 403:
        assert response.json() == {"detail": "Organization permission required"}
        response = await other_api.patch(
            f"/v1/memberships/{issuer['id']}",
            json={"permissions": sorted(set(permissions) | {"execution.read", "execution.run"})},
        )
        assert response.status_code == 200, response.text
        response = await client.get(CHECK, headers=headers)
        assert response.status_code == 200, response.text
    personal = await client.get(
        CHECK, headers={"Authorization": "Bearer " + token_pair["personal"]["token"]}
    )
    assert personal.status_code == 200, personal.text


async def test_token_check_preserves_bearer_precedence_over_cookies(client, token_pair):
    """Accept normal bearer/cookie authentication, but never hide an invalid header with a valid cookie."""
    for token in token_pair.values():
        for scheme in ["Bearer", "bearer", "bEaReR"]:
            response = await client.get(
                CHECK, headers={"Authorization": scheme + " " + token["token"]}
            )
            assert response.status_code == 200, response.text
        response = await client.get(
            CHECK,
            headers={
                "Authorization": "Bearer " + token["token"],
                "Cookie": f"{SESSION_COOKIE}=invalid",
            },
        )
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers
        cookie = {"Cookie": f"{SESSION_COOKIE}={token['token']}"}
        response = await client.get(CHECK, headers=cookie)
        assert response.status_code == 200, response.text
        for authorization in ["", "Bearer thr_invalid", "Basic abc", "Bearer"]:
            response = await client.get(CHECK, headers={**cookie, "Authorization": authorization})
            assert response.status_code == 401, response.text
            assert response.headers["cache-control"] == "no-store"
            assert "set-cookie" not in response.headers


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_token_check_does_not_modify_database_or_extend_tokens(api, client, app, token_pair):
    """Repeated preflights change no table, reserve no capacity and never extend token lifetime."""
    async with app.state.db.read() as session:
        before = {
            table.name: (
                await session.execute(select(table).order_by(*table.primary_key.columns))
            ).all()
            for table in Base.metadata.sorted_tables
        }
    for _ in range(3):
        for token in token_pair.values():
            response = await client.get(
                CHECK, headers={"Authorization": "Bearer " + token["token"]}
            )
            assert response.status_code == 200, response.text
    async with app.state.db.read() as session:
        after = {
            table.name: (
                await session.execute(select(table).order_by(*table.primary_key.columns))
            ).all()
            for table in Base.metadata.sorted_tables
        }
    assert after == before


async def test_token_check_openapi_contract(client):
    """Publish a protected GET with a fixed scope check, explicit output, and both rejection statuses."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()
    operation = schema["paths"][CHECK]["get"]
    assert operation["security"] == [{"AccessToken": []}, {"BrowserSession": []}]
    assert "requestBody" not in operation
    assert not operation.get("parameters")
    assert {"200", "401", "403"} <= operation["responses"].keys()
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TokenCheck"
    }
    model = schema["components"]["schemas"]["TokenCheck"]
    assert set(model["required"]) == {"valid", "scopes"}
    assert set(model["properties"]) == {"valid", "scopes"}
    assert model["properties"]["valid"]["const"] is True
    scopes = model["properties"]["scopes"]
    assert scopes["type"] == "array"
    assert set(scopes["items"]["enum"]) == {"read", "execute", "manage"}
