"""Malformed peer retry deadlines must not disable or overflow recovery backoff."""

import httpx
import pytest

from tests.platform.api.unit.test_github_client import key as key
from tests.platform.api.unit.test_github_rate_edges import peer_client
from throng_api.github_client import GitHubError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("deadline", ["nan", "inf", "-1", str(2**31)])
async def test_invalid_peer_retry_deadline_keeps_bounded_cooldown(key, deadline):
    """Non-finite, negative and overflowing values fall back to a bounded provider cooldown."""
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": deadline}, json={})

    client, clock = peer_client(key, reply)
    for _ in range(2):
        with pytest.raises(GitHubError):
            await client.api("GET", "/repositories/42", token="temporary-test-token")
    assert len(calls) == 1
    clock.now += 60
    with pytest.raises(GitHubError):
        await client.api("GET", "/repositories/42", token="temporary-test-token")
    assert len(calls) == 2
