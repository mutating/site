"""Regression assertions confirmed during the repeated Actions review."""

import asyncio
import json
import re
from datetime import timedelta

import httpx
import pytest
import yaml
from sqlalchemy import func, select

from tests.platform.api.e2e.test_github import begin, connect
from throng_api import ci, ci_controller
from throng_api.models import Artifact, CIReservation, CIRoute, Command, GitHubAccount, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, provision_only, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


def policy(**changes):
    """Build an explicit CI binding policy with targeted overrides."""
    return {
        "installation_id": 456,
        "repository_id": 42,
        "workflow_path": ".github/workflows/ci.yml",
        "job_key": "test",
        **changes,
    }


# Assertions: impossible Git branch names are rejected before GitHub requests or
# persisted artifacts; ordinary nested/dotted branches still round-trip into YAML.
@pytest.mark.parametrize(
    "branch,expected",
    [
        ("topic/", 422),
        ("topic.", 422),
        (".hidden", 422),
        ("topic/.hidden", 422),
        ("topic.lock", 422),
        ("topic.lock/next", 422),
        ("topic/name.lock", 422),
        ("main", 201),
        ("topic.locked/release-v1.2", 201),
        ("topic./release", 201),
    ],
)
async def test_binding_requires_a_valid_git_branch(api, ci_peer, branch, expected):
    """Reject Git-invalid refs while preserving supported branch names."""
    before = len(ci_peer.calls)
    response = await api.post("/v1/github/routes", json=policy(ref=f"refs/heads/{branch}"))
    assert response.status_code == expected
    routes = (await api.get("/v1/github/routes")).json()
    assert len(routes) == int(expected == 201)
    if expected == 422:
        assert len(ci_peer.calls) == before
    else:
        assert routes[0]["ref"] == f"refs/heads/{branch}"
        workflow = await api.get(f"/v1/github/routes/{routes[0]['id']}/workflow")
        assert f'branches: ["{branch}"]' in workflow.json()["yaml"]


# Assertions: authorization is applied before LIMIT/OFFSET; an older unreadable
# organization binding cannot hide or shift a subsequent readable personal binding.
async def test_route_pagination_counts_only_authorized_rows(api, other_api, user, ci_peer):
    """Verify route pagination counts only authorized rows."""
    company = (await other_api.post("/v1/companies", json={"name": "Private CI"})).json()
    member = await other_api.post(
        "/v1/memberships",
        json={"company_id": company["id"], "user_id": user["id"], "role": "admin"},
    )
    assert member.status_code == 201
    hidden = await api.post("/v1/github/routes", json=policy(company_id=company["id"]))
    assert hidden.status_code == 201
    changed = await other_api.patch(
        f"/v1/memberships/{member.json()['id']}",
        json={"role": "user", "permissions": ["resources.read"]},
    )
    assert changed.status_code == 200
    visible = await api.post("/v1/github/routes", json=policy())
    assert visible.status_code == 201
    first = await api.get("/v1/github/routes", params={"limit": 1, "offset": 0})
    assert first.status_code == 200 and [row["id"] for row in first.json()] == [
        visible.json()["id"]
    ]
    assert (await api.get("/v1/github/routes", params={"limit": 1, "offset": 1})).json() == []


# Assertions: every accepted Job ID remains a string key when a YAML parser
# reads the generated workflow, including YAML boolean/null scalar spellings.
@pytest.mark.parametrize("job_key", ["null", "true", "false", "on", "yes", "OFF", "regular-job"])
async def test_workflow_preserves_scalar_like_job_ids(api, ci_peer, job_key):
    """Verify workflow preserves scalar like job ids."""
    created = await api.post("/v1/github/routes", json=policy(job_key=job_key))
    assert created.status_code == 201
    response = await api.get(f"/v1/github/routes/{created.json()['id']}/workflow")
    assert response.status_code == 200
    document = yaml.safe_load(response.json()["yaml"])
    assert set(document["jobs"]) == {
        "powercell_route_" + created.json()["id"].replace("-", ""),
        job_key,
    }
    assert all(isinstance(key, str) for key in document["jobs"])


@pytest.fixture
async def review_route(api, ci_peer):
    """Create an authorized binding for lifecycle regressions."""
    created = await api.post("/v1/github/routes", json=policy(preparation_seconds=90))
    assert created.status_code == 201
    return created.json()


