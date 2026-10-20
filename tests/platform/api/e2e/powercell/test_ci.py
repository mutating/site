"""Actual HTTP/DB/scheduler CI cycles with an RSA-signed GitHub peer, no real VM."""

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import select

from tests.platform.api.e2e.test_github import (
    connect,
)
from tests.platform.api.e2e.test_github import (
    github as github_fixture,
)
from throng_api import ci_controller
from throng_api.execution import tick
from throng_api.models import CIReservation, CIRoute, Command, ComputerOwnership, Isolate

from .conftest import proof_headers

github = github_fixture

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]
GiB = 1024**3


def encode(value):
    """Encode a JWT JSON part using the peer's RSA key."""
    if not isinstance(value, bytes):
        value = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture
async def ci_peer(github, api, app, clock):
    """Extend only the external GitHub transport with runner and OIDC endpoints."""
    await connect(api)
    original = github.service.client._transport
    key = github.service.client._key
    numbers = key.public_key().public_numbers()
    peer = SimpleNamespace(
        admin=True,
        online=False,
        registered=False,
        jit_count=0,
        deletes=0,
        calls=[],
        run_status="in_progress",
        run_attempt=1,
        head_repository=42,
        failures={},
        barrier=None,
        jit_callback=None,
        jit_response=None,
        full_name="test-org/repo",
        scoped_repository_id=42,
        busy=False,
        extra_runners=[],
        run_callback=None,
        history={},
    )

    async def handler(request):
        peer.calls.append(request)
        path = request.url.path
        failure = peer.failures.get((request.method, path))
        if failure:
            return httpx.Response(failure, json={"message": "private-provider-error"})
        if path == "/.well-known/jwks":
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "kty": "RSA",
                            "alg": "RS256",
                            "kid": "github-test",
                            "n": encode(numbers.n.to_bytes(256, "big")),
                            "e": encode(numbers.e.to_bytes(3, "big")),
                        }
                    ]
                },
            )
        if path == "/repositories/42":
            if peer.barrier and request.headers.get("authorization") == "Bearer user-token":
                await peer.barrier.wait()
            return httpx.Response(
                200,
                json={
                    "id": peer.scoped_repository_id
                    if request.headers.get("authorization") == "Bearer scoped-installation-token"
                    else 42,
                    "full_name": peer.full_name,
                    "owner": {"id": 88},
                    "permissions": {"admin": peer.admin},
                },
            )
        if path == "/app/installations/456/access_tokens":
            return httpx.Response(201, json={"token": "scoped-installation-token"})
        if path == "/installation/token":
            return httpx.Response(204)
        if path.startswith("/repos/test-org/repo/actions/runs/1234/attempts/") and path.endswith(
            "/jobs"
        ):
            return httpx.Response(200, json={"total_count": 0, "jobs": []})
        if path == "/repos/test-org/repo/actions/runs/1234" or path.startswith(
            "/repos/test-org/repo/actions/runs/1234/attempts/"
        ):
            if peer.run_callback:
                await peer.run_callback()
            if "/attempts/" in path and int(path.rsplit("/", 1)[1]) in peer.history:
                return httpx.Response(200, json=peer.history[int(path.rsplit("/", 1)[1])])
            return httpx.Response(
                200,
                json={
                    "id": 1234,
                    "repository": {"id": 42},
                    "head_repository": {"id": peer.head_repository},
                    "head_branch": "main",
                    "event": "workflow_dispatch",
                    "run_attempt": peer.run_attempt,
                    "path": ".github/workflows/ci.yml",
                    "status": peer.run_status,
                    "conclusion": getattr(peer, "conclusion", "success"),
                },
            )
        if path == "/repos/test-org/repo/actions/runners":
            return httpx.Response(
                200,
                json={
                    "runners": ([{"id": 55, "name": peer.label}] if peer.registered else [])
                    + peer.extra_runners,
                    "total_count": int(peer.registered),
                },
            )
        if path.endswith("/generate-jitconfig"):
            peer.jit_count += 1
            peer.registered = True
            registration = json.loads(request.content)
            peer.label = registration["name"]
            peer.labels = registration["labels"]
            assert peer.labels == [peer.label]
            if peer.jit_callback:
                await peer.jit_callback()
            if peer.jit_response is not None:
                return httpx.Response(201, json=peer.jit_response)
            return httpx.Response(
                201,
                json={
                    "runner": {"id": 55},
                    "encoded_jit_config": "test-private-jit-config",
                },
            )
        if path == "/repos/test-org/repo/actions/runners/55":
            if not peer.registered:
                return httpx.Response(404, json={"message": "Not Found"})
            if request.method == "DELETE":
                peer.registered = False
                peer.deletes += 1
                return httpx.Response(204)
            return httpx.Response(
                200,
                json={
                    "id": 55,
                    "status": "online" if peer.online else "offline",
                    "busy": peer.busy,
                },
            )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)

    def token(**updates):
        now = int(clock.now.timestamp())
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": github.service.api_origin + "/v1/github/ci",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "sub": "repo:test-org/repo:ref:refs/heads/main",
            "runner_environment": "github-hosted",
            "repository_id": "42",
            "repository_owner_id": "88",
            "repository": "test-org/repo",
            "workflow_ref": "test-org/repo/.github/workflows/ci.yml@refs/heads/main",
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "event_name": "workflow_dispatch",
            "run_id": "1234",
            "run_attempt": "1",
        }
        claims.update(updates)
        message = encode({"alg": "RS256", "kid": "github-test"}) + "." + encode(claims)
        return (
            message + "." + encode(key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256()))
        )

    peer.token = token
    return peer


