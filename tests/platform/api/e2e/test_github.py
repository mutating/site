"""Real account/session HTTP flows with a deterministic external GitHub peer."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from throng_api.github import GitHubService, configure
from throng_api.models import AccessToken, GitHubAccount, GitHubOAuthFlow, TokenOwnership
from throng_api.security import digest

pytestmark = pytest.mark.anyio


@pytest.fixture
def github(app, settings, tmp_path, clock):
    """Keep platform/database/session behavior real and substitute only GitHub's HTTP peer."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "test-app.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    configured = settings.model_copy(
        update={
            "github_app_id": 123,
            "github_app_slug": "test-app",
            "github_client_id": "client-id",
            "github_client_secret": "client-secret",
            "github_private_key_path": path,
            "github_webhook_secret": "w" * 64,
            "github_token_key": Fernet.generate_key().decode(),
        }
    )
    integration = configure(configured)
    integration.client._clock = lambda: clock.now.timestamp()
    peer = SimpleNamespace(
        calls=[],
        failures={},
        user_id=789,
        identity=None,
        token_response={
            "access_token": "user-token",
            "refresh_token": "refresh-token",
            "expires_in": 28800,
        },
        installation={
            "id": 456,
            "app_id": 123,
            "account": {"login": "test-org"},
            "repository_selection": "selected",
            "suspended_at": None,
        },
        refresh_started=asyncio.Event(),
        refresh_release=None,
        callback=None,
        repository_response=None,
    )

    async def handler(request):
        peer.calls.append(request)
        failure = peer.failures.get((request.method, request.url.path))
        if failure:
            return httpx.Response(failure, json={"message": "provider-private-response"})
        if request.url.path == "/login/oauth/access_token":
            if peer.callback:
                await peer.callback()
            if parse_qs(request.content.decode()).get("grant_type") == ["refresh_token"]:
                peer.refresh_started.set()
                if peer.refresh_release:
                    await peer.refresh_release.wait()
            return httpx.Response(200, json=peer.token_response)
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json=peer.identity
                if peer.identity is not None
                else {"id": peer.user_id, "login": "github-user"},
            )
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "installations": [peer.installation, {**peer.installation, "app_id": 999}],
                },
            )
        if request.url.path == "/app/installations/456":
            return httpx.Response(200, json=peer.installation)
        if request.url.path == "/user/installations/456/repositories":
            if peer.repository_response is not None:
                return httpx.Response(200, json=peer.repository_response)
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "repositories": [
                        {
                            "id": 10,
                            "full_name": "test-org/sandbox",
                            "private": True,
                            "default_branch": "main",
                            "permissions": {"admin": True},
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected GitHub operation: {request.method} {request.url.path}")

    integration.client._transport = httpx.MockTransport(handler)
    app.state.github = integration
    return SimpleNamespace(service=integration, peer=peer, settings=configured)


async def begin(api, path="/v1/github/authorize"):
    """Start a real session-bound redirect and return its unguessable state."""
    response = await api.post(path)
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["url"]).query)["state"][0]


async def connect(api):
    """Complete the browser authorization callback through the live HTTP server."""
    state = await begin(api)
    response = await api.get(
        "/v1/github/oauth/callback", params={"state": state, "code": "valid-code"}
    )
    assert response.status_code == 303, response.text
    return state


async def test_disabled_github_status_is_available_but_connect_requires_configuration(api):
    """Unconfigured installations expose an accurate status and fail closed on authorization."""
    assert (await api.get("/v1/github/status")).json() == {"enabled": False, "account": None}
    response = await api.post("/v1/github/authorize")
    assert response.status_code == 503


async def test_connection_encrypts_tokens_and_exposes_only_identity(api, app, user, github):
    """Link by the verified GitHub user ID and keep all credentials out of public responses."""
    assert (await api.get("/v1/github/status")).json() == {"enabled": True, "account": None}
    state = await connect(api)
    response = await api.get("/v1/github/status")
    assert response.json() == {"enabled": True, "account": {"id": 789, "login": "github-user"}}
    async with app.state.db.read() as session:
        account = await session.get(GitHubAccount, user["id"])
        assert "user-token" not in account.tokens and "refresh-token" not in account.tokens
        assert github.service.unseal(account.tokens)["access_token"] == "user-token"
        assert await session.get(GitHubOAuthFlow, digest(state)) is None
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "replay"})
    assert response.status_code == 400


