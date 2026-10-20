"""Provider rate-limit behavior through GitHub's actual bounded HTTP client."""

from types import SimpleNamespace

import httpx
import pytest

from tests.platform.api.unit.test_github_client import key as key
from tests.platform.api.unit.test_github_client import pem
from throng_api.github_client import GitHubClient, GitHubError

pytestmark = pytest.mark.anyio


def peer_client(key, handler):
    """Use a deterministic provider clock without sleeping or real HTTP calls."""
    clock = SimpleNamespace(now=2000)
    client = GitHubClient(
        "fixture-client",
        "fixture-client-secret",
        pem(key),
        transport=httpx.MockTransport(handler),
        clock=lambda: clock.now,
    )
    return client, clock


# Assertions: 403/429 limits honor both Retry-After and exhausted-primary reset;
# missing/invalid secondary-limit headers still impose at least a minute. During
# that interval no same-user request reaches GitHub; another user remains usable.
# Errors expose neither response bodies nor tokens; the original request can
# resume at the provider deadline without changing its method/path/payload.
@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize(
    "headers,wait",
    [
        ({"Retry-After": "120"}, 120),
        ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2120"}, 120),
        (
            {
                "Retry-After": "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "2120",
            },
            120,
        ),
        ({}, 60),
        ({"Retry-After": "invalid", "X-RateLimit-Reset": "invalid"}, 60),
    ],
)
async def test_rate_limit_defers_only_its_user_authentication_bucket(key, status, headers, wait):
    """Obey GitHub's retry deadline without leaking credentials or blocking another user."""
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                status,
                headers=headers,
                json={
                    "message": "You have exceeded a secondary rate limit. private-provider-body",
                    "token": "private-response-token",
                },
            )
        return httpx.Response(200, json={"ok": True})

    client, clock = peer_client(key, handler)
    with pytest.raises(GitHubError) as failure:
        await client.api("GET", "/repositories/42", token="private-user-token")
    assert failure.value.status == status
    for secret in (
        "private-provider-body",
        "private-response-token",
        "private-user-token",
    ):
        assert secret not in str(failure.value) and secret not in repr(failure.value)
    for age in (1, wait - 1):
        clock.now = 2000 + age
        with pytest.raises(GitHubError):
            await client.api("GET", "/repositories/43", token="private-user-token")
        assert len(calls) == 1
    assert await client.api("GET", "/repositories/43", token="another-user-token") == {"ok": True}
    assert len(calls) == 2
    clock.now = 2000 + wait
    assert await client.api("GET", "/repositories/42", token="private-user-token") == {"ok": True}
    assert len(calls) == 3 and calls[-1].url.path == "/repositories/42"


# Assertions: ordinary forbidden responses are not mistaken for rate limits just
# because GitHub's regular quota headers are present, nor cached as a global
# outage; a corrected permission can be retried immediately.
async def test_permission_forbidden_does_not_create_a_rate_limit_cooldown(key):
    """Distinguish a permission denial from primary or secondary throttling."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            403 if len(calls) == 1 else 200,
            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "5600"},
            json={"message": "Resource not accessible by integration"},
        )

    client, _ = peer_client(key, handler)
    with pytest.raises(GitHubError) as failure:
        await client.api("GET", "/repositories/42", token="fixture-user-token")
    assert failure.value.status == 403
    assert await client.api("GET", "/repositories/42", token="fixture-user-token") == {
        "message": "Resource not accessible by integration"
    }
    assert len(calls) == 2


# Assertions: installation REST quotas belong to the installation, not each
# freshly minted short-lived token. A fresh token for the same installation may
# not bypass its cooldown, while a different installation remains usable; after
# reset the original installation works without retaining reusable token state.
async def test_new_installation_tokens_cannot_bypass_the_installation_rate_limit(key):
    """Share throttling across rotating scoped tokens for one installation."""
    minted, protected = [], []

    def handler(request):
        if request.url.path.endswith("/access_tokens"):
            installation = int(request.url.path.split("/")[3])
            token = f"fixture-installation-{installation}-{len(minted)}"
            minted.append(token)
            return httpx.Response(201, json={"token": token})
        if request.url.path == "/installation/token":
            return httpx.Response(204)
        protected.append(request)
        if len(protected) == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, json={"message": "limited"})
        return httpx.Response(200, json={"ok": True})

    client, clock = peer_client(key, handler)
    with pytest.raises(GitHubError):
        async with client.installation_token(456, [42], {"metadata": "read"}) as token:
            await client.api("GET", "/repositories/42", token=token)
    clock.now += 1
    with pytest.raises(GitHubError):
        async with client.installation_token(456, [42], {"metadata": "read"}) as token:
            await client.api("GET", "/repositories/42", token=token)
    assert len(protected) == 1
    async with client.installation_token(457, [43], {"metadata": "read"}) as token:
        assert await client.api("GET", "/repositories/43", token=token) == {"ok": True}
    assert len(protected) == 2
    clock.now = 2120
    async with client.installation_token(456, [42], {"metadata": "read"}) as token:
        assert await client.api("GET", "/repositories/42", token=token) == {"ok": True}
    assert len(protected) == 3