@pytest.fixture
async def route(api, ci_peer):
    """Bind the verified repository to Alice's personal pool through the API."""
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "preparation_seconds": 10,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def reserve(client, route, peer, instance="default", **claims):
    """Call the public CI endpoint with a real signed OIDC identity."""
    return await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve",
        headers={"Authorization": "Bearer " + peer.token(**claims)},
        json={"instance": instance},
    )


async def schedule(app, clock):
    """Advance the real scheduler under its regular serialized transaction."""
    async with app.state.db.write() as session:
        await tick(session, clock.now, app.state.settings)


class SignedAgent(httpx.Auth):
    """Sign native agent requests with the genuinely enrolled device key."""

    requires_request_body = True

    def __init__(self, identity, clock):
        self.identity, self.clock = identity, clock

    def auth_flow(self, request):
        target = request.url.raw_path.decode()
        headers = dict(request.headers)
        for name in ("Authorization", "X-Agent-Session", "Idempotency-Key"):
            if name in request.headers:
                headers[name] = request.headers[name]
        signed = proof_headers(
            self.identity.key,
            self.identity.certificate,
            request.method,
            target,
            request.content,
            headers,
            self.clock.now,
        )
        request.headers.update(signed)
        yield request


@pytest.fixture
async def powercell(client, bound_device, clock):
    """Use the real ACME/device enrollment and proof-checked HTTP agent protocol."""
    device = bound_device.identity
    headers = {"Authorization": "Bearer " + bound_device.binding["credential"]}
    async with httpx.AsyncClient(
        base_url=client.base_url,
        headers=headers,
        auth=SignedAgent(device, clock),
        trust_env=False,
        timeout=10,
    ) as agent:
        response = await agent.post(
            "/agent/v1/session",
            headers={"Idempotency-Key": str(uuid4())},
            json={"clean_start": True},
        )
        assert response.status_code == 200, response.text
        agent.headers["X-Agent-Session"] = response.json()["session_id"]
        response = await agent.post(
            "/agent/v1/capacity",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "revision": 0,
                "accepting_work": True,
                "mode": "idle",
                "cpu_millis": 2000,
                "memory_bytes": 8 * GiB,
                "disk_bytes": 20 * GiB,
                "default_isolation": "vm",
                "network_enabled": True,
                "internet_only": True,
                "running_commands": 0,
                "cpu_used_millis": 0,
                "memory_used_bytes": 0,
                "image_families": ["ghcr.io/actions/actions-runner"],
            },
        )
        assert response.status_code == 200, response.text
        yield SimpleNamespace(client=agent, computer_id=bound_device.binding["computer_id"])


async def ready_runner(client, route, ci_peer, powercell, app, clock, *, select_runner=True):
    """Provision, dispatch and observe runner readiness using the actual agent protocol."""
    response = await reserve(client, route, ci_peer)
    assert response.status_code == 200 and response.json()["status"] == "preparing", response.text
    await schedule(app, clock)
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    tasks = response.json()["tasks"]
    assert len(tasks) == 1, tasks
    create = tasks[0]
    assert (
        create["kind"] == "create"
        and create["payload"]["image"] == "ghcr.io/actions/actions-runner:latest"
    )
    response = await powercell.client.post(f"/agent/v1/tasks/{create['id']}/complete", json={})
    assert response.status_code == 200, response.text
    await ci_controller.run_once(app)
    await schedule(app, clock)
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    run = response.json()["tasks"][0]
    assert run["kind"] == "run"
    assert run["payload"]["env"]["POWERCELL_GITHUB_JIT_CONFIG"] == "test-private-jit-config"
    response = await powercell.client.post(f"/agent/v1/tasks/{run['id']}/started", json={})
    assert response.status_code == 200, response.text
    ci_peer.online = True
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    if select_runner:
        response = await reserve(client, route, ci_peer)
        assert response.json()["status"] == "ready", response.text
        return response.json(), run
    async with app.state.db.read() as session:
        row = await session.scalar(
            select(CIReservation).where(
                CIReservation.route_id == route["id"],
                CIReservation.run_id == 1234,
                CIReservation.run_attempt == 1,
                CIReservation.instance == "default",
            )
        )
        from throng_api.ci import decision

        return decision(row), run


async def test_no_capacity_is_bounded_and_idempotent(client, route, ci_peer, app, clock):
    """Repeated requests retain an immediate, resource-free capacity refusal."""
    first = (await reserve(client, route, ci_peer)).json()
    assert first["status"] == "failed"
    assert (await reserve(client, route, ci_peer)).json() == first
    clock.now += timedelta(seconds=11)
    await ci_controller.run_once(app)
    result = (await reserve(client, route, ci_peer)).json()
    assert result["status"] == "failed" and "runs_on" not in result
    assert result["reason"] == "no_capacity"
    assert ci_peer.jit_count == 0
    async with app.state.db.read() as session:
        assert len((await session.scalars(select(CIReservation))).all()) == 1


