"""Direct browser authentication shares the API's revocable access tokens."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from throng_api.models import AccessToken
from throng_api.security import SESSION_COOKIE, digest

ORIGIN = "https://platform.mutating.tech"
CSRF = {"Origin": ORIGIN, "X-Requested-With": "ThrongDashboard"}
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"browser_secure_cookie": False}], indirect=True),
]


@pytest.fixture(autouse=True)
def current_cookie_clock(clock):
    """Keep browser cookie expiry in the future while retaining an adjustable API clock."""
    clock.now = datetime.now(UTC)


async def sign_in(client, user):
    """Log in through the public browser endpoint using the real user's credentials."""
    response = await client.post(
        "/v1/auth/session",
        headers=CSRF,
        json={"login": user["email"], "password": user["password"]},
    )
    assert response.status_code == 200, response.text
    return response


async def test_browser_login_uses_existing_token_storage(client, user, app):
    """Keep the token out of JSON and store only its hash in the existing token table."""
    response = await sign_in(client, user)
    assert response.json()["id"] == user["id"]
    assert "access_token" not in response.json()
    cookie = response.cookies[SESSION_COOKIE]
    assert cookie.startswith("thr_") and cookie not in response.text
    assert all(
        value in response.headers["set-cookie"]
        for value in ("HttpOnly", "SameSite=lax", "Path=/v1/")
    )
    assert "Domain=" not in response.headers["set-cookie"]
    async with app.state.db.read() as session:
        token = await session.scalar(
            select(AccessToken).where(AccessToken.token_hash == digest(cookie))
        )
        assert token and token.created_by_user_id == user["id"]
        assert token.token_hash != cookie
    for path in ("/v1/auth/session", "/v1/users/me"):
        profile = await client.get(path, headers={"Origin": ORIGIN})
        assert profile.status_code == 200 and profile.json()["id"] == user["id"]
        assert profile.headers["access-control-allow-origin"] == ORIGIN
        assert profile.headers["access-control-allow-credentials"] == "true"
        assert profile.headers["cache-control"] == "no-store"
    assert (
        await client.get("/v1/users/me", headers={"Authorization": "Bearer invalid"})
    ).status_code == 401
    assert (await client.get("/v1/users/me")).status_code == 200
    # Browser cookies never authenticate computer agents.
    assert (
        await client.post(
            "/agent/v1/session", json={"clean_start": True}, headers={"Idempotency-Key": "browser"}
        )
    ).status_code == 401


@pytest.mark.parametrize(
    "bad_headers",
    [
        {},
        {"Origin": ORIGIN},
        {"Origin": "https://evil.example", "X-Requested-With": "ThrongDashboard"},
        {"Origin": ORIGIN + ".evil.example", "X-Requested-With": "ThrongDashboard"},
    ],
)
async def test_browser_writes_require_exact_origin_and_custom_header(client, user, bad_headers):
    """Block login CSRF and cookie-authenticated writes from untrusted pages."""
    credentials = {"login": user["email"], "password": user["password"]}
    assert (
        await client.post("/v1/auth/session", json=credentials, headers=bad_headers)
    ).status_code == 403
    await sign_in(client, user)
    assert (
        await client.post("/v1/companies", json={"name": "forbidden"}, headers=bad_headers)
    ).status_code == 403
    assert (
        await client.post("/v1/companies", json={"name": "allowed"}, headers=CSRF)
    ).status_code == 201


async def test_cors_preflights_only_allow_the_dashboard(client):
    """Allow credentialed browser requests and expose pagination counts to the UI."""
    headers = {
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "content-type,x-requested-with",
    }
    allowed = await client.options("/v1/users/me", headers=headers)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = await client.options(
        "/v1/users/me", headers={**headers, "Origin": "https://evil.example"}
    )
    assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
    response = await client.get("/v1/users/me", headers={"Origin": ORIGIN})
    assert "x-total-count" in response.headers["access-control-expose-headers"].lower()


async def test_logout_revokes_the_actual_api_token_and_is_idempotent(client, user):
    """A cookie or bearer copied before logout cannot be replayed after the token is deleted."""
    response = await sign_in(client, user)
    raw = response.cookies[SESSION_COOKIE]
    response = await client.delete("/v1/auth/session", headers=CSRF)
    assert response.status_code == 204 and SESSION_COOKIE not in client.cookies
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (
        await client.get("/v1/users/me", headers={"Authorization": "Bearer " + raw})
    ).status_code == 401
    assert (
        await client.get("/v1/users/me", headers={"Cookie": f"{SESSION_COOKIE}={raw}"})
    ).status_code == 401
    assert (await client.delete("/v1/auth/session", headers=CSRF)).status_code == 204


async def test_expired_invalid_and_revoked_cookies_are_cleared(client, user, clock):
    """Validate expiry and revocation in the API rather than trusting the browser's cookie."""
    response = await sign_in(client, user)
    raw = response.cookies[SESSION_COOKIE]
    clock.now += timedelta(days=2)
    response = await client.get("/v1/auth/session", headers={"Cookie": f"{SESSION_COOKIE}={raw}"})
    assert response.status_code == 401 and "Max-Age=0" in response.headers["set-cookie"]
    response = await client.get("/v1/auth/session", headers={"Cookie": f"{SESSION_COOKIE}=invalid"})
    assert response.status_code == 401 and "Max-Age=0" in response.headers["set-cookie"]
    assert (
        await client.delete(
            "/v1/auth/session", headers={**CSRF, "Cookie": f"{SESSION_COOKIE}=invalid"}
        )
    ).status_code == 204


async def test_failed_login_and_account_deletion_clear_browser_cookie(client, user):
    """Reject wrong passwords and remove the session when the user deletes their own account."""
    assert (
        await client.post(
            "/v1/auth/session",
            headers=CSRF,
            json={"login": user["email"], "password": "incorrect password"},
        )
    ).status_code == 401
    await sign_in(client, user)
    response = await client.delete(f"/v1/users/{user['id']}", headers=CSRF)
    assert response.status_code == 204 and SESSION_COOKIE not in client.cookies
    assert (await client.get("/v1/auth/session")).status_code == 401


async def test_production_cookie_is_secure_and_host_only(client, user, settings):
    """Issue a Secure cookie scoped to the API host and versioned routes."""
    settings.browser_secure_cookie = True
    response = await sign_in(client, user)
    assert "Secure" in response.headers["set-cookie"]
    assert "Domain=" not in response.headers["set-cookie"]