async def test_starting_a_new_flow_replaces_abandoned_state_and_disconnect_is_idempotent(
    api, app, user, github
):
    """Bound abandoned state and remove encrypted credentials plus outstanding redirects."""
    old = await begin(api)
    await connect(api)
    async with app.state.db.read() as session:
        assert await session.get(GitHubOAuthFlow, digest(old)) is None
    await begin(api)
    for _ in range(2):
        response = await api.delete("/v1/github/connection")
        assert response.status_code == 204, response.text
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None
        assert await session.scalar(select(GitHubOAuthFlow)) is None


@pytest.mark.parametrize(
    "mode", ["other-user", "other-session", "expired", "unknown", "wrong-purpose"]
)
async def test_oauth_state_cannot_be_used_outside_the_initiating_flow(
    api, other_api, client, user, clock, github, mode
):
    """Reject state theft, session substitution, expiration and cross-purpose callbacks."""
    if mode == "wrong-purpose":
        await connect(api)
        state = await begin(api, "/v1/github/installations/authorize")
    else:
        state = await begin(api)
    caller = api
    if mode == "other-user":
        caller = other_api
    elif mode == "other-session":
        response = await client.post(
            "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        caller = client
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    elif mode == "expired":
        clock.now += timedelta(minutes=11)
    elif mode == "unknown":
        state = "not-a-state"
    response = await caller.get(
        "/v1/github/oauth/callback", params={"state": state, "code": "code"}
    )
    assert response.status_code == 400, response.text


async def test_anonymous_callback_cannot_consume_a_flow(api, client, github):
    """Knowing state alone does not authorize linking another platform account."""
    state = await begin(api)
    response = await client.get(
        "/v1/github/oauth/callback", params={"state": state, "code": "code"}
    )
    assert response.status_code == 401
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 303


async def test_existing_github_connection_can_be_reauthorized(api, github):
    """Rotate tokens while retaining a stable verified identity."""
    await connect(api)
    github.peer.token_response["access_token"] = "replacement-token"
    await connect(api)
    assert (await api.get("/v1/github/status")).json()["account"]["id"] == 789


async def test_github_identity_cannot_be_claimed_by_two_platform_users(api, other_api, github):
    """Keep the account association unique even when both users can authorize GitHub."""
    await connect(api)
    state = await begin(other_api)
    response = await other_api.get(
        "/v1/github/oauth/callback", params={"state": state, "code": "code"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"id": "789", "login": "user"},
        {"id": 0, "login": "user"},
        {"id": 1, "login": 2},
        {"id": 1, "login": ""},
        {"id": 1, "login": "x" * 101},
        [],
    ],
)
async def test_invalid_github_identity_is_not_saved(api, github, identity):
    """Treat invalid upstream identity documents as provider errors."""
    github.peer.identity = identity
    state = await begin(api)
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 502
    assert (await api.get("/v1/github/status")).json()["account"] is None


@pytest.mark.parametrize("expires", [0, -1, "3600", True, 31536001])
async def test_invalid_token_expiration_is_not_saved(api, github, expires):
    """Do not persist credentials with malformed provider expiration metadata."""
    github.peer.token_response["expires_in"] = expires
    state = await begin(api)
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 502


@pytest.mark.parametrize(
    "status,expected", [(401, 409), (403, 403), (404, 404), (429, 503), (500, 502)]
)
async def test_oauth_provider_errors_are_sanitized(api, github, status, expected):
    """Provider errors must not leak bodies or log the user out of the platform."""
    github.peer.failures[("POST", "/login/oauth/access_token")] = status
    state = await begin(api)
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == expected
    assert "provider-private-response" not in response.text


async def test_revocation_during_exchange_prevents_connecting(api, app, github):
    """Recheck the initiating session after the external token exchange."""
    state = await begin(api)

    async def revoke():
        async with app.state.db.write() as session:
            token = await session.scalar(
                select(AccessToken).where(
                    AccessToken.token_hash == digest(api.headers["authorization"].split(" ")[1])
                )
            )
            token.revoked_at = app.state.clock()

    github.peer.callback = revoke
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 401