async def test_runner_cycle_credentials_and_cleanup(
    client, api, route, ci_peer, powercell, app, clock
):
    """A ready runner is selected; credentials never enter command/task storage or history."""
    result, task = await ready_runner(client, route, ci_peer, powercell, app, clock)
    assert result["runs_on"][0].startswith("powercell-")
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        command = await session.get(Command, row.command_id)
        assert command.env == {} and "test-private-jit-config" not in row.credentials
    history = await api.get(f"/v1/github/routes/{route['id']}/runs")
    assert (
        history.status_code == 200
        and "test-private-jit-config" not in history.text
        and "credentials" not in history.text
    )
    response = await powercell.client.post(
        f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 200, response.text
    ci_peer.run_status = "completed"  # GitHub confirms the job result after the runner exits.
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    destroy = response.json()["tasks"][0]
    assert destroy["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
    ).status_code == 200
    clock.now += timedelta(seconds=65)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.cleaned_at and row.credentials is None
    assert ci_peer.deletes == 1


@pytest.mark.parametrize(
    "claims,code",
    [
        ({"repository_id": "99"}, 403),
        ({"repository_owner_id": "99"}, 403),
        ({"ref": "refs/heads/evil"}, 403),
        (
            {"workflow_ref": "test-org/repo/.github/workflows/evil.yml@refs/heads/main"},
            403,
        ),
        ({"event_name": "pull_request_target"}, 403),
        ({"aud": "other"}, 401),
        ({"iss": "https://evil.test"}, 401),
        ({"exp": 0}, 401),
        ({"runner_environment": "self-hosted"}, 401),
    ],
)
async def test_claims_cannot_cross_a_binding(client, route, ci_peer, claims, code):
    """A valid RSA signature cannot authorize another repository, owner, workflow or event."""
    response = await reserve(client, route, ci_peer, **claims)
    assert response.status_code == code, response.text


async def test_parallel_limit_and_disabled_route(
    client, api, route, ci_peer, powercell, app, clock
):
    """Each matrix instance is distinct, while the route-wide limit applies atomically."""
    assert (await reserve(client, route, ci_peer, "matrix-one")).json()["status"] == "preparing"
    second = (await reserve(client, route, ci_peer, "matrix-two")).json()
    assert second["reason"] == "concurrency_limit"
    assert (await api.delete(f"/v1/github/routes/{route['id']}")).status_code == 204
    await ci_controller.run_once(app)
    assert (await reserve(client, route, ci_peer, "matrix-one")).json()[
        "reason"
    ] == "authorization_revoked"


async def test_binding_requires_repository_admin(api, ci_peer):
    """A GitHub user who can read the repo cannot register its CI runners."""
    ci_peer.admin = False
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
        },
    )
    assert response.status_code == 403


