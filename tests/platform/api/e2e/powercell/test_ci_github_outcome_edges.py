"""Recover exact GitHub job outcomes when completion webhook delivery is lost."""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from throng_api import ci_controller
from throng_api.models import CIReservation, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci_review import review_route as review_route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
def outcome_peer(github, ci_peer):
    """Expose GitHub's paginated attempt jobs before the older broad run fixture."""
    original = github.service.client._transport
    peer = SimpleNamespace(jobs=[], calls=[], error=None, on_lookup=None)

    async def handler(request):
        path = request.url.path
        if path.startswith("/repos/test-org/repo/actions/runs/1234/") and path.endswith("/jobs"):
            assert "/attempts/" in path, "Latest-run jobs must not replace a historical attempt"
            attempt = int(path.split("/")[-2])
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params.get("per_page", "30"))
            assert 1 <= per_page <= 100 and page >= 1
            peer.calls.append((attempt, page, per_page))
            if peer.on_lookup:
                callback, peer.on_lookup = peer.on_lookup, None
                await callback()
            if peer.error:
                return httpx.Response(peer.error, json={"message": "temporary-provider-error"})
            start = (page - 1) * per_page
            return httpx.Response(
                200,
                json={
                    "total_count": len(peer.jobs),
                    "jobs": peer.jobs[start : start + per_page],
                },
            )
        return await original.handle_async_request(request)

    github.service.client._transport = httpx.MockTransport(handler)
    return peer


def exact_job(label, conclusion="success", **changes):
    """Describe a completed job without depending on its editable display name."""
    return {
        "id": 700,
        "run_id": 1234,
        "run_attempt": 1,
        "runner_id": 55,
        "runner_name": label,
        "name": "An arbitrary job display name (arm64)",
        "status": "completed",
        "conclusion": conclusion,
        **changes,
    }


async def prepare_outcome(client, github, ci_peer, powercell, task, source, conclusion):
    """Deliver only aggregate completion or runner exit, never exact job events."""
    if source != "active_poll":
        response = await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
        assert response.status_code == 200
    if source == "workflow_run":
        response = await deliver(
            client,
            github,
            {
                "action": "completed",
                "installation": {"id": 456},
                "repository": {"id": 42},
                "workflow_run": {
                    "id": 1234,
                    "run_attempt": 1,
                    "conclusion": conclusion,
                },
            },
            event="workflow_run",
        )
        assert response.status_code == 202
    ci_peer.run_status = "in_progress" if source == "runner_exited" else "completed"
    ci_peer.conclusion = conclusion


async def reconcile_due(app, clock, reservation_id, count=3):
    """Advance bounded reconciliation without coupling assertions to tick intervals."""
    for _ in range(count):
        async with app.state.db.write() as session:
            row = await session.get(CIReservation, reservation_id)
            row.next_check = clock.now
        await ci_controller.run_once(app)


# Assertions: losing exact completion delivery cannot replace this runner's job
# result with an unrelated workflow aggregate, even while other jobs still run.
# Polling and terminal cleanup converge to the same exact outcome without reruns.
@pytest.mark.parametrize("source", ["active_poll", "workflow_run", "runner_exited"])
@pytest.mark.parametrize(
    "job_conclusion,aggregate",
    [
        ("success", "failure"),
        ("failure", "success"),
        ("success", "cancelled"),
        ("cancelled", "failure"),
    ],
)
async def test_missing_job_webhook_recovers_exact_rest_outcome(
    client,
    review_route,
    ci_peer,
    github,
    outcome_peer,
    powercell,
    app,
    clock,
    source,
    job_conclusion,
    aggregate,
):
    """Recover the actual selected job from GitHub rather than its workflow result."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    outcome_peer.jobs = [exact_job(result["runs_on"][0], job_conclusion)]
    await prepare_outcome(client, github, ci_peer, powercell, task, source, aggregate)
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"])
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert (row.status, row.reason) == (
            "cancelled" if job_conclusion == "cancelled" else "completed",
            "github_" + job_conclusion,
        )
        assert row.credentials is None and row.finished_at is not None
    assert outcome_peer.calls and {attempt for attempt, _, _ in outcome_peer.calls} == {1}
    assert ci_peer.jit_count == 1


# Assertions: historical attempt jobs are paginated, with both runner identity
# fields required; same display name or only one matching identity cannot win.
# A later workflow attempt must not contaminate the original reservation.
async def test_exact_outcome_uses_paginated_attempt_and_full_runner_identity(
    client, review_route, ci_peer, github, outcome_peer, powercell, app, clock
):
    """Find a historical job beyond two pages without trusting display names."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    label = result["runs_on"][0]
    outcome_peer.jobs = [
        exact_job(label, "failure", id=1, runner_id=99, name="test"),
        exact_job("another-runner", "failure", id=2, name="test"),
        *[
            exact_job(
                "unrelated",
                "failure",
                id=index + 3,
                runner_id=1000 + index,
                name="test",
            )
            for index in range(198)
        ],
        exact_job(label, "success"),
    ]
    await prepare_outcome(client, github, ci_peer, powercell, task, "runner_exited", "failure")
    ci_peer.history[1] = {
        "id": 1234,
        "repository": {"id": 42},
        "head_repository": {"id": 42},
        "head_branch": "main",
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "failure",
    }
    ci_peer.run_attempt = 2
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"])
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.reason == "github_success" and row.run_attempt == 1
    assert outcome_peer.calls and {attempt for attempt, _, _ in outcome_peer.calls} == {1}
    assert max(page for _, page, _ in outcome_peer.calls) >= 3
    assert ci_peer.jit_count == 1


