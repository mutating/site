"""GitHub refresh invalidates old credentials even while their requests are in flight."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from throng_api import ci_controller
from throng_api.models import CIReservation, CIRoute, GitHubAccount

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner
from .test_ci import route as route
from .test_ci_github_authorization_edges import revoke

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
async def rotating_check(request, api, client, app, user, clock, github, ci_peer, route, powercell):
    """Hold a real controller user request while another HTTP request can rotate its token."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    status = getattr(request, "param", "running")
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.status = status
        row.expires_at = clock.now + timedelta(hours=1)
        row.next_check = clock.now
        account = await session.get(GitHubAccount, user["id"])
        account.expires_at = clock.now + timedelta(seconds=61)
    original = github.service.client._transport
    peer = SimpleNamespace(
        id=result["id"],
        status=status,
        started=asyncio.Event(),
        release=asyncio.Event(),
        reads=[],
        invalidated=False,
        revoked=False,
        reject_new=False,
        new_admin=True,
    )

    async def handler(request):
        path, auth = request.url.path, request.headers.get("authorization")
        if path == "/login/oauth/access_token":
            response = await original.handler(request)
            # GitHub revokes the old access token when the refresh grant is used.
            if response.status_code == 200:
                peer.invalidated = True
            return response
        if path == "/repositories/42" and auth in {
            "Bearer user-token",
            "Bearer rotated-user-token",
        }:
            peer.reads.append(auth)
            assert len(peer.reads) <= 2, (
                "A failed replacement token must not cause unbounded retries"
            )
            if auth == "Bearer user-token":
                peer.started.set()
                await peer.release.wait()
                if peer.invalidated or peer.revoked:
                    return httpx.Response(401, json={"message": "Bad credentials"})
            elif peer.reject_new:
                return httpx.Response(401, json={"message": "Bad credentials"})
            response = await original.handler(request)
            if auth == "Bearer rotated-user-token" and not peer.new_admin:
                data = response.json()
                data["permissions"] = {"admin": False}
                return httpx.Response(200, json=data)
            return response
        return await original.handler(request)

    github.service.client._transport = httpx.MockTransport(handler)
    peer.pending = asyncio.create_task(ci_controller.run_once(app))
    try:
        await asyncio.wait_for(peer.started.wait(), 5)
        yield peer
    finally:
        peer.release.set()
        await peer.pending


async def rotate(api, github, clock):
    """Refresh via a second platform API request, crossing the refresh threshold naturally."""
    clock.now += timedelta(seconds=2)
    github.peer.token_response = {
        "access_token": "rotated-user-token",
        "refresh_token": "rotated-refresh-token",
        "expires_in": 28800,
    }
    response = await api.get("/v1/github/installations")
    assert response.status_code == 200, response.text
    assert github.peer.refresh_started.is_set()


# Assertions: a genuine concurrent refresh invalidates the first request's token;
# a single retry uses the persisted replacement, preserves admission/active work,
# leaves the account connected and never creates or deletes another runner.
@pytest.mark.parametrize("rotating_check", ["ready", "selected", "running"], indirect=True)
async def test_concurrent_user_refresh_does_not_revoke_valid_ci(
    rotating_check, api, app, user, clock, github, ci_peer
):
    """Keep valid CI work alive when another request refreshes its access token."""
    peer = rotating_check
    await rotate(api, github, clock)
    assert peer.invalidated
    peer.release.set()
    await peer.pending
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, peer.id)
        assert row.status == peer.status, (row.status, row.reason)
        assert row.finished_at is None
        account = await session.get(GitHubAccount, user["id"])
        assert github.service.unseal(account.tokens)["access_token"] == "rotated-user-token"
    assert peer.reads == ["Bearer user-token", "Bearer rotated-user-token"]
    assert ci_peer.jit_count == 1 and ci_peer.deletes == 0


# Assertions: without a replacement grant, GitHub 401 remains a real authority
# denial; unselected work fails, selected/running work cancels and the same
# rejected credential is never retried.
@pytest.mark.parametrize("rotating_check", ["ready", "selected", "running"], indirect=True)
async def test_unchanged_user_token_401_still_revokes_ci(rotating_check, app):
    """Retain fail-closed behavior when GitHub actually denies the current token."""
    peer = rotating_check
    peer.revoked = True
    peer.release.set()
    await peer.pending
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, peer.id)
        assert row.status == ("failed" if peer.status == "ready" else "cancelled")
        assert row.reason == "authorization_revoked" and row.credentials is None
    assert peer.reads == ["Bearer user-token"]