async def test_webhook_signature_dedup_and_terminal_order(
    client, api, route, ci_peer, powercell, app, clock, github
):
    """Only a signed event for the assigned runner can finish it; stale starts cannot reopen it."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    payload = {
        "action": "completed",
        "installation": {"id": 456},
        "repository": {"id": 42},
        "workflow_job": {
            "run_id": 1234,
            "run_attempt": 1,
            "runner_id": 55,
            "runner_name": result["runs_on"][0],
            "conclusion": "success",
        },
    }
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": "workflow_job", "X-GitHub-Delivery": str(uuid4())}
    assert (
        await client.post("/v1/github/webhook", content=body, headers=headers)
    ).status_code == 401
    headers["X-Hub-Signature-256"] = (
        "sha256="
        + hmac.new(github.service.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    for _ in range(2):
        assert (
            await client.post("/v1/github/webhook", content=body, headers=headers)
        ).status_code == 202
    payload["action"] = "in_progress"
    body = json.dumps(payload).encode()
    headers["X-GitHub-Delivery"] = str(uuid4())
    headers["X-Hub-Signature-256"] = (
        "sha256="
        + hmac.new(github.service.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    assert (
        await client.post("/v1/github/webhook", content=body, headers=headers)
    ).status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "completed" and row.credentials is None


async def test_workflow_example_and_cross_user_access(api, other_api, route, ci_peer):
    """Generated YAML uses a routing dependency; another platform owner cannot read it."""
    path = f"/v1/github/routes/{route['id']}/workflow"
    response = await api.get(path)
    assert response.status_code == 200, response.text
    example = response.json()["yaml"]
    assert (
        "id-token: write" in example
        and "needs.powercell_route_" + route["id"].replace("-", "") + ".outputs.runs_on" in example
    )
    assert route["id"] in example and "client-secret" not in example
    assert (await other_api.get(path)).status_code == 404


async def test_github_permission_revocation_prevents_any_isolate(client, api, route, ci_peer, app):
    """Loss of repository admin declines new work before allocating an isolate."""
    ci_peer.admin = False
    response = await reserve(client, route, ci_peer)
    assert response.status_code == 200, response.text
    assert (
        response.json()["status"] == "failed" and response.json()["reason"] == "github_unavailable"
    )
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert row.isolate_id is None


@pytest.mark.parametrize(
    "changes",
    [
        {"cpu_millis": 1000001},
        {"memory_bytes": 2**50 + 1},
        {"disk_bytes": 1025 * GiB},
        {"workflow_path": ".github/workflows/../evil.yml"},
        {"ref": "refs/heads/../evil"},
        {"job_key": "powercell_route"},
    ],
)
async def test_invalid_resource_and_workflow_policy(api, ci_peer, changes):
    """Configuration rejects invalid platform limits and workflow paths."""
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            **changes,
        },
    )
    assert response.status_code == 422


async def test_no_capacity_is_an_explicit_failure(client, api, route, ci_peer, app, clock):
    """A capacity refusal has no runner output and never lowers job requirements."""
    await reserve(client, route, ci_peer)
    clock.now += timedelta(seconds=11)
    await ci_controller.run_once(app)
    response = await reserve(client, route, ci_peer)
    assert response.json()["status"] == "failed" and "runs_on" not in response.json()


async def test_disconnect_disables_routes_and_preparation(
    client, api, route, ci_peer, powercell, app
):
    """Disconnect removes saved authorization and prevents queued CI from acquiring resources."""
    await reserve(client, route, ci_peer)
    assert (await api.delete("/v1/github/connection")).status_code == 204
    await ci_controller.run_once(app)
    routes = (await api.get("/v1/github/routes")).json()
    assert routes[0]["enabled"] is False
    assert (await reserve(client, route, ci_peer)).json()["reason"] == "authorization_revoked"


async def test_unready_runner_cannot_leak_late_credentials(
    client, api, route, ci_peer, powercell, app, clock
):
    """Registration alone is not readiness; deadline expiry stops delivery and withdraws work."""
    await reserve(client, route, ci_peer)
    await schedule(app, clock)
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    create = response.json()["tasks"][0]
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{create['id']}/complete", json={})
    ).status_code == 200
    await ci_controller.run_once(app)
    await schedule(app, clock)
    assert ci_peer.jit_count == 1
    response = await reserve(client, route, ci_peer)
    assert response.json()["status"] == "preparing"
    clock.now += timedelta(seconds=11)
    # No CI tick yet: the agent endpoint independently checks the absolute deadline.
    response = await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.json()["tasks"] == []
    await ci_controller.run_once(app)
    response = await reserve(client, route, ci_peer)
    assert response.json()["status"] == "failed"
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert row.credentials is None


async def test_new_attempt_does_not_reuse_previous_reservation(client, route, ci_peer, powercell):
    """Rerunning a workflow uses a new idempotency domain, even when the prior run is active."""
    first = (await reserve(client, route, ci_peer)).json()
    second = (await reserve(client, route, ci_peer, run_attempt="2")).json()
    assert second["id"] != first["id"] and second["reason"] == "concurrency_limit"


async def test_company_route_requires_pool_management(api, other_api, company, membership, ci_peer):
    """GitHub admin and ordinary company execution access do not grant pool configuration rights."""
    response = await other_api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "company_id": company["id"],
        },
    )
    assert response.status_code == 403
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "company_id": company["id"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["company_id"] == company["id"] and response.json()["user_id"] is None


async def test_invalid_and_cross_pool_computer_selection(
    api, ci_peer, powercell, company, client, app, clock
):
    """A selected computer must belong to the selected personal/company pool."""
    payload = {
        "installation_id": 456,
        "repository_id": 42,
        "workflow_path": ".github/workflows/ci.yml",
        "job_key": "test",
        "computer_ids": [powercell.computer_id],
    }
    response = await api.post("/v1/github/routes", json={**payload, "company_id": company["id"]})
    assert response.status_code == 404
    response = await api.post("/v1/github/routes", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["computer_ids"] == [powercell.computer_id]
    binding = response.json()
    decision = (await reserve(client, binding, ci_peer)).json()
    await schedule(app, clock)
    async with app.state.db.read() as session:
        reservation = await session.get(CIReservation, decision["id"])
        isolate = await session.get(Isolate, reservation.isolate_id)
        assert isolate.computer_id == powercell.computer_id


async def test_concurrent_retries_reserve_once(client, route, ci_peer, app):
    """Concurrent idempotent calls check GitHub outside the lock, then reserve a single isolate."""
    ci_peer.barrier = asyncio.Barrier(2)
    first, second = await asyncio.gather(
        reserve(client, route, ci_peer), reserve(client, route, ci_peer)
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    async with app.state.db.read() as session:
        assert len((await session.scalars(select(CIReservation))).all()) == 1


async def test_cancellation_tombstone_wins_against_slow_reserve(client, route, ci_peer, app):
    """A client timeout can cancel an in-flight request before an isolate is committed."""
    started, release = asyncio.Event(), asyncio.Event()

    class Barrier:
        async def wait(self):
            started.set()
            await release.wait()

    ci_peer.barrier = Barrier()
    pending = asyncio.create_task(reserve(client, route, ci_peer))
    await asyncio.wait_for(started.wait(), 2)
    try:
        response = await client.post(
            f"/v1/github/ci/routes/{route['id']}/cancel",
            headers={"Authorization": "Bearer " + ci_peer.token()},
            json={"instance": "default"},
        )
        assert response.status_code == 200, response.text
    finally:
        release.set()
    response = await pending
    assert response.json()["reason"] == "bootstrap_cancelled"
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert row.isolate_id is None and row.cleaned_at


async def test_preparing_reservation_has_no_label_and_cancel_is_idempotent(
    client, route, ci_peer, powercell
):
    """Preparation has no runner output; repeated cancellation withdraws it once."""
    endpoint = f"/v1/github/ci/routes/{route['id']}"
    headers = {"Authorization": "Bearer " + ci_peer.token()}
    first = await reserve(client, route, ci_peer)
    assert first.status_code == 200 and first.json()["status"] == "preparing"
    assert "runs_on" not in first.json()
    assert (await reserve(client, route, ci_peer)).json() == first.json()
    for _ in range(2):
        response = await client.post(endpoint + "/cancel", headers=headers, json={})
        assert response.status_code == 200 and response.json()["reason"] == "bootstrap_cancelled"


async def test_selected_runner_without_job_is_bounded(
    client, route, ci_peer, powercell, app, clock
):
    """Losing the final bootstrap response cannot leave an idle runner consuming resources for hours."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 200
    clock.now += timedelta(seconds=301)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "timed_out" and row.credentials is None


