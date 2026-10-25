"""Login sessions and API credentials have independent storage and lifecycles."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.platform.api.token_helpers import move_to_legacy
from throng_api.security import SESSION_COOKIE, digest

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"browser_secure_cookie": False}], indirect=True),
]
CSRF = {"Origin": "https://platform.mutating.tech", "X-Requested-With": "ThrongDashboard"}


@pytest.fixture(autouse=True)
def current_cookie_clock(clock):
    """Keep cookie expiry in the future of the HTTP client's real clock."""
    clock.now = datetime.now(UTC)


async def count(app, table):
    """Read real storage; callers supply only the fixed table names in this module."""
    async with app.state.db.read() as session:
        return await session.scalar(text(f"SELECT COUNT(*) FROM {table}"))


async def issue(api, user, *, expires_in_seconds=None):
    """Create an explicit personal API credential through HTTP, indefinite unless requested otherwise."""
    response = await api.post(
        "/v1/api-tokens",
        json={
            "user_id": user["id"],
            "name": "Terminal",
            "scopes": ["read", "execute"],
            "expires_in_seconds": expires_in_seconds,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def peer_auth_headers(other_api, other_user, client, app):
    """Seed a working session/API/legacy credential for another user to detect unscoped bulk cleanup."""
    token = await issue(other_api, other_user)
    historical = await issue(other_api, other_user, expires_in_seconds=86400)
    async with app.state.db.write() as session:
        await move_to_legacy(session, historical["id"])
    headers = [
        other_user["headers"],
        {"Authorization": "Bearer " + token["token"]},
        {"Authorization": "Bearer " + historical["token"]},
    ]
    for credential in headers:
        assert (await client.get("/v1/auth/check", headers=credential)).status_code == 200
    return headers


async def test_logins_create_no_api_tokens_or_token_ownerships(client, user, app):
    """Keep login creation out of the API-token and ownership tables.

    Both Bearer and browser logins must persist hashed sessions instead of API
    tokens. The Bearer login response must also retain a finite expiry.
    """
    assert await count(app, "access_tokens") == 0
    assert await count(app, "token_ownerships") == 0
    credentials = {"login": user["email"], "password": user["password"]}
    bearer = await client.post("/v1/auth/login", json=credentials)
    browser = await client.post("/v1/auth/session", json=credentials, headers=CSRF)
    assert bearer.status_code == browser.status_code == 200
    assert "access_token" not in browser.json()
    assert await count(app, "access_tokens") == await count(app, "token_ownerships") == 0
    assert await count(app, "login_sessions") == 3
    async with app.state.db.read() as session:
        hashes = set(await session.scalars(text("SELECT session_hash FROM login_sessions")))
    assert digest(bearer.json()["access_token"]) in hashes
    assert digest(browser.cookies[SESSION_COOKIE]) in hashes
    assert bearer.json()["token"]["expires_at"] is not None
    assert "session_hash" not in bearer.text and "token_hash" not in bearer.text
    assert (await client.get("/v1/access-tokens", headers=user["headers"])).json() == []


async def test_session_revocation_is_separate_from_api_token_crud(
    api, client, other_api, user, app
):
    """Keep session revocation separate from API-token CRUD and ownership.

    A login ID must be hidden from token CRUD, a token ID must not be revocable
    as a session, and another user cannot revoke the login. Revoking one own
    session must leave the API credential and the other own login usable.
    """
    token = await issue(api, user)
    signed = await client.post(
        "/v1/auth/login", json={"login": user["email"], "password": user["password"]}
    )
    assert (await api.delete(f"/v1/auth/sessions/{token['id']}")).status_code == 404
    identifier = signed.json()["token"]["id"]
    for operation in [api.get, api.delete]:
        assert (await operation(f"/v1/access-tokens/{identifier}")).status_code == 404
    assert (
        await api.patch(f"/v1/access-tokens/{identifier}", json={"revoke": True})
    ).status_code == 404
    assert (await other_api.delete(f"/v1/auth/sessions/{identifier}")).status_code == 404
    assert (await api.delete(f"/v1/auth/sessions/{identifier}")).status_code == 204
    assert (
        await client.get(
            "/v1/users/me", headers={"Authorization": "Bearer " + signed.json()["access_token"]}
        )
    ).status_code == 401
    assert (
        await client.get("/v1/auth/check", headers={"Authorization": "Bearer " + token["token"]})
    ).status_code == 200
    assert (await api.get("/v1/users/me")).status_code == 200
    assert await count(app, "access_tokens") == 1


async def test_browser_logout_cannot_delete_an_api_token(api, client, user, app):
    """Treat an API secret in the session cookie as unrelated to real logins.

    Logging out with that cookie must leave the API secret and the real session
    usable. A subsequent logout with the genuine session cookie must invalidate
    that session.
    """
    token = await issue(api, user)
    signed = await client.post(
        "/v1/auth/session",
        json={"login": user["email"], "password": user["password"]},
        headers=CSRF,
    )
    cookie = signed.cookies[SESSION_COOKIE]
    assert (
        await client.delete(
            "/v1/auth/session", headers={**CSRF, "Cookie": f"{SESSION_COOKIE}={token['token']}"}
        )
    ).status_code == 204
    assert (
        await client.get("/v1/auth/check", headers={"Authorization": "Bearer " + token["token"]})
    ).status_code == 200
    assert (
        await client.get("/v1/users/me", headers={"Authorization": "Bearer " + cookie})
    ).status_code == 200
    assert (
        await client.delete(
            "/v1/auth/session", headers={**CSRF, "Cookie": f"{SESSION_COOKIE}={cookie}"}
        )
    ).status_code == 204
    assert (
        await client.get("/v1/users/me", headers={"Authorization": "Bearer " + cookie})
    ).status_code == 401
    assert await count(app, "access_tokens") == 1


async def test_api_owner_deletion_does_not_affect_a_login(api, client, user, peer_auth_headers):
    """Deleting API ownership must preserve logins and another user's API/legacy access; an unscoped credential DELETE must not erase unrelated tokens."""
    token = await issue(api, user)
    assert (await api.delete(f"/v1/token-ownerships/{token['ownership_id']}")).status_code == 204
    assert (await api.get("/v1/users/me")).status_code == 200
    for headers in peer_auth_headers:
        assert (await client.get("/v1/auth/check", headers=headers)).status_code == 200


async def test_password_change_revokes_both_independent_stores(
    api, client, user, peer_auth_headers
):
    """Password change must revoke the issuer's session and indefinite API token, while preserving another user's session/API/legacy access against an unscoped bulk update."""
    token = await issue(api, user)
    changed = await api.patch(
        f"/v1/users/{user['id']}", json={"password": "a new sufficiently long password"}
    )
    assert changed.status_code == 200, changed.text
    for raw in (token["token"], user["login"]["access_token"]):
        assert (
            await client.get("/v1/users/me", headers={"Authorization": "Bearer " + raw})
        ).status_code == 401

    for headers in peer_auth_headers:
        assert (await client.get("/v1/auth/check", headers=headers)).status_code == 200


async def test_account_deletion_cleans_up_both_stores(api, client, user, peer_auth_headers, app):
    """Account cleanup must remove its credentials and ownerships without foreign-key failures or unscoped DELETEs that erase another user's session/API/legacy access."""
    await issue(api, user)
    assert (await api.delete(f"/v1/users/{user['id']}")).status_code == 204
    for headers in peer_auth_headers:
        assert (await client.get("/v1/auth/check", headers=headers)).status_code == 200
    for table in ("login_sessions", "access_tokens", "legacy_credentials"):
        assert await count(app, table) == 1
    assert await count(app, "token_ownerships") == 2


async def test_sessions_keep_finite_expiry(client, user, clock):
    """Indefinite API credentials must not make logins indefinite or allow a login request to override its configured TTL."""
    credentials = {"login": user["email"], "password": user["password"]}
    assert (
        await client.post("/v1/auth/login", json={**credentials, "expires_in_seconds": None})
    ).status_code == 422
    clock.now += timedelta(days=2)
    assert (await client.get("/v1/users/me", headers=user["headers"])).status_code == 401
