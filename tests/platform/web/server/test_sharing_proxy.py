"""The dashboard forwards only intended organization-sharing operations with protected sessions."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app import Settings, create_app

ID = "12345678-1234-1234-1234-123456789abc"
TOKEN = "inv_" + "a" * 43


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"companies/{ID}"),
        ("GET", f"companies/{ID}/users/lookup?identifier=alice"),
        ("GET", "invitations"),
        ("GET", f"invitations/link/{TOKEN}"),
        ("GET", f"memberships/{ID}/avatar"),
        ("POST", "memberships"),
        ("POST", "invitations"),
        ("POST", f"invitations/{ID}/accept"),
        ("POST", f"invitations/{ID}/decline"),
        ("POST", f"invitations/{ID}/link"),
        ("POST", f"invitations/link/{TOKEN}/accept"),
        ("PATCH", f"memberships/{ID}"),
        ("PATCH", f"companies/{ID}"),
        ("DELETE", f"invitations/{ID}"),
    ],
)
def test_sharing_routes_forward_session_and_preserve_api_contract(method, path):
    """Each allowed operation keeps the bearer on the server, preserves payloads and requires CSRF on writes."""
    key = Fernet.generate_key()
    settings = Settings(
        session_key=key.decode(), public_origin="https://mutating.tech", _env_file=None
    )
    cookie = (
        Fernet(key)
        .encrypt(
            json.dumps(
                {
                    "token": "thr_private",
                    "token_id": ID,
                    "expires": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
                }
            ).encode()
        )
        .decode()
    )
    called = []

    def handler(request):
        called.append(request)
        assert request.headers["authorization"] == "Bearer thr_private"
        assert request.url.path == "/v1/" + path.split("?")[0]
        if "?" in path:
            assert request.url.params["identifier"] == "alice"
        if method in {"POST", "PATCH"}:
            assert json.loads(request.content) == {"role": "user", "permissions": []}
        return httpx.Response(200, json={"success": True}, headers={"X-Total-Count": "2"})

    with TestClient(create_app(settings), base_url=settings.public_origin) as client:
        upstream = httpx.AsyncClient(
            base_url="http://api.test", transport=httpx.MockTransport(handler)
        )
        client.app.state.client = upstream
        try:
            headers = {
                "Cookie": f"tmc_account_session={cookie}",
                "Origin": settings.public_origin,
                "X-Requested-With": "ThrongDashboard",
            }
            kwargs = (
                {"json": {"role": "user", "permissions": []}} if method in {"POST", "PATCH"} else {}
            )
            response = client.request(method, "/account/api/" + path, headers=headers, **kwargs)
            assert response.status_code == 200 and response.json() == {"success": True}
            assert response.headers["x-total-count"] == "2" and "thr_private" not in response.text
            if method != "GET":
                wrong = {**headers, "Origin": "https://evil.example"}
                assert (
                    client.request(
                        method, "/account/api/" + path, headers=wrong, **kwargs
                    ).status_code
                    == 403
                )
            assert len(called) == 1
            assert (
                client.get("/account/api/invitations/link/not-a-token", headers=headers).status_code
                == 404
            )
            assert client.get(f"/account/api/users/{ID}", headers=headers).status_code == 404
        finally:
            client.portal.call(upstream.aclose)
