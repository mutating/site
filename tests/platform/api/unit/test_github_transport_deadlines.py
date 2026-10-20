"""Exercise GitHub deadlines and quota recovery over real TCP connections."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from tests.platform.api.github_http_peer import http_peer, response
from tests.platform.api.unit.test_github_client import key as key
from tests.platform.api.unit.test_github_client import pem
from tests.platform.api.unit.test_github_oidc import oidc as oidc_fixture
from throng_api import github_client, github_oidc
from throng_api.github_client import GitHubClient, GitHubError, GitHubToken

pytestmark = pytest.mark.anyio


def bounded_time(monkeypatch, module, budget, seconds=0.05):
    """Speed up only the production deadline, retaining a real asyncio timer and sockets."""
    timeout = asyncio.timeout

    def scaled(value):
        assert value == budget
        return timeout(seconds)

    def scaled_at(deadline):
        remaining = deadline - asyncio.get_running_loop().time()
        assert 0 <= remaining <= budget
        return timeout(seconds * remaining / budget)

    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            timeout=scaled, timeout_at=scaled_at, get_running_loop=asyncio.get_running_loop
        ),
        raising=False,
    )


@pytest.mark.parametrize("phase", ["headers", "body"])
async def test_total_deadline_bounds_a_regularly_active_connection(key, monkeypatch, phase):
    """Regular bytes cannot prolong one request forever; its next request still succeeds.

    HTTPX's read timeout resets per chunk. A separate total deadline must close
    even an active response, redact the failure, and release the operation's client.
    """
    bounded_time(monkeypatch, github_client, 10)
    calls = 0

    async def handler(request, writer):
        nonlocal calls
        calls += 1
        if calls > 1:
            response(writer, {"recovered": True})
        elif phase == "headers":
            await asyncio.sleep(0.2)
            response(writer, {})
        else:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 42\r\n\r\n{")
            for _ in range(40):
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(0.005)
            writer.write(b"}")

    client = GitHubClient("fixture", "secret", pem(key))
    async with http_peer(handler) as peer:
        with pytest.raises(GitHubError) as failure:
            await client._json("GET", peer.url, token="private-token")
        assert failure.value.status == 502 and failure.value.retry_after is None
        assert "private-token" not in str(failure.value)
        assert await client._json("GET", peer.url, token="private-token") == {"recovered": True}
        assert len(peer.requests) == 2


@pytest.mark.parametrize(
    "status,headers",
    [
        (429, {"Retry-After": "120"}),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2120"}),
    ],
)
async def test_truncated_limit_response_preserves_its_reset(key, status, headers):
    """Headers establish a quota deadline even when its response body is lost in transit.

    One identity stays blocked until reset, another remains usable, and the first
    can recover immediately at reset without retaining a reusable token.
    """
    clock = SimpleNamespace(now=2000)

    async def handler(request, writer):
        if len(peer.requests) == 1:
            response(
                writer,
                {"private": "truncated"},
                status=status,
                headers={**headers, "Content-Length": "999"},
            )
        else:
            response(writer, {"ok": True})

    client = GitHubClient("fixture", "secret", pem(key), clock=lambda: clock.now)
    identity = GitHubToken("first-token", ("user", 1))
    async with http_peer(handler) as peer:
        for token in (identity, GitHubToken("rotated-token", ("user", 1))):
            with pytest.raises(GitHubError) as failure:
                await client._json("GET", peer.url, token=token)
            assert failure.value.status == status and failure.value.retry_after == 120
        assert len(peer.requests) == 1
        assert await client._json("GET", peer.url, token="another-user") == {"ok": True}
        clock.now = 2120
        assert await client._json("GET", peer.url, token=identity) == {"ok": True}
        assert len(peer.requests) == 3


@pytest.mark.parametrize("phase", ["jwks", "lock"])
@pytest.mark.parametrize("lifetime", [1, 2])
async def test_identity_must_still_be_current_after_key_wait(key, monkeypatch, phase, lifetime):
    """A token expiring during JWKS IO or another verifier's lock must be rejected."""
    peer = oidc_fixture.__wrapped__(key)
    clock = SimpleNamespace(now=0)
    peer.verifier.monotonic = lambda: clock.now
    payload = json.loads(
        peer.verifier.client._transport.handler(httpx.Request("GET", github_oidc.JWKS_URL)).content
    )

    async def handler(request, writer):
        clock.now += 2
        response(writer, payload)

    peer.verifier.client._transport = None
    async with http_peer(handler) as server:
        monkeypatch.setattr(github_oidc, "JWKS_URL", server.url)
        if phase == "lock":
            await peer.verifier.verify(peer.token(), peer.now)
            clock.now = 0
            await peer.verifier.lock.acquire()
        token = peer.token({"exp": int(peer.now.timestamp()) + lifetime})
        pending = asyncio.create_task(peer.verifier.verify(token, peer.now))
        if phase == "lock":
            await asyncio.sleep(0)
            assert not pending.done()
            clock.now = 2
            peer.verifier.lock.release()
        with pytest.raises(HTTPException) as failure:
            await pending
        assert failure.value.status_code == 401
        assert await peer.verifier.verify(peer.token(), peer.now)
