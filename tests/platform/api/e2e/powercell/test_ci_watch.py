"""Signed queue-watch requests observe one CI reservation without changing platform state."""

from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from throng_api.models import AgentTask, CIReservation, CIRoute, Command, Coordination, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
async def watched(app, clock, route, github):
    """Persist an existing selection with deliberately private runner identity and credentials."""
    async with app.state.db.write() as session:
        row = CIReservation(
            route_id=route["id"],
            run_id=1234,
            run_attempt=1,
            instance="default",
            label="powercell-private-watch-label",
            status="selected",
            credentials=github.service.seal({"jit": "private-watch-jit"}),
            deadline=clock.now + timedelta(seconds=90),
            expires_at=clock.now + timedelta(seconds=300),
            next_check=clock.now,
        )
        session.add(row)
        await session.flush()
        return row.id


async def platform_state(app):
    """Snapshot complete lifecycle rows and the write-lock revision to catch hidden side effects."""
    async with app.state.db.read() as session:
        result = {}
        for model in (CIReservation, CIRoute, Isolate, Command, AgentTask, Coordination):
            rows = (await session.scalars(select(model))).all()
            result[model.__name__] = [
                {column.key: getattr(row, column.key) for column in model.__table__.columns}
                for row in rows
            ]
        return result


async def watch(client, route, peer, *, instance="default", claims=None, headers=None):
    """Use the real HTTP handler and RSA-signed GitHub identity for an exact instance."""
    return await client.post(
        f"/v1/github/ci/routes/{route['id']}/watch",
        headers=headers
        if headers is not None
        else {"Authorization": "Bearer " + peer.token(**(claims or {}))},
        json={"instance": instance},
    )


@pytest.mark.parametrize(
    "status,reason,expired,expected",
    [
        ("preparing", None, False, {"status": "waiting"}),
        ("ready", None, False, {"status": "waiting"}),
        ("selected", None, False, {"status": "waiting"}),
        ("running", None, False, {"status": "started"}),
        ("completed", "runner_exited", False, {"status": "waiting"}),
        ("completed", "github_success", True, {"status": "started"}),
        ("cancelled", "github_cancelled", True, {"status": "started"}),
        (
            "completed",
            "github_outcome_unavailable",
            False,
            {"status": "failed", "reason": "github_outcome_unavailable"},
        ),
        ("failed", "no_capacity", False, {"status": "failed", "reason": "no_capacity"}),
    ],
)
async def test_watch_observes_state_without_allocating_selecting_or_extending_leases(
    client, route, ci_peer, watched, github, app, clock, status, reason, expired, expected
):
    """A guard learns only execution state; completed fast jobs need no live VM or command.

    Every lifecycle column and the coordination revision must stay unchanged.
    Private runner labels, IDs and JIT credentials never enter the response.
    """
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, watched)
        row.status, row.reason = status, reason
        if expired:
            row.expires_at = clock.now - timedelta(seconds=1)
    before, calls = await platform_state(app), len(ci_peer.calls)
    for _ in range(2):
        response = await watch(client, route, ci_peer)
        assert response.status_code == 200, response.text
        assert response.json() == expected
    assert await platform_state(app) == before
    assert all(request.url.path == "/.well-known/jwks" for request in ci_peer.calls[calls:])


@pytest.mark.parametrize(
    "status,reason", [("selected", None), ("running", None), ("completed", "runner_exited")]
)
@pytest.mark.parametrize("remaining", [-1, 0, 1])
async def test_watch_observes_current_expiration_without_waiting_for_controller(
    client, route, ci_peer, watched, app, clock, status, reason, remaining
):
    """Equality is expired; provisional exit never becomes proof that the queued job started."""
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, watched)
        row.status, row.reason = status, reason
        row.expires_at = clock.now + timedelta(seconds=remaining)
    before = await platform_state(app)
    response = await watch(client, route, ci_peer)
    assert response.status_code == 200
    assert response.json() == (
        {"status": "failed", "reason": "runner_timeout"}
        if remaining <= 0
        else {"status": "started" if status == "running" else "waiting"}
    )
    assert await platform_state(app) == before


@pytest.mark.parametrize("domain", ["run", "attempt", "instance", "route", "missing"])
async def test_watch_cannot_borrow_another_reservation_or_create_a_missing_one(
    client, api, route, ci_peer, watched, app, domain
):
    """The route, run, attempt and instance jointly identify the only observable reservation."""
    claims, instance, selected_route = {}, "default", route
    if domain == "run":
        claims = {"run_id": "1235"}
    elif domain == "attempt":
        claims = {"run_attempt": "2"}
    elif domain == "instance":
        instance = "another-instance"
    elif domain == "route":
        response = await api.post(
            "/v1/github/routes",
            json={
                "installation_id": 456,
                "repository_id": 42,
                "workflow_path": ".github/workflows/ci.yml",
                "job_key": "other",
            },
        )
        assert response.status_code == 201
        selected_route = response.json()
    else:
        async with app.state.db.write() as session:
            await session.delete(await session.get(CIReservation, watched))
    before = await platform_state(app)
    response = await watch(client, selected_route, ci_peer, instance=instance, claims=claims)
    assert response.status_code == 200
    assert response.json() == {"status": "failed", "reason": "reservation_unavailable"}
    assert await platform_state(app) == before


