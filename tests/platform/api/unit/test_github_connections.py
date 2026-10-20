"""Scoped keep-alive must preserve request authentication and bounded ownership."""

import asyncio

import httpx
import pytest

from tests.platform.api.unit.test_github_client import client
from tests.platform.api.unit.test_github_client import key as key
from throng_api import github_client
from throng_api.github_client import MAX_RESPONSE, GitHubError

pytestmark = pytest.mark.anyio


# Assertions: real sequential HTTP requests use one TCP connection within an
# operation; leaving that operation closes it; subsequent operations start fresh.
async def test_scoped_requests_reuse_a_real_connection(key, monkeypatch):
    connections, disconnected, requests = [], [], []
    all_closed = asyncio.Event()

    async def peer(reader, writer):
        connections.append(writer)
        try:
            while head := await reader.readuntil(b"\r\n\r\n"):
                lines = head.decode().split("\r\n")
                headers = {}
                for line in lines[1:]:
                    if line:
                        name, value = line.split(": ", 1)
                        headers[name.lower()] = value
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                method = lines[0].split()[0]
                requests.append((method, headers["authorization"], body))
                writer.write(
                    b"HTTP/1.1 204 No Content\r\n\r\n"
                    if method == "DELETE"
                    else b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
                )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.append(writer)
            if len(disconnected) == 2:
                all_closed.set()

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    monkeypatch.setattr(
        github_client, "API_URL", f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    )
    service = client(key, None)
    service._transport = None
    try:
        async with service.connection():
            assert await service.api("GET", "/app", token="first") == {}
            assert await service.api("POST", "/app", token="second", payload={"value": 1}) == {}
            assert await service.api("DELETE", "/app", token="third") is None
            assert len(connections) == 1 and not disconnected
            assert requests == [
                ("GET", "Bearer first", b""),
                ("POST", "Bearer second", b'{"value":1}'),
                ("DELETE", "Bearer third", b""),
            ]
        async with service.connection():
            await service.api("GET", "/app")
        assert len(connections) == 2
        async with asyncio.timeout(2):
            await all_closed.wait()
    finally:
        server.close()
        await server.wait_closed()
        for writer in connections:
            writer.close()


# Assertions: nested calls reuse the owner; only the owner closes the client;
# success, provider errors and cancellation all release it and reset the scope.
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_scoped_client_lifetime_and_nesting(key, outcome):
    closed = []

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)

    service = client(key, None)
    service._transport = Transport(lambda request: httpx.Response(200, json={}))
    owner = None

    async def operation():
        nonlocal owner
        async with service.connection() as owner:
            async with service.connection() as nested:
                assert nested is owner
                await service.api("GET", "/app")
            assert not owner.is_closed and not closed
            if outcome == "error":
                raise GitHubError(503)
            if outcome == "cancel":
                raise asyncio.CancelledError()

    if outcome == "success":
        await operation()
    else:
        with pytest.raises(GitHubError if outcome == "error" else asyncio.CancelledError):
            await operation()
    assert owner.is_closed and closed == [True]
    async with service.connection() as replacement:
        assert replacement is not owner
        await service.api("GET", "/app")
    assert closed == [True, True]


# Assertions: concurrent operations and separate GitHub clients never share
# ownership; completing one operation cannot close another operation's connection.
async def test_parallel_operations_have_independent_clients(key):
    service = client(key, lambda request: httpx.Response(200, json={}))
    other = client(key, lambda request: httpx.Response(200, json={}))
    entered, release = asyncio.Event(), asyncio.Event()
    owners = []

    async def first():
        async with service.connection() as owner:
            owners.append(owner)
            entered.set()
            await release.wait()
            assert not owner.is_closed
            await service.api("GET", "/app")

    task = asyncio.create_task(first())
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        async with service.connection() as second, other.connection() as third:
            assert len({id(owners[0]), id(second), id(third)}) == 3
            await service.api("GET", "/app")
        release.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# Assertions: an inherited task context cannot reuse a connection already closed