# Assertions: a local disconnect or signed GitHub revocation wins over the held
# user request; no retry recreates authority, and local reconciliation removes
# active work even if no replacement credential exists.
@pytest.mark.parametrize("source", ["disconnect", "webhook"])
async def test_removal_during_user_request_cannot_restore_ci_authority(
    rotating_check, api, client, app, user, github, route, source
):
    """Keep disconnect and provider revocation authoritative during retry decisions."""
    peer = rotating_check
    peer.revoked = True
    response = (
        await api.delete("/v1/github/connection")
        if source == "disconnect"
        else await revoke(client, github)
    )
    assert response.status_code in {202, 204}
    peer.release.set()
    await peer.pending
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None
        assert not (await session.get(CIRoute, route["id"])).enabled
        row = await session.get(CIReservation, peer.id)
        assert row.status == "cancelled" and row.reason == "authorization_revoked"
        assert row.credentials is None
    assert peer.reads == ["Bearer user-token"]


# Assertions: a replacement can also be rejected by GitHub; only one new-token
# retry is allowed and its 401 must cancel active work rather than looping or
# accepting stale authority.
async def test_rejected_replacement_user_token_is_not_retried_again(
    rotating_check, api, app, clock, github
):
    """Bound recovery to one persisted replacement and preserve a final denial."""
    peer = rotating_check
    await rotate(api, github, clock)
    peer.reject_new = True
    peer.release.set()
    await peer.pending
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, peer.id)
        assert row.status == "cancelled" and row.reason == "authorization_revoked"
    assert peer.reads == ["Bearer user-token", "Bearer rotated-user-token"]


# Assertions: retrying with a fresh token rechecks repository administration;
# token rotation cannot retain access after the fresh response loses admin rights.
async def test_replacement_user_token_must_still_prove_repository_administration(
    rotating_check, api, app, clock, github
):
    """Revalidate actual repository permissions on the successful replacement response."""
    peer = rotating_check
    await rotate(api, github, clock)
    peer.new_admin = False
    peer.release.set()
    await peer.pending
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, peer.id)
        assert row.status == "cancelled" and row.reason == "authorization_revoked"
    assert peer.reads == ["Bearer user-token", "Bearer rotated-user-token"]


# Assertions: discovery GETs have the same token invalidation race as controller
# authority checks; a concurrent refresh permits one replacement-token retry and
# the original caller receives its actual installation/repository result.
@pytest.mark.parametrize("kind", ["installations", "repositories"])
async def test_discovery_recovers_a_concurrently_rotated_user_token(
    api, app, user, clock, github, ci_peer, kind
):
    """Avoid asking connected users to reconnect after an unrelated request refreshed."""
    paths = {
        "installations": ("/v1/github/installations", "/user/installations"),
        "repositories": (
            "/v1/github/installations/456/repositories",
            "/user/installations/456/repositories",
        ),
    }
    endpoint, provider = paths[kind]
    refresh_endpoint = paths["repositories" if kind == "installations" else "installations"][0]
    async with app.state.db.write() as session:
        (await session.get(GitHubAccount, user["id"])).expires_at = clock.now + timedelta(
            seconds=61
        )
    original = github.service.client._transport
    started, release = asyncio.Event(), asyncio.Event()
    reads = []
    refreshed = False

    async def handler(request):
        nonlocal refreshed
        if request.url.path == "/login/oauth/access_token":
            response = await original.handler(request)
            refreshed = response.status_code == 200
            return response
        if request.url.path == provider:
            auth = request.headers.get("authorization")
            reads.append(auth)
            if auth == "Bearer user-token":
                started.set()
                await release.wait()
                assert refreshed
                return httpx.Response(401, json={"message": "Bad credentials"})
        return await original.handler(request)

    github.service.client._transport = httpx.MockTransport(handler)
    pending = asyncio.create_task(api.get(endpoint))
    try:
        await asyncio.wait_for(started.wait(), 5)
        clock.now += timedelta(seconds=2)
        github.peer.token_response = {
            "access_token": "rotated-user-token",
            "refresh_token": "rotated-refresh-token",
            "expires_in": 28800,
        }
        response = await api.get(refresh_endpoint)
        assert response.status_code == 200, response.text
    finally:
        release.set()
    response = await pending
    assert response.status_code == 200, response.text
    assert response.json()[kind]
    assert reads == ["Bearer user-token", "Bearer rotated-user-token"]