@pytest.mark.parametrize("state", ["selected", "running", "completed"])
@pytest.mark.parametrize("revocation", ["route", "connection"])
async def test_watch_local_revocation_overrides_even_confirmed_completion(
    client, route, ci_peer, watched, app, clock, state, revocation
):
    """Disabled bindings and disconnected accounts cannot authorize a queue guard."""
    from throng_api.models import GitHubAccount

    async with app.state.db.write() as session:
        row = await session.get(CIReservation, watched)
        row.status, row.reason = state, "github_success" if state == "completed" else None
        row.started_at = clock.now
        binding = await session.get(CIRoute, route["id"])
        if revocation == "route":
            binding.enabled = False
        else:
            await session.delete(await session.get(GitHubAccount, binding.created_by))
    before = await platform_state(app)
    response = await watch(client, route, ci_peer)
    assert response.status_code == 200
    assert response.json() == {"status": "failed", "reason": "authorization_revoked"}
    assert await platform_state(app) == before


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"repository_id": "43"}, 403),
        ({"repository_owner_id": "89"}, 403),
        ({"workflow_ref": "test-org/repo/.github/workflows/other.yml@refs/heads/main"}, 403),
        ({"ref": "refs/heads/other"}, 403),
        ({"event_name": "pull_request"}, 403),
        ({"runner_environment": "self-hosted"}, 401),
        ({"aud": "https://another.example"}, 401),
        ({"exp": 0}, 401),
    ],
)
async def test_watch_rejects_unrelated_or_invalid_signed_identities(
    client, route, ci_peer, watched, app, claims, expected
):
    """A valid RSA signature cannot grant a different workflow or repository access."""
    before = await platform_state(app)
    response = await watch(client, route, ci_peer, claims=claims)
    assert response.status_code == expected
    assert await platform_state(app) == before


@pytest.mark.parametrize("authorization", [None, "Bearer thr_platform_token", "Bearer malformed"])
async def test_watch_needs_actions_oidc_instead_of_platform_tokens(
    client, route, ci_peer, watched, app, authorization
):
    """The public routing endpoint never accepts an ordinary platform or malformed bearer token."""
    before = await platform_state(app)
    response = await watch(
        client,
        route,
        ci_peer,
        headers={} if authorization is None else {"Authorization": authorization},
    )
    assert response.status_code == 401
    assert await platform_state(app) == before


async def test_watch_unknown_route_remains_unavailable(client, route, ci_peer, watched, app):
    """A signed identity cannot use an unknown route to discover another runner."""
    before = await platform_state(app)
    response = await watch(client, {**route, "id": str(uuid4())}, ci_peer)
    assert response.status_code == 404
    assert await platform_state(app) == before


@pytest.mark.parametrize("status", ["preparing", "ready"])
@pytest.mark.parametrize("remaining", [-1, 0, 1])
async def test_watch_checks_preparation_deadline_independently_of_execution_expiry(
    client, route, ci_peer, watched, app, clock, status, remaining
):
    """Preparation stops at its exact deadline even if the VM lease still has several minutes."""
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, watched)
        row.status = status
        row.deadline = clock.now + timedelta(seconds=remaining)
    before = await platform_state(app)
    response = await watch(client, route, ci_peer)
    assert response.status_code == 200
    assert response.json() == (
        {"status": "failed", "reason": "preparation_timeout"}
        if remaining <= 0
        else {"status": "waiting"}
    )
    assert await platform_state(app) == before


async def test_watch_recovers_signing_key_availability_without_changing_the_reservation(
    client, route, ci_peer, watched, app
):
    """A temporary JWKS outage is retryable HTTP 503, never an allocation or a false start."""
    before = await platform_state(app)
    ci_peer.failures[("GET", "/.well-known/jwks")] = 503
    response = await watch(client, route, ci_peer)
    assert response.status_code == 503
    assert await platform_state(app) == before
    ci_peer.failures.clear()
    response = await watch(client, route, ci_peer)
    assert response.status_code == 200 and response.json() == {"status": "waiting"}
    assert await platform_state(app) == before


async def test_watch_checks_expiration_at_start_of_state_read(
    client, route, ci_peer, watched, app, clock, monkeypatch
):
    """A selection already expired when its state read begins must fail.

    The real SQL sessions remain in use. Instrumentation advances the test clock
    when opening the state session, before its first SQL statement. Later clock
    changes must not be combined with a lifecycle snapshot from this earlier time.
    """
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, watched)
        row.expires_at = clock.now + timedelta(seconds=1)
    before, original, reads = await platform_state(app), app.state.db.read, 0

    @asynccontextmanager
    async def delayed_read():
        nonlocal reads
        async with original() as session:
            reads += 1
            if reads == 2:
                clock.now += timedelta(seconds=2)
            yield session

    monkeypatch.setattr(app.state.db, "read", delayed_read)
    response = await watch(client, route, ci_peer)
    assert response.status_code == 200
    assert response.json() == {"status": "failed", "reason": "runner_timeout"}
    assert await platform_state(app) == before