# Assertions: once missing GitHub admin is observed, readiness/selection/running
# cannot survive it; credentials are cleared and later reservation calls fail.
@pytest.mark.parametrize("phase", ["ready", "selected", "running"])
async def test_discovered_authorization_revocation_stops_the_runner(
    client, review_route, ci_peer, powercell, app, clock, phase
):
    """Verify discovered authorization revocation stops the runner."""
    result, _ = await ready_runner(
        client, review_route, ci_peer, powercell, app, clock, select_runner=phase != "ready"
    )
    if phase == "running":
        ci_peer.busy = True
        clock.now += timedelta(seconds=5)
        await ci_controller.run_once(app)
    ci_peer.admin = False
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("failed" if phase == "ready" else "cancelled")
        assert row.reason == "authorization_revoked" and row.credentials is None
    response = await client.post(
        f"/v1/github/ci/routes/{review_route['id']}/reserve",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert response.json()["reason"] == "authorization_revoked" and "runs_on" not in response.json()


# Assertions: outages and remote rate-limit 403s fail closed before selection,
# but do not kill an already selected/running CI job as if its admin were revoked.
@pytest.mark.parametrize("phase", ["ready", "selected", "running"])
@pytest.mark.parametrize("status", [403, 500])
async def test_provider_outages_preserve_started_selections(
    client, review_route, ci_peer, powercell, app, clock, phase, status
):
    """Verify provider outages preserve started selections."""
    result, _ = await ready_runner(
        client, review_route, ci_peer, powercell, app, clock, select_runner=phase != "ready"
    )
    if phase == "running":
        ci_peer.busy = True
        clock.now += timedelta(seconds=5)
        await ci_controller.run_once(app)
    ci_peer.failures[("GET", "/repositories/42")] = status
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("failed" if phase == "ready" else phase)


# Assertions: disable is a synchronous boundary for fresh JIT delivery; the
# assigned agent cannot retrieve credentials even before another controller tick.
async def test_disabled_route_immediately_refuses_fresh_jit_delivery(
    client, api, review_route, ci_peer, powercell, app, clock
):
    """Verify disabled route immediately refuses fresh jit delivery."""
    _, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    assert (await api.delete(f"/v1/github/routes/{review_route['id']}")).status_code == 204
    response = await powercell.client.get(f"/agent/v1/tasks/{task['id']}")
    assert response.status_code == 409 and "test-private-jit-config" not in response.text


# Assertions: every malformed signature header, including non-ASCII wire bytes,
# is an authentication failure instead of an uncaught server exception.
@pytest.mark.parametrize("signature", [b"\xff", b"sha256=\xff", b"sha256=invalid"])
async def test_non_ascii_webhook_signatures_are_unauthorized(client, github, signature):
    """Verify non ascii webhook signatures are unauthorized."""
    response = await client.post(
        "/v1/github/webhook", content=b"{}", headers=[(b"x-hub-signature-256", signature)]
    )
    assert response.status_code == 401


# Assertions: allowed consecutive dots within a repository name are passed to
# the fixed GitHub origin; only traversal path segments are rejected.
async def test_github_repository_name_with_dots_is_not_path_traversal(api, ci_peer, github):
    """Verify github repository name with dots is not path traversal."""
    ci_peer.full_name = "test-org/foo..bar"
    created = await api.post("/v1/github/routes", json=policy())
    assert created.status_code == 201 and created.json()["repository"] == ci_peer.full_name

    def endpoint(request):
        assert str(request.url) == "https://api.github.com/repos/test-org/foo..bar/actions/runners"
        return httpx.Response(200, json={"runners": [], "total_count": 0})

    github.service.client._transport = httpx.MockTransport(endpoint)
    assert (await github.service.client.api("GET", "/repos/test-org/foo..bar/actions/runners"))[
        "runners"
    ] == []


# Assertions: GitHub cancellation/provider error consumes the session-bound
# state, keeps an existing connection, exchanges no code and reflects no provider text.
@pytest.mark.parametrize(
    "error,result",
    [("access_denied", "cancelled"), ("unknown-provider-error", "authorization_failed")],
)
async def test_oauth_error_returns_to_actions_without_changing_identity(
    api, ci_peer, github, error, result
):
    """Verify oauth error returns to actions without changing identity."""
    state = await begin(api)
    before = (await api.get("/v1/github/status")).json()
    exchanges = len(
        [
            request
            for request in github.peer.calls
            if request.url.path == "/login/oauth/access_token"
        ]
    )
    response = await api.get(
        "/v1/github/oauth/callback",
        params={
            "state": state,
            "error": error,
            "error_description": "private-provider-description",
        },
    )
    assert response.status_code == 303 and response.headers["location"].endswith(
        "/actions?github=" + result
    )
    assert "private-provider" not in response.text + response.headers["location"]
    assert (await api.get("/v1/github/status")).json() == before
    assert (
        len(
            [
                request
                for request in github.peer.calls
                if request.url.path == "/login/oauth/access_token"
            ]
        )
        == exchanges
    )
    assert (
        await api.get("/v1/github/oauth/callback", params={"state": state, "error": error})
    ).status_code == 400


# Assertions: when completion webhooks are lost, polling preserves GitHub's
# actual conclusion instead of treating every completed workflow as cancelled.
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
async def test_polling_recovers_the_actual_workflow_conclusion(
    client, review_route, ci_peer, powercell, app, clock, conclusion
):
    """Verify polling recovers the actual workflow conclusion."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    ci_peer.run_status, ci_peer.conclusion = "completed", conclusion
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("cancelled" if conclusion == "cancelled" else "completed")
        assert row.reason == "github_" + conclusion


# Assertions: both deleted and temporarily unreadable historical attempts still
# delete the runner and destroy the VM; 404 settles as unknown, 503 retries outcome.
@pytest.mark.parametrize("status", [404, 503])
async def test_outcome_lookup_failure_cannot_block_cleanup(
    client, review_route, ci_peer, powercell, app, clock, status
):
    """Verify outcome lookup failure cannot block cleanup."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    ci_peer.failures[("GET", "/repos/test-org/repo/actions/runs/1234/attempts/1")] = status
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    assert ci_peer.deletes == 1 and not ci_peer.registered
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    ).status_code == 200
    clock.now += timedelta(seconds=65)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        if status == 404:
            assert row.cleaned_at and row.reason == "github_outcome_unavailable"
        else:
            assert row.cleaned_at is None and row.reason == "runner_exited"
    if status == 503:
        ci_peer.failures.clear()
        ci_peer.run_status = "completed"
        clock.now += timedelta(seconds=30)
        await ci_controller.run_once(app)
        # Settling the result starts the final crash-recovery grace period.
        clock.now += timedelta(seconds=65)
        await ci_controller.run_once(app)
        async with app.state.db.read() as session:
            row = await session.get(CIReservation, result["id"])
            assert row.cleaned_at and row.reason == "github_success"


