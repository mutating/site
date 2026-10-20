"""GitHub-side authorization revocation and shared rate-limit regressions."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.platform.api.e2e.test_github import begin, connect
from throng_api import ci_controller
from throng_api.models import (
    CIReservation,
    CIRoute,
    GitHubAccount,
    GitHubOAuthFlow,
    Isolate,
)

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner, reserve
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


def user_calls(peer):
    """Count only calls made with the fixture user's provider access token."""
    return sum(
        request.headers.get("authorization") == "Bearer user-token" for request in peer.calls
    )


async def revoke(client, github, *, sender=789, delivery=None):
    """Deliver GitHub's mandatory account-level event without an installation."""
    return await deliver(
        client,
        github,
        {"action": "revoked", "sender": {"id": sender, "login": "fixture-user"}},
        event="github_app_authorization",
        delivery=delivery,
    )


# Assertions: a signed numeric GitHub user revocation removes only that user's
# tokens/flows, disables their routes and prevents further user API calls. Another
# account and its routes/flows remain valid; replay is idempotent.
async def test_revocation_disconnects_only_the_matching_github_identity(
    client, api, other_api, app, user, other_user, github, ci_peer, route
):
    """Treat provider authorization revocation like a scoped local disconnect."""
    github.peer.user_id = 790
    await connect(other_api)
    other_route = await other_api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "other",
        },
    )
    assert other_route.status_code == 201, other_route.text
    for caller in (api, other_api):
        await begin(caller)
        await begin(caller, "/v1/github/installations/authorize")
    before = user_calls(ci_peer)
    delivery = str(uuid4())
    for _ in range(2):
        response = await revoke(client, github, delivery=delivery)
        assert response.status_code == 202, response.text
    assert (await api.get("/v1/github/status")).json()["account"] is None
    assert (await other_api.get("/v1/github/status")).json()["account"]["id"] == 790
    assert (await api.get("/v1/github/installations")).status_code == 409
    result = await reserve(client, route, ci_peer)
    assert result.status_code == 200 and result.json()["reason"] == "route_disabled"
    assert user_calls(ci_peer) == before
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None
        assert await session.get(GitHubAccount, other_user["id"]) is not None
        assert not (await session.get(CIRoute, route["id"])).enabled
        assert (await session.get(CIRoute, other_route.json()["id"])).enabled
        flows = (await session.scalars(select(GitHubOAuthFlow))).all()
        assert len(flows) == 2 and all(row.user_id == other_user["id"] for row in flows)


# Assertions: malformed or unrelated sender IDs never revoke a connected user by
# string coercion, login collision or a missing identity; no provider call occurs.
@pytest.mark.parametrize("sender", ["789", 790, 0, -1, None, True])
async def test_revocation_requires_the_matching_numeric_sender(
    client, api, app, user, github, ci_peer, route, sender
):
    """Keep unrelated identities untouched even in correctly signed payloads."""
    before = user_calls(ci_peer)
    response = await revoke(client, github, sender=sender)
    assert response.status_code in {202, 400}
    assert (await api.get("/v1/github/status")).json()["account"]["id"] == 789
    assert user_calls(ci_peer) == before
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is not None
        assert (await session.get(CIRoute, route["id"])).enabled


# Assertions: revocation invalidates an already issued OAuth state before its
# callback can exchange a code or restore the removed connection.
async def test_revocation_invalidates_pending_oauth_state(api, client, github, ci_peer):
    """Discard pre-revocation authorization flows rather than reconnecting from them."""
    state = await begin(api)
    assert (await revoke(client, github)).status_code == 202
    before = len(github.peer.calls)
    response = await api.get(
        "/v1/github/oauth/callback", params={"state": state, "code": "pending-code"}
    )
    assert response.status_code == 400
    assert len(github.peer.calls) == before
    assert (await api.get("/v1/github/status")).json()["account"] is None