# by its parent; the delayed request owns and releases a fresh client instead.
async def test_delayed_child_does_not_reuse_a_closed_parent_connection(key):
    service = client(key, lambda request: httpx.Response(200, json={}))
    release = asyncio.Event()

    async def child():
        await release.wait()
        async with service.connection() as own:
            assert own is not parent and not own.is_closed
            assert await service.api("GET", "/app") == {}
        assert own.is_closed

    async with service.connection() as parent:
        task = asyncio.create_task(child())
    try:
        release.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# Assertions: reuse never forwards cookies received by an earlier GitHub API or
# OAuth response; bearer credentials remain specific to each individual request.
async def test_connection_reuse_does_not_share_cookies_or_authentication(key):
    calls = []

    def handler(request):
        calls.append(request)
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            json={"access_token": "fresh"},
            headers={"set-cookie": "credential=private; Domain=.github.com; Path=/"},
        )

    service = client(key, handler)
    async with service.connection():
        await service.api("GET", "/app", token="first")
        await service.api("GET", "/app", token="second")
        await service.oauth(code="fixture-code")
        await service.api("GET", "/app", token="third")
    assert [r.headers.get("authorization") for r in calls] == [
        "Bearer first",
        "Bearer second",
        None,
        "Bearer third",
    ]


# Assertions: rate-limit buckets remain distinct, and scoped credentials are
# revoked even if the repository request fails.
async def test_reused_connection_preserves_rate_limits_and_token_revocation(key):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={"token": "installation"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.headers["authorization"] == "Bearer installation":
            return httpx.Response(503)
        if request.headers["authorization"] == "Bearer limited":
            return httpx.Response(429, headers={"retry-after": "20"})
        return httpx.Response(200, json={})

    service = client(key, handler)
    async with service.connection():
        with pytest.raises(GitHubError) as limited:
            await service.api("GET", "/app", token="limited")
        assert limited.value.retry_after == 20
        count = len(calls)
        with pytest.raises(GitHubError):
            await service.api("GET", "/app", token="limited")
        assert len(calls) == count
        assert await service.api("GET", "/app", token="other") == {}
        with pytest.raises(GitHubError) as failed:
            async with service.installation_token(123, [456], {"metadata": "read"}) as token:
                await service.api("GET", "/repositories/456", token=token)
        assert failed.value.status == 503
        assert calls[-1] == ("DELETE", "/installation/token")


# Assertions: a rejected oversized response closes its stream without poisoning
# the operation's pool; the next authenticated request can still complete.
async def test_oversized_response_does_not_retain_a_pooled_connection(key):
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * (MAX_RESPONSE + 1)
            pytest.fail("Reading must stop at the response limit")

        async def aclose(self):
            closed.append(True)

    service = client(
        key,
        lambda request: (
            httpx.Response(200, stream=Stream())
            if request.url.path == "/large"
            else httpx.Response(200, json={})
        ),
    )
    async with service.connection() as owner:
        with pytest.raises(GitHubError):
            await service.api("GET", "/large")
        assert closed == [True] and not owner.is_closed
        assert await service.api("GET", "/app") == {}


# Assertions: ordinary callers do not need a connection scope, and their clients
# are disposed after each request, including malformed/oversized/error responses.
@pytest.mark.parametrize("status,body", [(200, b"{}"), (200, b"bad-json"), (503, b"private")])
async def test_unscoped_requests_keep_their_original_lifetime(key, status, body):
    closed = []

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)

    service = client(key, None)
    service._transport = Transport(lambda request: httpx.Response(status, content=body))
    for count in [1, 2]:
        if status == 200 and body == b"{}":
            assert await service.api("GET", "/app") == {}
        else:
            with pytest.raises(GitHubError):
                await service.api("GET", "/app")
        assert len(closed) == count
