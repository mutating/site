"""The dashboard serves public UI files and configuration without an API proxy."""

from fastapi.testclient import TestClient

from server.app import Settings, create_app


def test_frontend_root_routes_and_removed_account_prefix(tmp_path, monkeypatch):
    """Serve root pages and assets while removed and API URLs return 404."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><script src="/assets/app.js"></script></html>')
    (dist / "assets/app.js").write_text("console.log('dashboard')")
    monkeypatch.setattr("server.app.ROOT", tmp_path)
    with TestClient(create_app(Settings(_env_file=None))) as client:
        for path in ("/", "/login", "/register", "/profile", "/organizations/123"):
            response = client.get(path)
            assert response.status_code == 200
            assert "/assets/app.js" in response.text
        assert client.get("/assets/app.js").text == "console.log('dashboard')"
        for path in (
            "/account",
            "/account/",
            "/account/profile?ref=old",
            "/account/api/session",
            "/api/session",
            "/v1/users/me",
            "/missing.js",
            "/health/ready",
            "/agent/v1/session",
            "/../pyproject.toml",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 404
            assert "location" not in response.headers


def test_public_configuration_and_csp_point_to_the_same_api():
    """Configure direct browser requests and avatar images without any credentials."""
    for url in ("https://api.mutating.tech/v1", "http://127.0.0.1:8000/v1/"):
        with TestClient(create_app(Settings(api_url=url, _env_file=None))) as client:
            response = client.get("/config.js")
            assert response.status_code == 200
            assert response.text == f'window.__TMC_API_URL__ = "{url.rstrip("/")}";\n'
            assert response.headers["content-type"].startswith("application/javascript")
            assert "set-cookie" not in response.headers
            assert (
                "connect-src 'self' " + url.split("/v1")[0]
                in response.headers["content-security-policy"]
            )
            assert response.headers["cache-control"] == "no-store"


def test_missing_build_reports_unavailable(tmp_path, monkeypatch):
    """Report an absent UI build without pretending that the app is ready."""
    monkeypatch.setattr("server.app.ROOT", tmp_path)
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/").status_code == 503