# Assertions: ephemeral auto-deregistration is not revoked authorization. Before
# selection it fails; afterward it keeps a provisional result for late events.
@pytest.mark.parametrize("phase", ["ready", "selected", "running"])
async def test_runner_detail_404_is_not_an_authorization_revocation(
    client, review_route, ci_peer, github, powercell, app, clock, phase
):
    """Verify runner detail 404 is not an authorization revocation."""
    result, _ = await ready_runner(
        client, review_route, ci_peer, powercell, app, clock, select_runner=phase != "ready"
    )
    if phase == "running":
        ci_peer.busy = True
        clock.now += timedelta(seconds=5)
        await ci_controller.run_once(app)
    ci_peer.registered = False
    ci_peer.failures[("GET", "/repos/test-org/repo/actions/runners/55")] = 404
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("failed" if phase == "ready" else "completed")
        assert row.reason == ("runner_unavailable" if phase == "ready" else "runner_exited")
    if phase != "ready":
        assert (
            await deliver(
                client,
                github,
                {
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
                },
            )
        ).status_code == 202
        async with app.state.db.read() as session:
            assert (await session.get(CIReservation, result["id"])).reason == "github_success"


# Assertions: two bindings in one workflow merge without router-key collisions;
# each target consumes only its own route's output, and the example warns about gates.
async def test_multiple_workflow_bindings_have_independent_routers(api, ci_peer):
    """Verify multiple workflow bindings have independent routers."""
    jobs = {}
    for job_key in ["test", "lint"]:
        route = (await api.post("/v1/github/routes", json=policy(job_key=job_key))).json()
        source = (await api.get(f"/v1/github/routes/{route['id']}/workflow")).json()["yaml"]
        assert "upstream needs" in source and "approval/concurrency" in source
        snippet = yaml.safe_load(source)["jobs"]
        assert not jobs.keys() & snippet.keys()
        router = snippet[job_key]["needs"]
        selection = next(
            row["runs_on"]
            for row in snippet[job_key]["strategy"]["matrix"]["include"]
            if row["role"] == "workload"
        )
        assert "needs." + router + ".outputs.run_attempt == github.run_attempt" in selection
        assert "needs." + router + ".outputs.runs_on" in selection
        assert snippet[router]["steps"][0]["env"]["POWERCELL_ROUTE"] == route["id"]
        jobs.update(snippet)
    assert len(jobs) == 4


# Assertions: unlink/relink during GitHub verification invalidates pending creation;
# it leaves no enabled route, artifact metadata or orphaned artifact file.
@pytest.mark.parametrize("reconnect", [False, True])
async def test_disconnect_invalidates_pending_route_creation(api, ci_peer, app, reconnect):
    """Verify disconnect invalidates pending route creation."""
    entered, release = asyncio.Event(), asyncio.Event()

    class Barrier:
        async def wait(self):
            entered.set()
            await release.wait()

    ci_peer.barrier = Barrier()
    async with app.state.db.read() as session:
        artifacts_before = await session.scalar(select(func.count()).select_from(Artifact))
    files_before = set(app.state.settings.storage_path.iterdir())
    creating = asyncio.create_task(api.post("/v1/github/routes", json=policy()))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert (await api.delete("/v1/github/connection")).status_code == 204
        if reconnect:
            await connect(api)
    finally:
        release.set()
    response = await creating
    assert response.status_code == 409
    assert (await api.get("/v1/github/routes")).json() == []
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CIRoute)) == 0
        assert await session.scalar(select(func.count()).select_from(Artifact)) == artifacts_before
    assert set(app.state.settings.storage_path.iterdir()) == files_before


