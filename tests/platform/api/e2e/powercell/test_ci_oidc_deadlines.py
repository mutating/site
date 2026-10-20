"""Actions identities must remain valid until actual reservation authorization."""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from tests.platform.api.github_http_peer import http_peer, response
from throng_api import github_client, github_oidc
from throng_api.models import CIReservation

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.mark.parametrize("endpoint", ["reserve", "cancel"])
@pytest.mark.parametrize("lifetime", [1, 2])
async def test_expired_identity_after_jwks_fetch_cannot_mutate_ci(
    client, app, clock, github, ci_peer, route, monkeypatch, endpoint, lifetime
):
    """A valid signature cannot authorize creation or cancellation after token expiration.

    A real JWKS HTTP peer advances both clocks while the request is in flight;
    even a correctly signed token must fail before any reservation is persisted.
    """
    original = github.service.client._transport
    original_jwks = github_oidc.JWKS_URL
    elapsed = SimpleNamespace(now=0)
    github.service.oidc.monotonic = lambda: elapsed.now

    async def handler(request, writer):
        upstream = await original.handler(httpx.Request("GET", original_jwks))
        clock.now += timedelta(seconds=2)
        elapsed.now += 2
        response(writer, upstream.json())

    token = ci_peer.token(exp=int(clock.now.timestamp()) + lifetime)
    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_oidc, "JWKS_URL", peer.url)
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        result = await client.post(
            f"/v1/github/ci/routes/{route['id']}/{endpoint}",
            headers={"Authorization": "Bearer " + token},
            json={"instance": "expired"},
        )
    assert result.status_code == 401, result.text
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CIReservation)) == 0
