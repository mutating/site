"""Concurrent and repeated GitHub rate-limit behavior at the transport boundary."""

import asyncio

import httpx
import pytest

from tests.platform.api.unit.test_github_client import key as key
from tests.platform.api.unit.test_github_rate_edges import peer_client
from throng_api.github_client import GitHubError

pytestmark = pytest.mark.anyio


# Assertions: an older successful in-flight request cannot cancel a quota limit
# learned from a newer response; subsequent requests still respect that deadline.
async def test_inflight_success_preserves_a_newer_provider_cooldown(key):
    """Keep the provider's latest restriction when parallel requests finish out of order."""
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.path == "/slow":
            started.set()
            await release.wait()
        elif request.url.path == "/limited":
            return httpx.Response(429, headers={"Retry-After": "120"}, json={"message": "limited"})
        return httpx.Response(200, json={"ok": True})

    client, _ = peer_client(key, handler)
    pending = asyncio.create_task(client.api("GET", "/slow", token="same-user"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(GitHubError):
            await client.api("GET", "/limited", token="same-user")
    finally:
        release.set()
    assert await pending == {"ok": True}
    with pytest.raises(GitHubError):
        await client.api("GET", "/another", token="same-user")
    assert len(calls) == 2


# Assertions: consecutive secondary limits without headers wait at least a minute
# and then double the delay; a successful eligible retry restores normal requests.
async def test_repeated_secondary_limits_back_off_until_success(key):
    """Increase secondary-limit delays instead of repeatedly hammering GitHub."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429 if len(calls) <= 2 else 200, json={"message": "secondary rate limit"}
        )

    client, clock = peer_client(key, handler)
    for now in (2000, 2060):
        clock.now = now
        with pytest.raises(GitHubError):
            await client.api("GET", "/app", token="same-user")
    clock.now = 2179
    with pytest.raises(GitHubError):
        await client.api("GET", "/app", token="same-user")
    assert len(calls) == 2
    clock.now = 2180
    for _ in range(2):
        assert await client.api("GET", "/app", token="same-user") == {
            "message": "secondary rate limit"
        }
    assert len(calls) == 4


# Assertions: concurrent primary and secondary limits share one identity; neither
# response ordering nor 403/429 may shorten the latest reset deadline. HTTP must
# stay blocked after the shorter delay and resume only at the longest deadline.
@pytest.mark.parametrize("primary_status", [403, 429])
@pytest.mark.parametrize("secondary_status", [403, 429])
@pytest.mark.parametrize("short_last", [False, True])
async def test_concurrent_limits_keep_the_longest_deadline(
    key, primary_status, secondary_status, short_last
):
    """Honor both restrictions when primary and secondary responses arrive concurrently."""
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.path == "/slow":
            started.set()
            await release.wait()
        if request.url.path in {"/slow", "/fast"}:
            short = short_last == (request.url.path == "/slow")
            return httpx.Response(
                secondary_status if short else primary_status,
                headers={"Retry-After": "60"}
                if short
                else {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5600"},
                json={"message": "rate limit exceeded"},
            )
        return httpx.Response(200, json={"ok": True})

    client, clock = peer_client(key, handler)
    pending = asyncio.create_task(client.api("GET", "/slow", token="same-user"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(GitHubError):
            await client.api("GET", "/fast", token="same-user")
    finally:
        release.set()
    with pytest.raises(GitHubError) as limited:
        await pending
    assert limited.value.retry_after == 3600
    clock.now = 2061
    with pytest.raises(GitHubError) as early:
        await client.api("GET", "/after", token="same-user")
    assert early.value.retry_after == 3539
    assert len(calls) == 2
    clock.now = 5600
    assert await client.api("GET", "/after", token="same-user") == {"ok": True}
    assert len(calls) == 3