# Assertions: disconnect or a newer OAuth flow wins over an in-flight older
# exchange; the consumed public state cannot be replayed while the HTTP call waits.
@pytest.mark.parametrize("invalidate", ["disconnect", "new_flow", "expire"])
async def test_oauth_exchange_cannot_restore_an_invalidated_connection(
    api, github, clock, invalidate
):
    """Verify oauth exchange cannot restore an invalidated connection."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()

    github.peer.callback = wait
    state = await begin(api)
    exchanging = asyncio.create_task(
        api.get("/v1/github/oauth/callback", params={"state": state, "code": "valid-code"})
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        replay = await api.get(
            "/v1/github/oauth/callback", params={"state": state, "code": "replay"}
        )
        assert replay.status_code == 400
        if invalidate == "disconnect":
            assert (await api.delete("/v1/github/connection")).status_code == 204
        elif invalidate == "new_flow":
            await begin(api)
        else:
            clock.now += timedelta(minutes=11)
    finally:
        release.set()
    assert (await exchanging).status_code == 409
    assert (await api.get("/v1/github/status")).json()["account"] is None


# Assertions: a callback lacking both code and provider error consumes its state
# and returns a clear 400 without changing the current connection.
async def test_oauth_callback_requires_a_code_or_error(api, github):
    """Verify oauth callback requires a code or error."""
    state = await begin(api)
    assert (await api.get("/v1/github/oauth/callback", params={"state": state})).status_code == 400
    assert (
        await api.get("/v1/github/oauth/callback", params={"state": state, "code": "replay"})
    ).status_code == 400


# Assertions: a newer rerun cannot overwrite the old attempt's result or cancel
# its platform reservation as a foreign workflow identity when completion was lost.
async def test_polling_targets_the_reservations_historical_attempt(
    client, review_route, ci_peer, github, powercell, app, clock
):
    """Verify polling targets the reservations historical attempt."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    response = await github.service.client.api(
        "GET", "/repos/test-org/repo/actions/runs/1234/attempts/1", token="test"
    )
    ci_peer.history[1] = {**response, "status": "completed", "conclusion": "failure"}
    ci_peer.run_attempt = 2
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "completed" and row.reason == "github_failure"
        assert row.run_attempt == 1