async def test_slow_authorization_counts_toward_preparation_budget(
    client, route, ci_peer, clock, app
):
    """A slow GitHub check cannot extend the platform's absolute preparation bound."""

    class Slow:
        async def wait(self):
            clock.now += timedelta(seconds=11)

    ci_peer.barrier = Slow()
    response = await reserve(client, route, ci_peer)
    assert response.json()["reason"] == "preparation_timeout"
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert row.isolate_id is None


async def deliver(client, github, payload, event="workflow_job", delivery=None, body=None):
    """Sign the exact provider bytes while keeping delivery IDs independent of HTTP retries."""
    body = json.dumps(payload).encode() if body is None else body
    signature = hmac.new(github.service.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/v1/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid4()),
            "X-Hub-Signature-256": "sha256=" + signature,
        },
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"installation":null}',
        b'{"workflow_job":[]}',
        b'{"repositories_removed":{}}',
        b'{"repositories_removed":[1]}',
    ],
)
async def test_malformed_signed_webhooks_are_rejected(client, github, body):
    """A signed malformed payload does not escape as a server error or durable delivery."""
    response = await deliver(client, github, {}, body=body)
    assert response.status_code == 400


async def test_webhook_delivery_integrity_and_size(client, github):
    """Delivery IDs are bounded, changed bytes are rejected and oversized bodies fail before parsing."""
    response = await deliver(client, github, {}, event="ping", delivery="invalid/id")
    assert response.status_code == 400
    identifier = str(uuid4())
    assert (await deliver(client, github, {}, event="ping", delivery=identifier)).status_code == 202
    response = await deliver(client, github, {"changed": True}, event="ping", delivery=identifier)
    assert response.status_code == 409
    response = await deliver(client, github, {}, body=b"x" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413


@pytest.mark.parametrize(
    "event,action,removed,disabled",
    [
        ("installation", "suspend", [], True),
        ("installation", "deleted", [], True),
        ("installation", "unsuspend", [], False),
        ("installation_repositories", "removed", [{"id": 42}], True),
        ("installation_repositories", "removed", [{"id": 99}], False),
    ],
)
async def test_installation_changes_disable_only_affected_routes(
    client, api, route, ci_peer, github, event, action, removed, disabled
):
    """Revocation events stop new work; unrelated repository changes preserve the binding."""
    response = await deliver(
        client,
        github,
        {
            "action": action,
            "installation": {"id": 456},
            "repositories_removed": removed,
        },
        event=event,
    )
    assert response.status_code == 202
    rows = (await api.get("/v1/github/routes")).json()
    assert rows[0]["enabled"] is not disabled


async def test_unrelated_job_and_old_attempt_do_not_finish_runner(
    client, route, ci_peer, powercell, app, clock, github
):
    """Other jobs, incomplete events and a prior attempt cannot release this runner's reservation."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    notification = await deliver(
        client,
        github,
        {
            "action": "in_progress",
            "installation": {"id": 456},
            "repository": {"id": 42},
            "workflow_run": {"id": 1234, "run_attempt": 1},
        },
        event="workflow_run",
    )
    assert notification.status_code == 202
    job = {
        "run_id": 1234,
        "run_attempt": 1,
        "runner_id": 55,
        "runner_name": result["runs_on"][0],
        "conclusion": "success",
    }
    for changes in (
        {"runner_id": 99},
        {"runner_name": "foreign"},
        {"run_attempt": 2},
        {"run_attempt": None},
    ):
        response = await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": {**job, **changes},
            },
        )
        assert response.status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "selected"
    assert (
        await deliver(
            client,
            github,
            {
                "action": "in_progress",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": job,
            },
        )
    ).status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "running"
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/cancel",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 409
    assert (
        await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_run": {
                    "id": 1234,
                    "run_attempt": 1,
                    "conclusion": "cancelled",
                },
            },
            event="workflow_run",
        )
    ).status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "cancelled"


async def provision_only(client, route, ci_peer, powercell, app, clock):
    """Prepare a real isolate while keeping the external runner-registration boundary unopened."""
    response = await reserve(client, route, ci_peer)
    assert response.status_code == 200, response.text
    await schedule(app, clock)
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["kind"] == "create"
    response = await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("failure", ["finished", "foreign_head", "different_attempt"])
async def test_changed_workflow_run_cannot_register_runner(
    client, route, ci_peer, powercell, app, clock, failure
):
    """A completed run or changed provider identity is rejected before JIT registration."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    if failure == "finished":
        ci_peer.run_status = "completed"
    elif failure == "foreign_head":
        ci_peer.head_repository = 99
    else:
        ci_peer.run_attempt = 2
    await ci_controller.run_once(app)
    result = (await reserve(client, route, ci_peer)).json()
    assert result["status"] == "failed" and ci_peer.jit_count == 0


@pytest.mark.parametrize("failure", ["deadline", "isolate_lost", "claim_lost"])
async def test_late_jit_response_never_delivers_a_secret(
    client, route, ci_peer, powercell, app, clock, failure
):
    """A delayed external side effect cannot escape cancellation or another controller's claim."""
    await provision_only(client, route, ci_peer, powercell, app, clock)

    async def during_jit():
        if failure == "deadline":
            clock.now += timedelta(seconds=11)
        else:
            async with app.state.db.write() as session:
                row = await session.scalar(select(CIReservation))
                if failure == "claim_lost":
                    row.lease_nonce = "new-controller"
                else:
                    isolate = await session.get(Isolate, row.isolate_id)
                    isolate.status = "failed"

    ci_peer.jit_callback = during_jit
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.scalar(select(CIReservation))
        assert row.command_id is None
        assert row.credentials is None
    clock.now += timedelta(seconds=61)
    await ci_controller.run_once(app)
    assert ci_peer.registered is False


async def test_provider_failure_fails_and_never_logs_credentials(
    client, route, ci_peer, powercell, app, clock, caplog
):
    """GitHub failure during registration withdraws the reservation with redacted diagnostics."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    ci_peer.failures[("POST", "/repos/test-org/repo/actions/runners/generate-jitconfig")] = 503
    await ci_controller.run_once(app)
    result = (await reserve(client, route, ci_peer)).json()
    assert result["status"] == "failed" and result["reason"] == "github_unavailable"
    assert "private-provider-error" not in caplog.text and "user-token" not in caplog.text


async def test_orphan_registration_is_recovered_by_name(
    client, route, ci_peer, powercell, app, clock
):
    """Restart cleanup removes a runner whose GitHub ID never reached the database."""
    response = await reserve(client, route, ci_peer)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        ci_peer.label = row.label
    ci_peer.registered = True
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/cancel",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 200
    await ci_controller.run_once(app)
    assert ci_peer.deletes == 1 and ci_peer.registered is False


async def test_lost_agent_retains_reserved_resources(client, route, ci_peer, powercell, app, clock):
    """A vanished Mac cannot make the same CPU/memory available before confirmed cleanup."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    clock.now += timedelta(seconds=61)
    await schedule(app, clock)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert row.status == "failed" and isolate.reserved and row.cleaned_at is None


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("foreign_app", 404),
        ("suspended", 404),
        ("unsafe_name", 502),
        ("wrong_scoped_repo", 502),
        ("provider_error", 502),
    ],
)
async def test_binding_verifies_installation_and_repository_boundaries(
    api, ci_peer, github, kind, expected
):
    """Repository readability cannot substitute for the correct App installation and repository identity."""
    if kind == "foreign_app":
        github.peer.installation["app_id"] = 999
    elif kind == "suspended":
        github.peer.installation["suspended_at"] = "2026-10-16T00:00:00Z"
    elif kind == "unsafe_name":
        ci_peer.full_name = "../other/repo"
    elif kind == "wrong_scoped_repo":
        ci_peer.scoped_repository_id = 99
    else:
        ci_peer.failures[("GET", "/app/installations/456")] = 503
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
        },
    )
    assert response.status_code == expected, response.text


