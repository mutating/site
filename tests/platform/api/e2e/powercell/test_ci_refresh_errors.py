"""User-token refresh failures obey the same safe boundary as other GitHub requests."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from tests.platform.api.e2e.test_github import connect
from tests.platform.api.e2e.test_github import github as github
from tests.platform.api.github_http_peer import http_peer, response
from tests.platform.api.unit.test_github_transport_deadlines import bounded_time
from throng_api import github_client
from throng_api.ci import verify_repository
from throng_api.github_client import GitHubError
from throng_api.models import CIRoute

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
    pytest.mark.parametrize("fault", ["unavailable", "limited", "limited_delayed", "timeout"]),
]


@pytest.fixture
async def failing_refresh(api, github, clock, monkeypatch, fault):
    """Expire real encrypted credentials, then fail only GitHub's actual HTTP endpoint."""
    await connect(api)
    clock.now += timedelta(hours=8)
    # Only the timeout scenario compresses the real request budget. A valid
    # provider status must survive ordinary scheduling/TCP delays above 50 ms.
    if fault == "timeout":
        bounded_time(monkeypatch, github_client, 10)

    async def handler(request, writer):
        assert request.path == "/login/oauth/access_token"
        if fault == "timeout":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n{")
            for _ in range(40):
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(0.005)
        else:
            if fault == "limited_delayed":
                await asyncio.sleep(0.08)
            response(
                writer,
                {"private": "provider-secret"},
                status=429 if fault.startswith("limited") else 503,
                headers={"Retry-After": "120"} if fault.startswith("limited") else {},
            )

    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_client, "TOKEN_URL", peer.url + "/login/oauth/access_token")
        yield SimpleNamespace(
            status=503 if fault.startswith("limited") else 502,
            retry_after="120" if fault.startswith("limited") else None,
        )


async def test_create_binding_reports_refresh_failure_without_a_server_error(
    api, app, failing_refresh
):
    """Refresh is part of GitHub verification and must not escape as an unhandled HTTP500."""
    result = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
        },
    )
    assert result.status_code == failing_refresh.status, result.text
    assert result.headers.get("retry-after") == failing_refresh.retry_after
    assert "provider-secret" not in result.text
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CIRoute)) == 0


async def test_repository_verification_maps_refresh_errors_at_its_boundary(
    app, user, failing_refresh
):
    """Direct verification preserves a sanitized provider cause and its retry deadline."""
    with pytest.raises(HTTPException) as caught:
        await verify_repository(SimpleNamespace(app=app), user["id"], 456, 42)
    assert caught.value.status_code == failing_refresh.status
    assert (caught.value.headers or {}).get("Retry-After") == failing_refresh.retry_after
    assert isinstance(caught.value.__cause__, GitHubError)