# Assertions: a signed completion received during an outcome HTTP request wins
# over that stale response, including a concurrent 404; cancellation stays terminal.
@pytest.mark.parametrize("response_status", [200, 404])
async def test_late_completion_wins_over_inflight_outcome_lookup(
    client, review_route, ci_peer, github, powercell, app, clock, response_status
):
    """Verify late completion wins over inflight outcome lookup."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    original = github.service.client._transport

    async def handler(request):
        if "/attempts/" in request.url.path:
            await deliver(
                client,
                github,
                {
                    "action": "completed",
                    "installation": {"id": 456},
                    "repository": {"id": 42},
                    "workflow_job": {
                        "run_id": 1234,
                        "run_attempt": 1,
                        "runner_id": 55,
                        "runner_name": result["runs_on"][0],
                        "conclusion": "cancelled",
                    },
                },
            )
            if response_status == 404:
                return httpx.Response(404)
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    ci_peer.run_status = "completed"
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "cancelled" and row.reason == "github_cancelled"
    assert not ci_peer.registered


# Assertions: Actions owns its VM and commands exclusively. An execution-only
# organization member cannot enqueue commands, snapshot, renew, destroy or cancel
# them before JIT registration or after selection; ordinary metadata stays readable.
@pytest.mark.parametrize("phase", ["before_registration", "selected"])
async def test_ci_isolates_reject_manual_mutations(
    client, api, other_api, other_user, ci_peer, powercell, app, clock, phase
):
    """Verify ci isolates reject manual mutations."""
    company = (await api.post("/v1/companies", json={"name": "Actions managed"})).json()
    assert (
        await api.post(
            "/v1/memberships",
            json={
                "company_id": company["id"],
                "user_id": other_user["id"],
                "role": "user",
                "permissions": ["execution.run", "execution.read"],
            },
        )
    ).status_code == 201
    response = await api.post(
        "/v1/github/routes", json=policy(company_id=company["id"], preparation_seconds=90)
    )
    assert response.status_code == 201
    route = response.json()
    # A public ownership transfer rotates the device's credential and session.
    computer = (await api.get(f"/v1/computers/{powercell.computer_id}")).json()
    ownership = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == powercell.computer_id
    )
    response = await api.patch(
        f"/v1/computer-ownerships/{ownership['id']}", json={"company_id": company["id"]}
    )
    assert response.status_code == 200
    credential = await api.post(f"/v1/computers/{powercell.computer_id}/credentials")
    assert credential.status_code == 201
    powercell.client.headers["Authorization"] = "Bearer " + credential.json()["token"]
    previous = (await powercell.client.get("/agent/v1/session")).json()
    response = await powercell.client.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": "org-session"},
        json={"clean_start": True, "previous_session_id": previous["session_id"]},
    )
    assert response.status_code == 200
    powercell.client.headers["X-Agent-Session"] = response.json()["session_id"]
    assert (
        await powercell.client.post(
            "/agent/v1/capacity",
            headers={"Idempotency-Key": "org-capacity"},
            json=computer["capacity_report"],
        )
    ).status_code == 200
    if phase == "before_registration":
        await provision_only(client, route, ci_peer, powercell, app, clock)
    else:
        await ready_runner(client, route, ci_peer, powercell, app, clock)
    async with app.state.db.read() as session:
        row = await session.scalar(
            select(CIReservation).where(CIReservation.route_id == route["id"])
        )
        isolate_id, command_id = row.isolate_id, row.command_id
        isolate = await session.get(Isolate, isolate_id)
        sequence, expires = isolate.next_sequence, isolate.expires_at
        count = await session.scalar(select(func.count()).select_from(Command))
    for actor in [api, other_api]:
        assert (await actor.get(f"/v1/isolates/{isolate_id}")).status_code == 200
        for method, suffix, body in [
            ("POST", "/commands", {"command": "true"}),
            ("POST", "/snapshots", None),
            ("POST", "/renew", None),
            ("DELETE", "", None),
        ]:
            response = await actor.request(
                method,
                f"/v1/isolates/{isolate_id}" + suffix,
                json=body,
                headers={"Idempotency-Key": phase + suffix},
            )
            assert response.status_code == 409, response.text
        if command_id:
            assert (await actor.post(f"/v1/commands/{command_id}/cancel")).status_code == 409
            assert (
                await actor.post("/v1/commands/cancel", json={"ids": [command_id]})
            ).status_code == 409
    async with app.state.db.read() as session:
        isolate = await session.get(Isolate, isolate_id)
        assert (
            isolate.status == "ready"
            and isolate.next_sequence == sequence
            and isolate.expires_at == expires
        )
        assert await session.scalar(select(func.count()).select_from(Command)) == count


# Assertions: refreshing an expiring grant before configuration succeeds normally;
# disconnect immediately after refresh is still detected before storing a route.
@pytest.mark.parametrize("disconnect", [False, True])
async def test_route_grant_snapshot_handles_refresh_and_immediate_disconnect(
    api, ci_peer, app, user, clock, monkeypatch, disconnect
):
    """Verify route grant snapshot handles refresh and immediate disconnect."""
    async with app.state.db.write() as session:
        (await session.get(GitHubAccount, user["id"])).expires_at = clock.now
    if disconnect:
        original = ci.user_token

        async def refreshing(*args):
            token = await original(*args)
            assert (await api.delete("/v1/github/connection")).status_code == 204
            return token

        monkeypatch.setattr(ci, "user_token", refreshing)
    response = await api.post("/v1/github/routes", json=policy())
    assert response.status_code == (409 if disconnect else 201)


# Assertions: temporary runner-detail outages keep selected jobs alive, and a
# signed completion delivered during runner404 reconciliation cannot be overwritten.
@pytest.mark.parametrize("complete", [False, True])
async def test_runner_detail_error_respects_existing_selection_and_completion(
    client, review_route, ci_peer, github, powercell, app, clock, complete
):
    """Verify runner detail error respects existing selection and completion."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    original = github.service.client._transport

    async def handler(request):
        if request.url.path.endswith("/actions/runners/55") and request.method == "GET":
            if complete:
                await deliver(
                    client,
                    github,
                    {
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
                    },
                )
            return httpx.Response(404 if complete else 503)
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("completed" if complete else "selected")
        assert row.reason == ("github_success" if complete else None)


# Assertions: GitHub's reused router output is bound to its original attempt.
# A partial rerun fails before allocation instead of reusing an obsolete runner.
async def test_generated_selection_never_reuses_a_previous_attempt(api, ci_peer):
    """Verify generated selection never reuses a previous attempt."""
    route = (await api.post("/v1/github/routes", json=policy())).json()
    source = (await api.get(f"/v1/github/routes/{route['id']}/workflow")).json()["yaml"]
    jobs = yaml.safe_load(source)["jobs"]
    router = jobs["test"]["needs"]
    assert jobs[router]["outputs"]["run_attempt"] == "${{ github.run_attempt }}"
    # Model only the documented GitHub &&/||/fromJSON boundary; the expression
    # and stale-attempt error come from the delivered YAML, not the renderer.
    assert jobs["test"]["runs-on"] == "${{ fromJSON(matrix.runs_on) }}"
    expression = next(
        row["runs_on"]
        for row in jobs["test"]["strategy"]["matrix"]["include"]
        if row["role"] == "workload"
    )
    parsed = re.fullmatch(
        r"\$\{\{ needs\.([A-Za-z0-9_]+)\.outputs\.run_attempt == github\.run_attempt "
        r"&& needs\.\1\.outputs\.runs_on \|\| '([^']*)' \}\}",
        expression,
    )
    assert parsed and parsed[1] == router
    stored = {"run_attempt": "1", "runs_on": '["powercell-old-attempt"]'}

    def evaluate(current_attempt):
        return json.loads(
            (stored["run_attempt"] == current_attempt and stored["runs_on"]) or parsed[2]
        )

    assert evaluate("1") == ["powercell-old-attempt"]
    assert "Re-run the routing job or all jobs" in parsed[2]
    with pytest.raises(json.JSONDecodeError):
        evaluate("2")