async def test_non_powercell_computer_rejected_without_orphan_artifact(
    api, ci_peer, computer, settings
):
    """Invalid selected runtimes do not leave an unused initial-state artifact behind."""
    before = set(settings.storage_path.iterdir())
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "computer_ids": [computer["id"]],
        },
    )
    assert response.status_code == 422
    assert set(settings.storage_path.iterdir()) == before


async def test_company_route_visibility_respects_execution_permissions(
    api, other_api, company, membership, ci_peer
):
    """Removing execution.read hides CI history without removing ordinary organization membership."""
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "company_id": company["id"],
        },
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    for suffix in ("runs", "workflow"):
        assert (await api.get(f"/v1/github/routes/{identifier}/{suffix}")).status_code == 200
    assert (await other_api.delete(f"/v1/github/routes/{identifier}")).status_code == 403
    assert (
        await api.patch(
            f"/v1/memberships/{membership['id']}",
            json={"permissions": ["resources.read"]},
        )
    ).status_code == 200
    assert (await other_api.get("/v1/github/routes")).json() == []
    for suffix in ("runs", "workflow"):
        assert (await other_api.get(f"/v1/github/routes/{identifier}/{suffix}")).status_code == 403
    assert (await api.get("/v1/github/routes")).json()[0]["id"] == identifier
    assert (await api.delete(f"/v1/github/routes/{identifier}")).status_code == 204


