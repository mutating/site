"""Dashboard cookies, CSRF and proxy boundaries without touching production data."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app import Settings, create_app


def test_frontend_root_and_legacy_redirects_preserve_queries_and_api_sessions(
    tmp_path, monkeypatch
):
    """Serve root URLs and keep old bookmarks, assets and session transport working."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><script src="/assets/app.js"></script></html>')
    (dist / "assets/app.js").write_text("console.log('dashboard')")
    monkeypatch.setattr("server.app.ROOT", tmp_path)
    settings = Settings(session_key=Fernet.generate_key().decode(), _env_file=None)
    with TestClient(create_app(settings)) as client:
        for path in ("/", "/login", "/register", "/profile", "/organizations/123"):
            response = client.get(path)
            assert response.status_code == 200
            assert "/assets/app.js" in response.text
        assert client.get("/assets/app.js").text == "console.log('dashboard')"
        for old, new in (
            ("/account", "/"),
            ("/account?path=profile", "/?path=profile"),
            ("/account/?ref=old", "/?ref=old"),
            ("/account/login", "/login"),
            ("/account/profile", "/profile"),
            (
                "/account/power-cells/computers?user_id=123&page=2",
                "/power-cells/computers?user_id=123&page=2",
            ),
            ("/account/invitations/inv_token", "/invitations/inv_token"),
            ("/account/assets/app.js", "/assets/app.js"),
            ("/account//evil.example", "/evil.example"),
            ("/account/encoded%23fragment", "/encoded%23fragment"),
        ):
            response = client.get(old, follow_redirects=False)
            assert response.status_code == 308
            assert response.headers["location"] == new
        assert client.get("/account/api/session", follow_redirects=False).status_code == 401
        for path in (
            "/missing.js",
            "/api/session",
            "/health/ready",
            "/agent/v1/session",
            "/../pyproject.toml",
        ):
            assert client.get(path).status_code == 404


def test_proxy_requires_session_and_enforces_route_and_origin_boundaries():
    """Anonymous and cross-origin callers cannot use the dashboard as an API proxy."""
    settings = Settings(
        session_key=Fernet.generate_key().decode(),
        public_origin="https://mutating.tech",
        secure_cookie=True,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/account/api/session").status_code == 401
        assert client.get("/account/api/users/me").status_code == 401
        assert client.get("/account/api/access-tokens").status_code == 404
        assert (
            client.post("/account/api/session", json={"login": "x", "password": "x"}).status_code
            == 403
        )
        assert (
            client.post(
                "/account/api/session",
                headers={"Origin": "https://evil.example", "X-Requested-With": "ThrongDashboard"},
                json={},
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/account/api/session", cookies={"tmc_account_session": "malformed"}
            ).status_code
            == 401
        )
        assert client.get("/account/../pyproject.toml").status_code == 404


def test_expired_encrypted_cookie_is_rejected_before_api_request():
    """An expired session cannot be extended by replaying the encrypted cookie."""
    key = Fernet.generate_key()
    settings = Settings(session_key=key.decode(), _env_file=None)
    value = (
        Fernet(key)
        .encrypt(
            json.dumps(
                {
                    "token": "thr_test",
                    "token_id": "test",
                    "expires": (datetime.now(UTC) - timedelta(seconds=1)).timestamp(),
                }
            ).encode()
        )
        .decode()
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/account/api/session", cookies={"tmc_account_session": value})
        assert response.status_code == 401
        assert "Max-Age=0" in response.headers["set-cookie"]
        response = client.delete(
            "/account/api/session",
            headers={"Origin": settings.public_origin, "X-Requested-With": "ThrongDashboard"},
        )
        assert response.status_code == 204


def test_production_requires_persistent_session_key():
    """Production cannot accidentally use a process-local or development signing key."""
    import pytest

    with pytest.raises(RuntimeError, match="DASHBOARD_SESSION_KEY"):
        create_app(Settings(secure_cookie=True, session_key=None, _env_file=None))


def test_login_keeps_bearer_token_inside_encrypted_secure_cookie():
    """The browser receives a profile and encrypted cookie, never a bearer token in JSON."""
    key = Fernet.generate_key()
    settings = Settings(
        session_key=key.decode(),
        public_origin="https://mutating.tech",
        secure_cookie=True,
        _env_file=None,
    )
    profile = {"id": "user-id", "email": "user@example.com"}
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def handler(request):
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={
                    "access_token": "thr_secret",
                    "token": {"id": "token-id", "expires_at": expiry},
                },
            )
        assert request.headers["authorization"] == "Bearer thr_secret"
        return httpx.Response(200, json=profile)

    with TestClient(create_app(settings), base_url="https://mutating.tech") as client:
        transport = httpx.MockTransport(handler)
        client.app.state.client = httpx.AsyncClient(base_url="http://api.test", transport=transport)
        response = client.post(
            "/account/api/session",
            headers={"Origin": "https://mutating.tech", "X-Requested-With": "ThrongDashboard"},
            json={"login": "user@example.com", "password": "password"},
        )
        assert response.json() == profile
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert "thr_secret" not in cookie and "thr_secret" not in response.text
        assert client.get("/account/api/session").json() == profile


@pytest.mark.parametrize("stage", ["/auth/login", "/users/me"])
@pytest.mark.parametrize("failure", ["transport", "status"])
def test_registration_preserves_created_account_when_automatic_login_fails(stage, failure):
    """A committed registration remains successful when either subsequent API request fails."""
    settings = Settings(session_key=Fernet.generate_key().decode(), _env_file=None)
    profile = {"id": "created-user", "email": "new@example.com"}
    state = {"created": False, "fail": True}
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def handler(request):
        if request.url.path == "/api/v1/users":
            assert not state["created"], "The browser must never repeat successful registration"
            state["created"] = True
            return httpx.Response(201, json=profile)
        if state["fail"] and request.url.path.endswith(stage):
            if failure == "transport":
                raise httpx.ConnectError("Temporary outage", request=request)
            return httpx.Response(503, json={"detail": "Temporary outage"})
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={
                    "access_token": "thr_new",
                    "token": {"id": "new-token", "expires_at": expiry},
                },
            )
        return httpx.Response(200, json=profile)

    with TestClient(create_app(settings)) as client:
        upstream = httpx.AsyncClient(
            base_url="http://api.test", transport=httpx.MockTransport(handler)
        )
        client.app.state.client = upstream
        try:
            headers = {"Origin": settings.public_origin, "X-Requested-With": "ThrongDashboard"}
            response = client.post(
                "/account/api/register",
                headers=headers,
                json={"email": profile["email"], "password": "sufficiently long password"},
            )
            assert state["created"] and response.status_code == 201
            assert response.json() == {"account_created": True, "requires_login": True}
            assert "tmc_account_session" not in client.cookies
            state["fail"] = False
            login = client.post(
                "/account/api/session",
                headers=headers,
                json={"login": profile["email"], "password": "sufficiently long password"},
            )
            assert login.status_code == 200 and login.json() == profile
            assert client.get("/account/api/session").json() == profile
        finally:
            client.portal.call(upstream.aclose)