# Assertions: long-running reservations share the installation's polling budget
# across routes, retain crash fencing, and stay below 2700 steady HTTP requests/hour.
@pytest.mark.parametrize("count", [1, 3, 100])
async def test_running_poll_interval_scales_with_installation_load(api, ci_peer, app, clock, count):
    """Verify running poll interval scales with installation load."""
    review_route = (await api.post("/v1/github/routes", json=policy(max_parallel=100))).json()
    other_route = (
        await api.post("/v1/github/routes", json=policy(job_key="lint", max_parallel=100))
    ).json()
    async with app.state.db.write() as session:
        for index in range(count):
            session.add(
                CIReservation(
                    route_id=(review_route if index % 2 == 0 else other_route)["id"],
                    run_id=1234,
                    run_attempt=1,
                    instance=str(index),
                    label="powercell-budget-" + str(index),
                    status="running",
                    deadline=clock.now,
                    expires_at=clock.now + timedelta(hours=1),
                    next_check=clock.now,
                )
            )
    work, _ = await ci_controller.claim_work(app)
    interval = (work.next_check - clock.now).total_seconds()
    assert interval == max(30, 12 * count)
    assert 9 * count * 3600 / interval <= 2700
    assert work.lease_nonce and work.lease_until == clock.now + timedelta(seconds=60)
    # Completion must override a long previously planned poll, without removing
    # the in-flight worker's nonce: first cleanup is immediately eligible.
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, work.id)
        nonce = row.lease_nonce
        ci_controller.close_github(row, "success", clock.now)
        assert row.next_check == clock.now and row.lease_nonce == nonce


# Assertions: binding validation matches the sole supported runtime: RAM >=16MiB
# and disk <=1TiB. Impossible configurations fail before artifacts or VMs are created.
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"memory_bytes": 16 * 1024**2 - 1}, 422),
        ({"memory_bytes": 16 * 1024**2}, 201),
        ({"disk_bytes": 1024**4 + 1}, 422),
        ({"disk_bytes": 1024**4}, 201),
    ],
)
async def test_binding_resource_limits_match_the_power_cell_runtime(api, ci_peer, changes, status):
    """Verify binding resource limits match the power cell runtime."""
    response = await api.post("/v1/github/routes", json=policy(**changes))
    assert response.status_code == status
    assert len((await api.get("/v1/github/routes")).json()) == int(status == 201)


# Assertions: offline-Mac cleanup gets one immediate deletion and an early
# post-lease recheck, then shares the installation's steady polling budget while
# retaining its local resource reservation until the agent confirms destruction.
@pytest.mark.parametrize("count", [3, 10])
async def test_terminal_offline_cleanup_uses_the_shared_poll_budget(
    client, review_route, ci_peer, github, powercell, app, clock, count
):
    """Verify terminal offline cleanup uses the shared poll budget."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    async with app.state.db.write() as session:
        for index in range(count - 1):
            session.add(
                CIReservation(
                    route_id=review_route["id"],
                    run_id=9000 + index,
                    run_attempt=1,
                    instance="default",
                    label="powercell-offline-peer-" + str(index),
                    status="completed",
                    reason="github_success",
                    finished_at=clock.now,
                    deadline=clock.now,
                    expires_at=clock.now,
                    next_check=clock.now + timedelta(hours=1),
                )
            )
    assert (
        await deliver(
            client,
            github,
            {
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
            },
        )
    ).status_code == 202
    await ci_controller.run_once(app)
    assert ci_peer.deletes == 1
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert (await session.get(Isolate, row.isolate_id)).reserved
        assert row.cleaned_at is None and row.next_check == clock.now + timedelta(
            seconds=36 if count == 3 else 60
        )
    clock.now += timedelta(seconds=60)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.cleaned_at is None and (await session.get(Isolate, row.isolate_id)).reserved
        assert row.next_check == clock.now + timedelta(seconds=12 * count)
    assert ci_peer.deletes == 1


# Assertions: matching signed start events cannot renew an expired selection,
# expired preparation, or unavailable VM; timely ready/selected starts still work.
@pytest.mark.parametrize(
    "boundary",
    [
        "selected_expired",
        "ready_deadline",
        "isolate_expired",
        "isolate_failed",
        "ready_live",
        "selected_live",
    ],
)
async def test_start_webhook_cannot_revive_an_expired_or_unavailable_runner(
    client, review_route, ci_peer, github, powercell, app, clock, boundary
):
    """Verify start webhook cannot revive an expired or unavailable runner."""
    ready = boundary.startswith("ready")
    result, _ = await ready_runner(
        client, review_route, ci_peer, powercell, app, clock, select_runner=not ready
    )
    if boundary == "selected_expired":
        clock.now += timedelta(seconds=301)
    elif boundary == "ready_deadline":
        clock.now += timedelta(seconds=91)
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        if boundary == "isolate_expired":
            isolate.expires_at = clock.now - timedelta(seconds=1)
        if boundary == "isolate_failed":
            isolate.status = "failed"
        before = row.expires_at, isolate.expires_at
    assert (
        await deliver(
            client,
            github,
            {
                "action": "in_progress",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_job": {
                    "run_id": 1234,
                    "run_attempt": 1,
                    "runner_id": 55,
                    "runner_name": result["runs_on"][0],
                },
            },
        )
    ).status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        if boundary.endswith("live"):
            assert (
                row.status == "running"
                and row.expires_at == isolate.expires_at == clock.now + timedelta(hours=1)
            )
        else:
            assert row.status == ("ready" if ready else "selected")
            assert (row.expires_at, isolate.expires_at) == before


# Assertions: a busy response delayed past the selection lease has the same
# deadline rules as webhooks and cannot extend resources after expiration.
async def test_stale_busy_poll_cannot_extend_an_expired_selection(
    client, review_route, ci_peer, powercell, app, clock
):
    """Verify stale busy poll cannot extend an expired selection."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)

    async def delayed():
        clock.now += timedelta(seconds=301)

    ci_peer.run_callback, ci_peer.busy = delayed, True
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "selected" and row.expires_at < clock.now
        assert (await session.get(Isolate, row.isolate_id)).expires_at < clock.now
    await ci_controller.local_tick(app)
    async with app.state.db.read() as session:
        assert (await session.get(CIReservation, result["id"])).status == "timed_out"