@pytest.mark.parametrize("revoke", ["remove", "limit"])
async def test_creator_loses_company_pool_authority(
    client, api, other_api, company, membership, ci_peer, github, powercell, app, revoke
):
    """Revoking a binding creator's live company rights stops existing preparation."""
    assert (
        await api.patch(f"/v1/memberships/{membership['id']}", json={"role": "admin"})
    ).status_code == 200
    github.peer.user_id = 790
    await connect(other_api)
    response = await other_api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
            "company_id": company["id"],
        },
    )
    assert response.status_code == 201, response.text
    # Supply an online Mac in this pool before revoking the creator's authority.
    async with app.state.db.write() as session:
        ownership = await session.scalar(
            select(ComputerOwnership).where(ComputerOwnership.computer_id == powercell.computer_id)
        )
        ownership.user_id, ownership.company_id = None, company["id"]
    binding = response.json()
    assert (await reserve(client, binding, ci_peer)).json()["status"] == "preparing"
    if revoke == "remove":
        assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    else:
        assert (
            await api.patch(f"/v1/memberships/{membership['id']}", json={"permissions": []})
        ).status_code == 200
    await ci_controller.run_once(app)
    assert (await reserve(client, binding, ci_peer)).json()["reason"] == "authorization_revoked"


async def test_polling_observes_job_start_without_webhook(
    client, route, ci_peer, powercell, app, clock
):
    """A missing start webhook is repaired by GitHub's busy status and extends the isolate lifetime."""
    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    ci_peer.busy = True
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        assert row.status == "running" and isolate.expires_at == row.expires_at
        assert (row.expires_at - clock.now).total_seconds() == 3600


async def test_regular_isolates_count_toward_ci_owner_quota(
    client, api, route, ci_peer, app, isolate_payload
):
    """CI cannot bypass the same owner quota already consumed by an ordinary isolate."""
    app.state.settings.max_owner_isolates = 1
    response = await api.post(
        "/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=isolate_payload
    )
    assert response.status_code == 202
    result = (await reserve(client, route, ci_peer)).json()
    assert result["status"] == "failed" and result["reason"] == "isolate_quota"


async def test_disabled_route_and_unknown_route_allocate_nothing(client, api, route, ci_peer):
    """Disabled routes retain a deterministic refusal; unknown route IDs remain unavailable."""
    assert (await api.delete(f"/v1/github/routes/{route['id']}")).status_code == 204
    response = await reserve(client, route, ci_peer)
    assert response.json()["reason"] == "route_disabled"
    response = await reserve(client, {**route, "id": str(uuid4())}, ci_peer)
    assert response.status_code == 404


async def test_expired_selection_cannot_be_republished(
    client, route, ci_peer, powercell, app, clock
):
    """Reserve checks expiration before returning a previously selected label."""
    await ready_runner(client, route, ci_peer, powercell, app, clock)
    clock.now += timedelta(seconds=301)
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/reserve",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 409


async def test_existing_registration_is_replaced_before_a_fresh_jit(
    client, route, ci_peer, powercell, app, clock
):
    """A controller restart can find its unused registration and create one fresh runner command."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    async with app.state.db.read() as session:
        ci_peer.label = (await session.scalar(select(CIReservation))).label
    ci_peer.registered = True
    ci_peer.extra_runners = [{"id": 99, "name": "another-runner"}]
    await ci_controller.run_once(app)
    assert ci_peer.deletes == 1 and ci_peer.jit_count == 1


async def test_malformed_jit_response_is_not_executed(
    client, route, ci_peer, powercell, app, clock
):
    """A provider response with an invalid registration cannot become executable agent work."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    ci_peer.jit_response = {
        "runner": {"id": 0},
        "encoded_jit_config": "private-invalid-config",
    }
    await ci_controller.run_once(app)
    response = await reserve(client, route, ci_peer)
    assert response.json()["reason"] == "github_unavailable"
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, response.json()["id"])
        assert row.command_id is None and row.credentials is None


