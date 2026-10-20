"""A committed CI claim cannot contact GitHub beyond its actual remaining lease."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from tests.platform.api.github_http_peer import http_peer, response
from throng_api import ci_controller, github_client
from throng_api.models import CIReservation

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
async def due_cleanup(app, clock, route):
    """Persist a due terminal reservation that requires ordinary GitHub reconciliation."""
    async with app.state.db.write() as session:
        row = CIReservation(
            route_id=route["id"],
            run_id=1234,
            run_attempt=1,
            instance="commit",
            label="powercell-claim-commit",
            status="completed",
            reason="runner_exited",
            deadline=clock.now + timedelta(seconds=300),
            expires_at=clock.now + timedelta(hours=1),
            next_check=clock.now,
        )
        session.add(row)
        await session.flush()
        return row.id


def stalled_claim_commit(app, monkeypatch, *, handoff=False):
    """Pause a real claim transaction's commit, optionally before returning its detached work."""
    original = app.state.db.write
    held, release, committed, resume = [asyncio.Event() for _ in range(4)]
    delayed = False

    @asynccontextmanager
    async def stalled_write():
        nonlocal delayed
        claimed = False
        async with original() as session:
            yield session
            if not delayed and any(
                isinstance(row, CIReservation) and row.lease_nonce for row in session.dirty
            ):
                delayed = claimed = True
                await session.flush()
                held.set()
                await release.wait()
        if claimed and handoff:
            committed.set()
            await resume.wait()

    monkeypatch.setattr(app.state.db, "write", stalled_write)
    return SimpleNamespace(held=held, release=release, committed=committed, resume=resume)


async def upstream_response(transport, request):
    """Preserve the GitHub fixture contract behind a real TCP/HTTP boundary."""
    return await transport.handler(
        httpx.Request(
            request.method,
            "https://api.github.com" + request.path,
            headers=request.headers,
            content=request.body,
        )
    )


@pytest.mark.parametrize("elapsed", [60, 61])
@pytest.mark.parametrize("replacement", [False, True])
async def test_expired_claim_commit_cannot_issue_http_or_release_a_successor(
    app, clock, github, ci_peer, due_cleanup, monkeypatch, elapsed, replacement
):
    """A claim expired at equality cannot perform remote work or clear a newer nonce.

    Real SQLite flush/commit barriers let another real claim replace the expired
    nonce before the old controller resumes. All provider requests use TCP, so
    their absence establishes that stale work had no remote side effects. An
    expired claim must not defer due reconciliation as a synthetic provider outage.
    """
    barrier = stalled_claim_commit(app, monkeypatch, handoff=replacement)
    original = github.service.client._transport

    async def provider(request, writer):
        upstream = await upstream_response(original, request)
        response(
            writer,
            None if upstream.status_code == 204 else upstream.json(),
            status=upstream.status_code,
        )

    github.service.client._transport = None
    async with http_peer(provider) as peer:
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        pending = asyncio.create_task(ci_controller.run_once(app))
        await asyncio.wait_for(barrier.held.wait(), 2)
        clock.now += timedelta(seconds=elapsed)
        barrier.release.set()
        successor = None
        if replacement:
            await asyncio.wait_for(barrier.committed.wait(), 2)
            successor, _ = await ci_controller.claim_work(app)
            assert successor.lease_until > clock.now
            barrier.resume.set()
        await asyncio.wait_for(pending, 5)
        assert not peer.requests, "An expired claim performed remote GitHub operations"
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, due_cleanup)
        assert row.lease_nonce == (successor.lease_nonce if successor else None)
        assert row.lease_until == (successor.lease_until if successor else None)
        if successor:
            assert row.next_check == successor.next_check
        else:
            assert row.next_check <= clock.now


@pytest.mark.parametrize("elapsed,budget,expected_calls", [(0, 40, 5), (59, 1, 1)])
async def test_reconciliation_uses_only_the_committed_claim_remaining_time(
    app, clock, github, ci_peer, due_cleanup, monkeypatch, elapsed, budget, expected_calls
):
    """Fresh work retains 40 seconds; a 59-second commit leaves only one for HTTP.

    Scale only the controller timer while keeping actual TCP reads active. The
    token response fits inside a fresh budget, but cannot finish inside the short
    remainder: removing the cap causes extra provider operations, not just a
    different timer argument.
    """
    barrier = stalled_claim_commit(app, monkeypatch)
    original = github.service.client._transport
    timeout = asyncio.timeout
    budgets, delivered = [], []

    def scaled(value):
        budgets.append(value)
        return timeout(value * 0.02)

    monkeypatch.setattr(ci_controller, "asyncio", SimpleNamespace(timeout=scaled))

    async def provider(request, writer):
        upstream = await upstream_response(original, request)
        if len(peer.requests) == 1:
            assert request.path == "/app/installations/456/access_tokens"
            body = upstream.content
            writer.write(
                f"HTTP/1.1 201 Created\r\nContent-Length: {len(body) + 40}\r\n"
                "Connection: close\r\n\r\n".encode()
            )
            for _ in range(40):
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(0.005)
            writer.write(body)
            delivered.append(True)
        else:
            response(
                writer,
                None if upstream.status_code == 204 else upstream.json(),
                status=upstream.status_code,
            )

    github.service.client._transport = None
    async with http_peer(provider) as peer:
        monkeypatch.setattr(github_client, "API_URL", peer.url)
        pending = asyncio.create_task(ci_controller.run_once(app))
        await asyncio.wait_for(barrier.held.wait(), 2)
        clock.now += timedelta(seconds=elapsed)
        barrier.release.set()
        await asyncio.wait_for(pending, 5)
        assert len(peer.requests) == expected_calls
        assert bool(delivered) == (elapsed == 0)
        assert budgets == [budget]
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, due_cleanup)
        assert row.lease_nonce is None and row.lease_until is None
