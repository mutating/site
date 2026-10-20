"""Real account HTTP flows remain bounded when GitHub or the database stalls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.platform.api.e2e.test_github import connect
from tests.platform.api.e2e.test_github import github as github
from tests.platform.api.github_http_peer import http_peer, response
from tests.platform.api.unit.test_github_transport_deadlines import bounded_time
from throng_api import github as github_module
from throng_api import github_client
from throng_api.models import GitHubAccount

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.mark.parametrize(
    "status,headers", [(429, {"Retry-After": "120"}), (403, {"X-RateLimit-Remaining": "0"})]
)
async def test_discovery_honors_quota_headers_after_body_disconnect(
    api, app, clock, github, monkeypatch, status, headers
):
    """A truncated GitHub error preserves Retry-After and permits discovery after reset.

    All platform authentication, encryption, HTTP handling and database writes are
    real; only GitHub is represented by a deliberately faulty local HTTP service.
    """
    await connect(api)
    expected_wait = 120 if status == 429 else 60

    async def handler(request, writer):
        assert request.path == "/user/installations?per_page=100&page=1"
        if len(peer.requests) == 1:
            response(
                writer,
                {"private": "provider-secret"},
                status=status,
                headers={**headers, "Content-Length": "999"},
            )
        else:
            response(writer, {"total_count": 1, "installations": [github.peer.installation]})

    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        for _ in range(2):
            result = await api.get("/v1/github/installations")
            assert result.status_code == 503, result.text
            assert result.headers["retry-after"] == str(expected_wait)
            assert "provider-secret" not in result.text
        assert len(peer.requests) == 1
        clock.now += timedelta(seconds=expected_wait)
        result = await api.get("/v1/github/installations")
        assert result.status_code == 200 and result.json()["installations"][0]["id"] == 456
        assert len(peer.requests) == 2


async def test_slow_refresh_stops_before_its_claim_can_be_reused(
    api, app, user, clock, github, monkeypatch
):
    """An active trickle cannot keep consuming a rotating grant beyond its claim.

    The peer observes the real socket closing on timeout. A subsequent rejected
    grant requires reconnecting, rather than retaining a live overlapping refresh
    or a stale durable lock; a lost rotation response cannot be safely replayed.
    """
    await connect(api)
    clock.now += timedelta(hours=8)
    bounded_time(monkeypatch, github_client, 10)
    bounded_time(monkeypatch, github_module, 20, 0.5)
    stopped = asyncio.Event()

    async def handler(request, writer):
        assert request.path == "/login/oauth/access_token"
        if len(peer.requests) > 1:
            assert stopped.is_set(), "An earlier rotation is still live"
            response(writer, {"error": "bad_refresh_token"})
            return
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n{")
        try:
            for _ in range(40):
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(0.025)
        except ConnectionError:
            stopped.set()

    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_client, "TOKEN_URL", peer.url + "/login/oauth/access_token")
        async with asyncio.timeout(0.3):
            result = await api.get("/v1/github/installations")
        assert result.status_code == 502, result.text
        async with asyncio.timeout(1):
            await stopped.wait()
        async with app.state.db.read() as session:
            account = await session.get(GitHubAccount, user["id"])
            assert account.refresh_nonce is None and account.refresh_until is None
        assert (await api.get("/v1/github/installations")).status_code == 409
        assert len(peer.requests) == 2


async def test_refresh_claim_uses_time_after_database_lock(
    api, app, user, clock, github, monkeypatch
):
    """Waiting for a real write lock must not make token freshness or the claim stale."""
    await connect(api)
    async with app.state.db.write() as session:
        account = await session.get(GitHubAccount, user["id"])
        account.expires_at = clock.now + timedelta(seconds=61)
    waiting = asyncio.Event()
    original = app.state.db.write

    @asynccontextmanager
    async def observed_write():
        waiting.set()
        async with original() as session:
            yield session

    async with original():
        monkeypatch.setattr(app.state.db, "write", observed_write)
        pending = asyncio.create_task(api.get("/v1/github/installations"))
        await asyncio.wait_for(waiting.wait(), 1)
        clock.now += timedelta(seconds=2)
    result = await pending
    assert result.status_code == 200, result.text
    assert github.peer.refresh_started.is_set()


async def test_database_wait_cannot_outlive_a_live_refresh_claim(
    api, app, user, clock, github, monkeypatch
):
    """Bound refresh through persistence, so a completed response cannot resume after lease reuse.

    The real coordination row is locked after GitHub receives the grant. Timing
    out rolls back the pending write, leaves old credentials intact and releases
    the nonce once the database becomes available again.
    """
    await connect(api)
    clock.now += timedelta(hours=8)
    bounded_time(monkeypatch, github_client, 10, 0.1)
    bounded_time(monkeypatch, github_module, 20, 0.15)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request, writer):
        if request.path.startswith("/user/installations"):
            response(writer, {"total_count": 0, "installations": []})
            return
        started.set()
        await release.wait()
        response(
            writer,
            {"access_token": "new-private", "refresh_token": "next-private", "expires_in": 28800},
        )

    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_client, "TOKEN_URL", peer.url + "/login/oauth/access_token")
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        pending = asyncio.create_task(api.get("/v1/github/installations"))
        await asyncio.wait_for(started.wait(), 1)
        async with app.state.db.write():
            release.set()
            await asyncio.sleep(0.3)
        result = await pending
    assert result.status_code == 502, result.text
    async with app.state.db.read() as session:
        account = await session.get(GitHubAccount, user["id"])
        assert github.service.unseal(account.tokens)["access_token"] == "user-token"
        assert account.refresh_nonce is None and account.refresh_until is None


async def test_slow_claim_commit_spends_the_refresh_budget_before_network_io(
    api, app, user, clock, github, monkeypatch
):
    """An already delayed claim must not start a new full-length refresh after committing.

    Flush the claim into the real transaction, then advance both clocks while
    its commit is held. An exhausted budget must release the nonce without
    consuming a rotating grant, so the next request can recover normally.
    """
    await connect(api)
    clock.now += timedelta(hours=8)
    elapsed = SimpleNamespace(now=0)
    monkeypatch.setattr(
        github_module,
        "asyncio",
        SimpleNamespace(
            get_running_loop=lambda: SimpleNamespace(time=lambda: elapsed.now),
            timeout=asyncio.timeout,
            timeout_at=lambda deadline: asyncio.timeout(deadline - elapsed.now),
        ),
    )
    original = app.state.db.write
    held, release = asyncio.Event(), asyncio.Event()
    first = True

    @asynccontextmanager
    async def delayed_commit():
        nonlocal first
        async with original() as session:
            yield session
            if first:
                first = False
                await session.flush()
                held.set()
                await release.wait()

    async def handler(request, writer):
        if request.path == "/login/oauth/access_token":
            response(
                writer,
                {"access_token": "new-token", "refresh_token": "next-token", "expires_in": 28800},
            )
        else:
            response(writer, {"total_count": 0, "installations": []})

    github.service.client._transport = None
    async with http_peer(handler) as peer:
        monkeypatch.setattr(github_client, "TOKEN_URL", peer.url + "/login/oauth/access_token")
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        monkeypatch.setattr(app.state.db, "write", delayed_commit)
        pending = asyncio.create_task(api.get("/v1/github/installations"))
        await asyncio.wait_for(held.wait(), 1)
        clock.now += timedelta(seconds=21)
        elapsed.now += 21
        release.set()
        result = await pending
        assert result.status_code == 502, result.text
        assert not peer.requests
        async with app.state.db.read() as session:
            account = await session.get(GitHubAccount, user["id"])
            assert account.refresh_nonce is None and account.refresh_until is None
            assert github.service.unseal(account.tokens)["access_token"] == "user-token"
        assert (await api.get("/v1/github/installations")).status_code == 200
        assert len(peer.requests) == 2