# Assertions: history links resolve the exact displayed attempt, so an earlier
# failure never opens Latest after a successful rerun of the same GitHub run ID.
async def test_history_links_point_to_the_displayed_attempt(api, review_route, app, clock):
    """Verify history links point to the displayed attempt."""
    async with app.state.db.write() as session:
        for attempt in (1, 2):
            session.add(
                CIReservation(
                    route_id=review_route["id"],
                    run_id=1234,
                    run_attempt=attempt,
                    instance="default",
                    label="powercell-history-" + str(attempt),
                    status="completed",
                    reason="github_failure" if attempt == 1 else "github_success",
                    deadline=clock.now,
                    expires_at=clock.now,
                    next_check=clock.now,
                    finished_at=clock.now,
                    cleaned_at=clock.now,
                )
            )
    response = await api.get(f"/v1/github/routes/{review_route['id']}/runs")
    assert response.status_code == 200
    urls = {row["run_attempt"]: row["url"] for row in response.json()}
    assert urls == {
        attempt: f"https://github.com/test-org/repo/actions/runs/1234/attempts/{attempt}"
        for attempt in (1, 2)
    }


# Assertions: reserve and its replay recheck the VM, runner command and
# assigned device session before publishing a label, even before a controller tick.
@pytest.mark.parametrize("phase", ["ready", "selected"])
@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "destroying",
        "destroyed",
        "expired",
        "command_completed",
        "session_expired",
        "session_changed",
    ],
)
async def test_reserve_refuses_a_locally_unavailable_runner(
    client, review_route, ci_peer, powercell, app, clock, phase, change
):
    """Refuse readiness publication for an unavailable local runner."""
    from throng_api.models import Computer

    result, task = await ready_runner(
        client, review_route, ci_peer, powercell, app, clock, select_runner=phase == "selected"
    )
    if change == "command_completed":
        assert (
            await powercell.client.post(
                f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
            )
        ).status_code == 200
    async with app.state.db.write() as session:
        row = await session.get(CIReservation, result["id"])
        isolate = await session.get(Isolate, row.isolate_id)
        computer = await session.get(Computer, isolate.computer_id)
        if change in {"failed", "destroying", "destroyed"}:
            isolate.status = change
        elif change == "expired":
            isolate.expires_at = clock.now - timedelta(seconds=1)
        elif change == "session_expired":
            computer.lease_expires_at = clock.now - timedelta(seconds=1)
        elif change == "session_changed":
            computer.session_id = None
        before = row.expires_at, isolate.expires_at
    response = await client.post(
        f"/v1/github/ci/routes/{review_route['id']}/reserve",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 409 and "runs_on" not in response.text
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == phase
        assert (row.expires_at, (await session.get(Isolate, row.isolate_id)).expires_at) == before


# Assertions: canonical repository ID resolves rename for active reconciliation
# and terminal cleanup, without following redirects or depending on user OAuth.
@pytest.mark.parametrize("phase", ["active", "disconnected_cleanup"])
async def test_repository_rename_preserves_reconciliation_and_cleanup(
    client, api, review_route, ci_peer, github, powercell, app, clock, phase
):
    """Verify repository rename preserves reconciliation and cleanup."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    ci_peer.full_name = "test-org/renamed"
    original, paths = github.service.client._transport, []
    if phase == "disconnected_cleanup":
        assert (await api.delete("/v1/github/connection")).status_code == 204

    async def handler(request):
        path = request.url.path
        paths.append(path)
        if phase == "disconnected_cleanup":
            assert request.headers.get("authorization") != "Bearer user-token"
        if path.startswith("/repos/test-org/repo/"):
            return httpx.Response(
                301,
                headers={
                    "Location": "https://api.github.com" + path.replace("/repo/", "/renamed/")
                },
            )
        if path.startswith("/repos/test-org/renamed/"):
            request = httpx.Request(
                request.method,
                request.url.copy_with(path=path.replace("/renamed/", "/repo/")),
                content=request.content,
                headers=request.headers,
            )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    if phase == "active":
        async with app.state.db.read() as session:
            assert (await session.get(CIReservation, result["id"])).status == "selected"
        assert "/repos/test-org/renamed/actions/runs/1234/attempts/1" in paths
        assert (
            await deliver(
                client,
                github,
                {
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
                },
            )
        ).status_code == 202
        await ci_controller.run_once(app)
    assert not ci_peer.registered and ci_peer.deletes == 1
    assert "/repositories/42" in paths
    assert not any(path.startswith("/repos/test-org/repo/") for path in paths)


# Assertions: cleanup refuses malformed or foreign repository metadata instead
# of issuing a credentialed runner request at an unverified canonical name.
@pytest.mark.parametrize(
    "metadata",
    [[], {"id": 99, "full_name": "test-org/other"}, {"id": 42, "full_name": "unsafe!name"}],
)
async def test_cleanup_validates_canonical_repository_metadata(
    client, review_route, ci_peer, github, powercell, app, clock, metadata
):
    """Verify cleanup validates canonical repository metadata."""
    _, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    original = github.service.client._transport

    async def handler(request):
        if request.url.path == "/repositories/42":
            return httpx.Response(200, json=metadata)
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    assert ci_peer.registered and ci_peer.deletes == 0


# Assertions: runner exit is provisional; later job/run webhooks or historical
# attempt polling may refine it without starting another job or reviving a cancellation.
@pytest.mark.parametrize("source", ["workflow_job", "workflow_run", "poll"])
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
async def test_late_github_outcome_refines_only_the_provisional_runner_exit(
    client, review_route, ci_peer, github, powercell, app, clock, source, conclusion
):
    """Verify late github outcome refines only the provisional runner exit."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    response = await powercell.client.post(
        f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 200
    clock.now += timedelta(seconds=5)
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.reason == "runner_exited" and row.runner_id == 55 and row.cleaned_at is None
    if source == "poll":
        ci_peer.run_status, ci_peer.conclusion = "completed", conclusion
        clock.now += timedelta(seconds=60)
        await ci_controller.run_once(app)
    else:
        item = {
            "id": 1234,
            "run_id": 1234,
            "run_attempt": 1,
            "runner_id": 55,
            "runner_name": result["runs_on"][0],
            "conclusion": conclusion,
        }
        response = await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                source: item,
            },
            event=source,
        )
        assert response.status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("cancelled" if conclusion == "cancelled" else "completed")
        assert row.reason == "github_" + conclusion


