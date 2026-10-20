"""A temporarily stale successful GitHub jobs response cannot freeze the workflow aggregate as a job result."""

from datetime import timedelta

import pytest

from throng_api import ci_controller
from throng_api.models import CIReservation, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import ready_runner
from .test_ci_github_outcome_edges import exact_job, prepare_outcome, reconcile_due
from .test_ci_github_outcome_edges import outcome_peer as outcome_peer
from .test_ci_review import review_route as review_route
from .test_ci_started import start_job

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.mark.parametrize("source", ["runner_exited", "workflow_run"])
@pytest.mark.parametrize("visibility", ["missing", "queued"])
async def test_delayed_exact_job_result_cannot_be_replaced_permanently_by_workflow_aggregate(
    client,
    api,
    review_route,
    ci_peer,
    github,
    outcome_peer,
    powercell,
    app,
    clock,
    source,
    visibility,
):
    """Recover success for this runner even when a sibling makes the workflow fail.

    The signed start, native command completion, workflow notification, database
    and public history HTTP endpoint are real. Only GitHub is an external peer:
    its attempt/jobs endpoint temporarily returns HTTP 200 with missing or stale
    queued job data. The exact successful job appears after the cleanup grace;
    its completion webhook is deliberately lost. Physical cleanup must proceed,
    while this binding's history must eventually reflect its own job outcome.
    """
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    await start_job(client, github, ci_peer, app, clock, result, "webhook")
    outcome_peer.jobs = (
        [] if visibility == "missing" else [exact_job(result["runs_on"][0], None, status="queued")]
    )
    await prepare_outcome(client, github, ci_peer, powercell, task, source, "failure")
    ci_peer.run_status, ci_peer.conclusion = "completed", "failure"
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"], count=1)
    assert not ci_peer.registered and ci_peer.deletes == 1
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    ).status_code == 200
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.credentials is None and not (await session.get(Isolate, row.isolate_id)).reserved
        assert row.cleaned_at is None
    clock.now += timedelta(seconds=65)
    await reconcile_due(app, clock, result["id"], count=1)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        observed = {
            "initial_reason": row.reason,
            "cleaned_before_exact_result": row.cleaned_at is not None,
        }
        assert row.credentials is None and not (await session.get(Isolate, row.isolate_id)).reserved
    before_calls = len(outcome_peer.calls)
    outcome_peer.jobs = [exact_job(result["runs_on"][0], "success")]
    clock.now += timedelta(seconds=60)
    await reconcile_due(app, clock, result["id"], count=2)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        observed.update(
            final_reason=row.reason,
            lookups_after_result_became_visible=len(outcome_peer.calls) - before_calls,
        )
        final_reason = row.reason
    response = await api.get(f"/v1/github/routes/{review_route['id']}/runs")
    assert response.status_code == 200
    history = next(row for row in response.json() if row["id"] == result["id"])
    observed["public_history_reason"] = history["reason"]
    assert final_reason == history["reason"] == "github_success", observed


@pytest.mark.parametrize(
    "source,not_found_before_grace",
    [("runner_exited", False), ("workflow_run", False), ("runner_exited", True)],
)
async def test_missing_exact_result_then_404_finishes_cleanup_without_retrying_forever(
    client,
    api,
    review_route,
    ci_peer,
    github,
    outcome_peer,
    powercell,
    app,
    clock,
    source,
    not_found_before_grace,
):
    """Treat a later 404 as a finite unavailable result after stale successful responses.

    No job record in HTTP 200 keeps result recovery pending, while the native VM
    and registration are immediately released. An explicit 404 must still end
    that retry after the cleanup grace, even with persisted proof of dispatch.
    """
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    await start_job(client, github, ci_peer, app, clock, result, "webhook")
    await prepare_outcome(client, github, ci_peer, powercell, task, source, "failure")
    ci_peer.run_status, ci_peer.conclusion = "completed", "failure"
    clock.now += timedelta(seconds=5)
    await reconcile_due(app, clock, result["id"], count=1)
    assert not ci_peer.registered and ci_peer.deletes == 1
    tasks = (await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
    assert (
        await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    ).status_code == 200
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.credentials is None and row.cleaned_at is None
        assert not (await session.get(Isolate, row.isolate_id)).reserved
    outcome_peer.error = 404
    clock.now += timedelta(seconds=5 if not_found_before_grace else 65)
    await reconcile_due(app, clock, result["id"], count=1)
    expected = "github_outcome_unavailable" if source == "runner_exited" else "github_failure"
    if not_found_before_grace:
        async with app.state.db.read() as session:
            row = await session.get(CIReservation, result["id"])
            assert row.reason == expected and row.cleaned_at is None
        outcome_peer.error = None  # Recovery to HTTP 200 with no job is not a new result.
        clock.now += timedelta(seconds=65)
        await reconcile_due(app, clock, result["id"], count=1)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.reason == expected and row.cleaned_at is not None
        cleaned_at = row.cleaned_at
    lookup_count = len(outcome_peer.calls)
    outcome_peer.error, outcome_peer.jobs = None, [exact_job(result["runs_on"][0], "success")]
    clock.now += timedelta(hours=1)
    await ci_controller.run_once(app)
    assert len(outcome_peer.calls) == lookup_count
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.cleaned_at == cleaned_at and row.reason == expected
        assert row.credentials is None
    response = await api.get(f"/v1/github/routes/{review_route['id']}/runs")
    assert response.status_code == 200
    assert next(row for row in response.json() if row["id"] == result["id"])["reason"] == expected
    assert ci_peer.jit_count == 1