@pytest.mark.parametrize("status", [404, 403])
async def test_runner_cleanup_handles_absence_and_retries_denial(
    client, route, ci_peer, powercell, app, clock, status
):
    """Deleting an absent registration succeeds; denied deletion is retained for a later retry."""
    result, task = await ready_runner(client, route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    ci_peer.failures[("DELETE", "/repos/test-org/repo/actions/runners/55")] = status
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.runner_id == 55  # Keep the job identity until its provisional result settles.
        if status == 403:
            assert row.next_check > clock.now


async def test_background_controller_retries_without_exposing_exception_details(
    app, client, ci_peer, monkeypatch, caplog
):
    """An unexpected DB failure is retried by the real loop and does not print secret-bearing exceptions."""
    from contextlib import asynccontextmanager

    integration = app.state.github
    app.state.github = None
    await ci_controller.run_once(app)
    app.state.github = integration
    original = app.state.db.write
    succeeded, stop = asyncio.Event(), asyncio.Event()
    failed = False

    @asynccontextmanager
    async def unreliable_write():
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("private-db-credential")
        async with original() as session:
            yield session
        succeeded.set()

    monkeypatch.setattr(app.state.db, "write", unreliable_write)
    task = asyncio.create_task(ci_controller.run(app, stop))
    try:
        await asyncio.wait_for(succeeded.wait(), 3)
    finally:
        stop.set()
        await task
    assert "private-db-credential" not in caplog.text


async def test_route_quota_preserves_storage_on_rejection(api, route, ci_peer, app, settings):
    """The per-pool binding limit rejects new configuration without creating an orphan file."""
    async with app.state.db.write() as session:
        original = await session.get(CIRoute, route["id"])
        values = {
            column.key: getattr(original, column.key)
            for column in CIRoute.__table__.columns
            if column.key not in {"id", "created_at"}
        }
        session.add_all([CIRoute(**values) for _ in range(99)])
    before = set(settings.storage_path.iterdir())
    response = await api.post(
        "/v1/github/routes",
        json={
            "installation_id": 456,
            "repository_id": 42,
            "workflow_path": ".github/workflows/ci.yml",
            "job_key": "test",
        },
    )
    assert response.status_code == 409
    assert set(settings.storage_path.iterdir()) == before


async def test_workflow_quota_and_cleanup_of_an_unassigned_record(
    client, route, ci_peer, app, clock
):
    """A large matrix cannot bypass its history bound, while an unfinished allocation remains cancellable."""
    async with app.state.db.write() as session:
        for index in range(1000):
            session.add(
                CIReservation(
                    route_id=route["id"],
                    run_id=1234,
                    run_attempt=1,
                    instance=f"item-{index}",
                    label="powercell-" + str(uuid4()),
                    status="failed",
                    reason="bootstrap_cancelled",
                    deadline=clock.now,
                    expires_at=clock.now,
                    next_check=clock.now,
                    finished_at=clock.now,
                    cleaned_at=clock.now,
                )
            )
    assert (await reserve(client, route, ci_peer)).status_code == 409
    endpoint = f"/v1/github/ci/routes/{route['id']}"
    headers = {"Authorization": "Bearer " + ci_peer.token()}
    assert (await client.post(endpoint + "/cancel", headers=headers, json={})).status_code == 409
    # Recover a nullable, unfinished allocation record with no assigned isolate.
    async with app.state.db.write() as session:
        row = await session.scalar(select(CIReservation).where(CIReservation.instance == "item-0"))
        row.status, row.cleaned_at, row.finished_at = "preparing", None, None
        row.deadline = clock.now + timedelta(seconds=10)
        row.expires_at = clock.now + timedelta(hours=1)
    assert await ci_controller.claim_work(app) is None
    response = await client.post(endpoint + "/cancel", headers=headers, json={"instance": "item-0"})
    assert response.status_code == 200
    await ci_controller.run_once(app)


async def test_online_registration_alone_is_not_ready(
    client, route, ci_peer, powercell, app, clock
):
    """The provider's online status cannot substitute for the agent's execution acknowledgement."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    await ci_controller.run_once(app)
    await schedule(app, clock)
    ci_peer.online = True
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    assert (await reserve(client, route, ci_peer)).json()["status"] == "preparing"


async def test_completion_racing_with_poll_is_terminal(
    client, route, ci_peer, powercell, app, clock, github
):
    """A completion webhook arriving during a status request cannot be replaced by cancellation."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    ci_peer.run_status = "completed"

    async def completed():
        response = await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_run": {"id": 1234, "run_attempt": 1, "conclusion": "success"},
            },
            event="workflow_run",
        )
        assert response.status_code == 202

    ci_peer.run_callback = completed
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        assert (await session.scalar(select(CIReservation))).status == "completed"


async def test_jit_delivery_is_assignment_scoped_and_stops_after_cancellation(
    client, route, ci_peer, powercell, app, clock, agent
):
    """Only the assigned agent can read credentials; completed tasks contain no saved secret."""
    _, task = await ready_runner(client, route, ci_peer, powercell, app, clock)
    path = f"/agent/v1/tasks/{task['id']}"
    response = await client.get(path, headers=agent["headers"])
    assert response.status_code == 404 and "test-private-jit-config" not in response.text
    response = await powercell.client.get(path)
    assert (
        response.status_code == 200
        and response.json()["payload"]["env"]["POWERCELL_GITHUB_JIT_CONFIG"]
        == "test-private-jit-config"
    )
    integration = app.state.github
    app.state.github = None
    try:
        assert (await powercell.client.get(path)).status_code == 409
    finally:
        app.state.github = integration
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/cancel",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 200
    assert (await powercell.client.get(path)).status_code == 409
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert tasks[0]["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    ).status_code == 200
    response = await powercell.client.get(path)
    assert response.status_code == 200 and "test-private-jit-config" not in response.text