# Assertions: real API bindings accept bounded cold-start waits up to five minutes;
# generated router jobs outlive that wait and preserve small workload resources;
# values above the supported deadline are rejected before creating a binding.
@pytest.mark.parametrize("wait,minutes", [(90, 4), (180, 4), (181, 5), (240, 5), (300, 6)])
async def test_cold_runner_budget_is_supported_by_api_and_generated_router(
    api, ci_peer, wait, minutes
):
    """Verify cold runner budget is supported by api and generated router."""
    created = await api.post(
        "/v1/github/routes",
        json=policy(
            preparation_seconds=wait,
            cpu_millis=500,
            memory_bytes=512 * 1024**2,
            disk_bytes=4 * 1024**3,
        ),
    )
    assert created.status_code == 201
    route = created.json()
    assert route["cpu_millis"] == 500 and route["memory_bytes"] == 512 * 1024**2
    workflow = (await api.get(f"/v1/github/routes/{route['id']}/workflow")).json()["yaml"]
    jobs = yaml.safe_load(workflow)["jobs"]
    router = jobs["powercell_route_" + route["id"].replace("-", "")]
    assert router["timeout-minutes"] == minutes
    assert router["steps"][0]["env"]["POWERCELL_WAIT_SECONDS"] == str(wait)
    assert "identity_token(url)" in router["steps"][0]["run"]
    assert (
        await api.post("/v1/github/routes", json=policy(preparation_seconds=301))
    ).status_code == 422