# Assertions: an OAuth exchange already outside the DB lock cannot persist its
# eventual token result after a signed revocation has removed its durable claim.
async def test_revocation_during_oauth_cannot_restore_the_connection(
    api, client, app, user, github, ci_peer
):
    """Let provider revocation win over an in-flight reauthorization exchange."""
    state = await begin(api)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked_exchange():
        started.set()
        await release.wait()

    github.peer.callback = blocked_exchange
    pending = asyncio.create_task(
        api.get("/v1/github/oauth/callback", params={"state": state, "code": "in-flight"})
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert (await revoke(client, github)).status_code == 202
    finally:
        release.set()
    assert (await pending).status_code == 409
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None
        assert await session.scalar(select(GitHubOAuthFlow)) is None


# Assertions: a rotating refresh response cannot recreate credentials removed by
# a provider revocation, and later discovery does not reuse the revoked token.
async def test_revocation_during_refresh_cannot_restore_the_connection(
    api, client, app, user, clock, github, ci_peer
):
    """Let revocation win while token refresh is waiting on GitHub."""
    clock.now += timedelta(hours=8)
    github.peer.refresh_release = asyncio.Event()
    pending = asyncio.create_task(api.get("/v1/github/installations"))
    try:
        await asyncio.wait_for(github.peer.refresh_started.wait(), 5)
        assert (await revoke(client, github)).status_code == 202
    finally:
        github.peer.refresh_release.set()
    assert (await pending).status_code == 409
    before = user_calls(ci_peer)
    assert (await api.get("/v1/github/installations")).status_code == 409
    assert user_calls(ci_peer) == before
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None


# Assertions: revoked authorization immediately blocks selection and causes local
# cleanup on the next controller tick even if the next provider poll is far away;
# selected/running jobs cancel, unselected ready jobs fail, without user IO.
@pytest.mark.parametrize("status", ["ready", "selected", "running"])
async def test_revocation_closes_active_reservations_without_user_api_calls(
    client, app, clock, github, ci_peer, route, powercell, status
):
    """Enforce the provider's revocation through existing local lifecycle guards."""
    result, _ = await ready_runner(
        client, route, ci_peer, powercell, app, clock, select_runner=False
    )
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        row.status = status
        row.next_check = clock.now + timedelta(hours=1)
    before = user_calls(ci_peer)
    assert (await revoke(client, github)).status_code == 202
    selection = await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={"instance": "default"},
    )
    assert selection.status_code == 409
    await ci_controller.run_once(app)
    assert user_calls(ci_peer) == before
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("failed" if status == "ready" else "cancelled")
        assert row.reason == "authorization_revoked" and row.credentials is None
        assert (await session.get(Isolate, row.isolate_id)).status in {
            "destroying",
            "destroyed",
        }


# Assertions: one account-level limit stops provider user requests from all its
# existing running reservations until the advertised reset. Local job deadlines
# still advance and expire during the provider cooldown.
@pytest.mark.parametrize("provider_status", [403, 429])
async def test_provider_user_rate_limit_is_shared_by_running_reservations(
    client, api, app, clock, github, ci_peer, route, provider_status
):
    """Do not retry a shared user budget separately for every active job."""
    ids = []
    async with app.state.db.write() as session:
        for index in range(2):
            row = CIReservation(
                route_id=route["id"],
                run_id=1234,
                run_attempt=1,
                instance=f"rate-{index}",
                label="powercell-" + str(uuid4()),
                status="running",
                deadline=clock.now + timedelta(seconds=300),
                expires_at=clock.now + timedelta(seconds=90 if index == 0 else 300),
                next_check=clock.now,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
    original = github.service.client._transport
    github.service.client._clock = lambda: clock.now.timestamp()
    limited = []
    reset = clock.now + timedelta(seconds=120)

    async def handler(request):
        if (
            request.url.path == "/repositories/42"
            and request.headers.get("authorization") == "Bearer user-token"
        ):
            limited.append(clock.now)
            return httpx.Response(
                provider_status,
                headers={
                    "Retry-After": "120",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(reset.timestamp())),
                },
                json={"message": "private-rate-limit-message"},
            )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    for advance in (0, 1, 30, 30):
        clock.now += timedelta(seconds=advance)
        await ci_controller.run_once(app)
    assert len(limited) == 1
    assert (await api.get("/v1/github/status")).json()["account"]["id"] == 789
    clock.now = reset - timedelta(seconds=1)
    await ci_controller.local_tick(app)
    async with app.state.db.read() as session:
        assert (await session.get(CIReservation, ids[0])).status == "timed_out"
        assert (await session.get(CIReservation, ids[1])).status == "running"
    assert len(limited) == 1


# Assertions: throttling is a temporary API failure, not a permission denial;
# refresh-token rotation cannot bypass the same GitHub user's exhausted budget,
# while another linked GitHub identity can still discover its installations.
@pytest.mark.parametrize("provider_status", [403, 429])
async def test_rotated_user_tokens_keep_their_provider_cooldown(
    api, other_api, app, user, clock, github, ci_peer, provider_status
):
    """Keep user quota identity stable across token rotation and redact API errors."""
    original = github.service.client._transport
    github.service.client._clock = lambda: clock.now.timestamp()
    discoveries = []

    async def handler(request):
        if request.url.path == "/user/installations":
            discoveries.append(request)
            if request.headers.get("authorization") == "Bearer user-token":
                return httpx.Response(
                    provider_status,
                    headers={"Retry-After": "120"},
                    json={"message": "You have exceeded a secondary rate limit. private-details"},
                )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    response = await api.get("/v1/github/installations")
    assert response.status_code == 503
    assert int(response.headers["retry-after"]) >= 120
    assert "private-details" not in response.text
    async with app.state.db.write() as session:
        (await session.get(GitHubAccount, user["id"])).expires_at = clock.now
    github.peer.token_response = {
        "access_token": "rotated-user-token",
        "refresh_token": "new-refresh",
        "expires_in": 28800,
    }
    assert (await api.get("/v1/github/installations")).status_code == 503
    assert len(discoveries) == 1
    github.peer.user_id = 790
    github.peer.token_response = {
        "access_token": "other-user-token",
        "refresh_token": "other-refresh",
        "expires_in": 28800,
    }
    await connect(other_api)
    assert (await other_api.get("/v1/github/installations")).status_code == 200
    assert len(discoveries) == 2