# Assertions: a transient job-result error does not retain the runner, VM or JIT
# credentials. Cleanup remains pending for outcome recovery, then converges once
# GitHub responds; no extra runner registration or workload execution is created.
@pytest.mark.parametrize("error", [403, 429, 503])
async def test_outcome_api_failure_cleans_resources_then_retries_exact_result(
    client, review_route, ci_peer, github, outcome_peer, powercell, app, clock, error
):
    """Separate mandatory cleanup from temporarily unavailable exact job results."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    outcome_peer.jobs = [exact_job(result["runs_on"][0])]
    outcome_peer.error = error
    await prepare_outcome(client, github, ci_peer, powercell, task, "workflow_run", "failure")
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"])
    assert outcome_peer.calls and not ci_peer.registered and ci_peer.deletes == 1
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    ).status_code == 200
    clock.now += timedelta(seconds=65)
    await reconcile_due(app, clock, result["id"], count=1)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.cleaned_at is None and row.credentials is None
        assert not (await session.get(Isolate, row.isolate_id)).reserved
    outcome_peer.error = None
    await reconcile_due(app, clock, result["id"])
    clock.now += timedelta(seconds=65)
    await reconcile_due(app, clock, result["id"])
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.reason == "github_success" and row.cleaned_at
        assert row.credentials is None and not (await session.get(Isolate, row.isolate_id)).reserved
    assert not ci_peer.registered and ci_peer.jit_count == 1


# Assertions: exact REST completion arriving after a concurrent platform terminal
# transition cannot overwrite cancellation, timeout or revoked authorization, and
# cannot restore reusable credentials or cause a replacement runner to be created.
@pytest.mark.parametrize("source", ["active_poll", "runner_exited"])
@pytest.mark.parametrize(
    "status,reason",
    [
        ("failed", "bootstrap_cancelled"),
        ("timed_out", "runner_timeout"),
        ("cancelled", "authorization_revoked"),
    ],
)
async def test_late_rest_outcome_preserves_concurrent_platform_terminal_state(
    client,
    review_route,
    ci_peer,
    github,
    outcome_peer,
    powercell,
    app,
    clock,
    source,
    status,
    reason,
):
    """Recheck local terminal precedence after awaiting the GitHub jobs API."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    outcome_peer.jobs = [exact_job(result["runs_on"][0])]
    await prepare_outcome(client, github, ci_peer, powercell, task, source, "failure")

    async def cancel_during_lookup():
        async with app.state.db.write() as session:
            row = await session.get(CIReservation, result["id"])
            ci_controller.close(row, status, reason, clock.now)

    outcome_peer.on_lookup = cancel_during_lookup
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"])
    assert outcome_peer.calls and outcome_peer.on_lookup is None
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert (row.status, row.reason) == (status, reason)
        assert row.credentials is None and row.finished_at is not None
    assert ci_peer.jit_count == 1
