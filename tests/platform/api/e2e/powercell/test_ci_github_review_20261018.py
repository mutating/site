"""GitHub integration regressions reproduced by the October 18 review."""

from datetime import timedelta

import pytest

from throng_api import ci_controller
from throng_api.models import CIReservation, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci_github_outcome_edges import exact_job
from .test_ci_github_outcome_edges import outcome_peer as outcome_peer
from .test_ci_review import review_route as review_route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: history records the selected job's exact signed outcome rather than
# the aggregate result of unrelated jobs. Both delivery orders converge without
# starting another runner or reviving the completed reservation. This remains
# true if polling supplies the aggregate or cleanup finishes before the job event.
@pytest.mark.parametrize(
    "job_conclusion,run_conclusion",
    [
        ("success", "failure"),
        ("failure", "success"),
        ("success", "cancelled"),
        ("cancelled", "failure"),
    ],
)
@pytest.mark.parametrize("job_first", [False, True])
@pytest.mark.parametrize("aggregate_source", ["workflow_run", "poll"])
@pytest.mark.parametrize("cleanup_between", [False, True])
async def test_exact_job_completion_wins_over_workflow_aggregate(
    client,
    review_route,
    ci_peer,
    github,
    powercell,
    app,
    clock,
    outcome_peer,
    job_conclusion,
    run_conclusion,
    job_first,
    aggregate_source,
    cleanup_between,
):
    """Keep job history independent of workflow completion delivery order."""
    result, task = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    events = [
        ("workflow_run", {"id": 1234, "run_attempt": 1, "conclusion": run_conclusion}),
        (
            "workflow_job",
            {
                "run_id": 1234,
                "run_attempt": 1,
                "runner_id": 55,
                "runner_name": result["runs_on"][0],
                "conclusion": job_conclusion,
            },
        ),
    ]
    if job_first:
        events.reverse()
    for index, (event, item) in enumerate(events):
        if event == "workflow_run" and aggregate_source == "poll":
            ci_peer.run_status, ci_peer.conclusion = "completed", run_conclusion
            clock.now += timedelta(seconds=5)
            await ci_controller.run_once(app)
        else:
            if event == "workflow_job":
                # The REST peer must reflect the exact job whose completion it reports.
                outcome_peer.jobs = [exact_job(result["runs_on"][0], job_conclusion)]
            response = await deliver(
                client,
                github,
                {
                    "action": "completed",
                    "installation": {"id": 456},
                    "repository": {"id": 42},
                    event: item,
                },
                event=event,
            )
            assert response.status_code == 202
        if cleanup_between and index == 0:
            await ci_controller.run_once(app)
            tasks = (
                await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
            ).json()["tasks"]
            assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
            assert (
                await powercell.client.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
            ).status_code == 200
            clock.now += timedelta(seconds=65)
            await ci_controller.run_once(app)
            async with app.state.db.read() as session:
                row = await session.get(CIReservation, result["id"])
                assert row.cleaned_at and not (await session.get(Isolate, row.isolate_id)).reserved
            assert not ci_peer.registered
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == ("cancelled" if job_conclusion == "cancelled" else "completed")
        assert row.reason == "github_" + job_conclusion
        assert row.finished_at is not None
    assert ci_peer.jit_count == 1


# Assertions: a late exact GitHub result never overrides a platform cancellation,
# timeout, authorization revocation or lost isolate; no terminal resource revives.
@pytest.mark.parametrize(
    "status,reason",
    [
        ("cancelled", "bootstrap_cancelled"),
        ("timed_out", "runner_timeout"),
        ("cancelled", "authorization_revoked"),
        ("failed", "isolate_unavailable"),
    ],
)
async def test_late_job_completion_preserves_platform_terminal_state(
    client, review_route, ci_peer, github, powercell, app, clock, status, reason
):
    """Preserve platform-enforced terminal outcomes despite late signed job events."""
    result, _ = await ready_runner(client, review_route, ci_peer, powercell, app, clock)
    async with app.state.db.write() as session:
        ci_controller.close(
            await session.get(CIReservation, result["id"]), status, reason, clock.now
        )
    response = await deliver(
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
    assert response.status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert (row.status, row.reason) == (status, reason)
    assert ci_peer.jit_count == 1