async def test_session_owner_change_during_exchange_cannot_link_the_new_owner(
    api, app, other_user, github
):
    """Do not attach an in-flight authorization to a different session owner."""
    state = await begin(api)

    async def transfer():
        async with app.state.db.write() as session:
            token = await session.scalar(
                select(AccessToken).where(
                    AccessToken.token_hash == digest(api.headers["authorization"].split(" ")[1])
                )
            )
            owner = await session.get(TokenOwnership, token.ownership_id)
            owner.user_id = other_user["id"]

    github.peer.callback = transfer
    response = await api.get("/v1/github/oauth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 400


async def test_manual_installation_redirect_does_not_trust_the_installation_id(client, api, github):
    """Initial manual setup may redirect to the dashboard but never associates accounts."""
    response = await client.get(
        "/v1/github/installations/callback",
        params={"installation_id": 456, "setup_action": "install"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://platform.mutating.tech/actions?github=installed"
    assert (await api.get("/v1/github/status")).json()["account"] is None
    assert not github.peer.calls


async def test_installation_flow_requires_a_connection_and_verifies_user_access(api, github):
    """Check the user's installation access after consuming session-bound state."""
    assert (await api.post("/v1/github/installations/authorize")).status_code == 409
    await connect(api)
    state = await begin(api, "/v1/github/installations/authorize")
    response = await api.get(
        "/v1/github/installations/callback", params={"state": state, "installation_id": 456}
    )
    assert response.status_code == 303
    assert github.peer.calls[-1].url.path == "/user/installations/456/repositories"


async def test_installation_callback_rejects_an_inaccessible_installation(api, github):
    """Installation state does not replace GitHub's current user-access check."""
    await connect(api)
    state = await begin(api, "/v1/github/installations/authorize")
    github.peer.failures[("GET", "/user/installations/456/repositories")] = 404
    response = await api.get(
        "/v1/github/installations/callback", params={"state": state, "installation_id": 456}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("result", [{}, []])
async def test_installation_callback_rejects_invalid_provider_documents(api, github, result):
    """Reject successful HTTP responses without the expected repository-access document."""
    await connect(api)
    state = await begin(api, "/v1/github/installations/authorize")
    github.peer.repository_response = result
    response = await api.get(
        "/v1/github/installations/callback", params={"state": state, "installation_id": 456}
    )
    assert response.status_code == 502


async def test_deleting_a_user_removes_github_credentials_and_abandoned_flows(
    api, app, user, github
):
    """Account deletion must not retain tokens or fail on connection foreign keys."""
    await connect(api)
    await begin(api)
    response = await api.delete(f"/v1/users/{user['id']}")
    assert response.status_code == 204, response.text
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None
        assert await session.scalar(select(GitHubOAuthFlow)) is None


async def test_discovery_lists_only_this_apps_installations_and_selected_repositories(api, github):
    """Keep installation and repository responses limited to useful public metadata."""
    await connect(api)
    response = await api.get("/v1/github/installations", params={"page": 2})
    assert response.status_code == 200, response.text
    assert response.json()["installations"] == [
        {"id": 456, "login": "test-org", "repository_selection": "selected", "suspended": False}
    ]
    assert github.peer.calls[-1].url.params["page"] == "2"
    response = await api.get("/v1/github/installations/456/repositories")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "total_count": 1,
        "repositories": [
            {
                "id": 10,
                "full_name": "test-org/sandbox",
                "private": True,
                "default_branch": "main",
                "can_administer": True,
            }
        ],
    }


@pytest.mark.parametrize("kind", ["suspended", "different-app"])
async def test_repository_discovery_rejects_unavailable_installations(api, github, kind):
    """A suspended installation or one belonging to another App is unavailable."""
    await connect(api)
    github.peer.installation["suspended_at" if kind == "suspended" else "app_id"] = (
        "suspended" if kind == "suspended" else 999
    )
    assert (await api.get("/v1/github/installations/456/repositories")).status_code == 404


@pytest.mark.parametrize(
    "path", ["/v1/github/installations", "/v1/github/installations/456/repositories"]
)
async def test_discovery_needs_a_connected_account_and_handles_provider_failures(api, github, path):
    """Reject missing connections and keep upstream failures separate from platform login."""
    assert (await api.get(path)).status_code == 409
    await connect(api)
    github.peer.failures[
        (
            "GET",
            "/user/installations" if path.endswith("installations") else "/app/installations/456",
        )
    ] = 500
    assert (await api.get(path)).status_code == 502


async def test_expiring_user_token_is_refreshed_and_saved_encrypted(api, app, user, clock, github):
    """Rotate expiring tokens once and persist the replacement rather than stale credentials."""
    await connect(api)
    clock.now += timedelta(hours=8)
    github.peer.token_response = {
        "access_token": "new-token",
        "refresh_token": "new-refresh",
        "expires_in": 28800,
    }
    response = await api.get("/v1/github/installations")
    assert response.status_code == 200, response.text
    assert github.peer.refresh_started.is_set()
    async with app.state.db.read() as session:
        account = await session.get(GitHubAccount, user["id"])
        assert github.service.unseal(account.tokens)["access_token"] == "new-token"
        assert account.refresh_nonce is None and account.refresh_until is None


async def test_refresh_claim_prevents_parallel_use_of_a_rotating_refresh_token(api, github, clock):
    """A second API process/request cannot simultaneously consume the same refresh grant."""
    await connect(api)
    clock.now += timedelta(hours=8)
    github.peer.refresh_release = asyncio.Event()
    first = asyncio.create_task(api.get("/v1/github/installations"))
    try:
        await asyncio.wait_for(github.peer.refresh_started.wait(), 5)
        second = await api.get("/v1/github/installations")
        assert second.status_code == 409
    finally:
        github.peer.refresh_release.set()
    assert (await first).status_code == 200


async def test_disconnecting_during_refresh_cannot_resurrect_the_connection(
    api, app, user, clock, github
):
    """Discard an external refresh result if the connection was removed during HTTP IO."""
    await connect(api)
    clock.now += timedelta(hours=8)
    github.peer.refresh_release = asyncio.Event()
    first = asyncio.create_task(api.get("/v1/github/installations"))
    try:
        await asyncio.wait_for(github.peer.refresh_started.wait(), 5)
        assert (await api.delete("/v1/github/connection")).status_code == 204
    finally:
        github.peer.refresh_release.set()
    assert (await first).status_code == 409
    async with app.state.db.read() as session:
        assert await session.get(GitHubAccount, user["id"]) is None


async def test_failed_refresh_releases_its_durable_claim(api, app, user, clock, github):
    """A failed external request must not leave the account permanently locked."""
    await connect(api)
    clock.now += timedelta(hours=8)
    github.peer.failures[("POST", "/login/oauth/access_token")] = 500
    assert (await api.get("/v1/github/installations")).status_code == 502
    async with app.state.db.read() as session:
        account = await session.get(GitHubAccount, user["id"])
        assert account.refresh_nonce is None and account.refresh_until is None


@pytest.mark.parametrize("mode", ["no-expiry", "no-refresh", "corrupt"])
async def test_nonexpiring_unrefreshable_and_unreadable_credentials(
    api, app, user, clock, github, mode
):
    """Handle static tokens and require reconnecting when a token cannot be refreshed or decrypted."""
    if mode == "no-expiry":
        github.peer.token_response.pop("expires_in")
    elif mode == "no-refresh":
        github.peer.token_response.pop("refresh_token")
    await connect(api)
    clock.now += timedelta(hours=8)
    if mode == "corrupt":
        async with app.state.db.write() as session:
            account = await session.get(GitHubAccount, user["id"])
            account.tokens = "not-an-encrypted-token"
    response = await api.get("/v1/github/installations")
    assert response.status_code == (200 if mode == "no-expiry" else 409)


async def test_configuration_requires_a_complete_persistent_credential_set(settings):
    """Partial settings must not silently activate an unusable GitHub integration."""
    assert configure(settings) is None
    with pytest.raises(ValueError, match="Configure all GitHub App credentials"):
        configure(settings.model_copy(update={"github_app_id": 123}))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "https://example.test/",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
    ],
)
def test_valid_public_origins_and_local_development(url):
    """Keep OAuth redirects on an explicit HTTPS origin, with loopback development support."""
    assert GitHubService.origin(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "example.test",
        "http://example.test",
        "https://user:pass@example.test",
        "https://user@example.test",
        "https://example.test/path",
        "https://example.test?q=1",
        "https://example.test#fragment",
    ],
)
def test_invalid_oauth_redirect_configuration_is_rejected(url):
    """Reject credentials, paths and unencrypted public origins in redirect configuration."""
    with pytest.raises(ValueError, match="GitHub public URLs"):
        GitHubService.origin(url)
