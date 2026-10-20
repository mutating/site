"""A verified runner start remains durable evidence after completion or provider outages."""

from datetime import UTC, datetime, timedelta

import anyio
import pytest
from alembic import command
from sqlalchemy import inspect, select

from throng_api import ci_controller
from throng_api.cli import migration_config
from throng_api.models import CIReservation, Isolate

from .test_ci import ci_peer as ci_peer
from .test_ci import deliver, ready_runner
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import route as route
from .test_ci_github_outcome_edges import exact_job
from .test_ci_github_outcome_edges import outcome_peer as outcome_peer

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


async def start_job(client, github, ci_peer, app, clock, result, source):
    """Confirm actual dispatch through a signed webhook or the real busy-runner poll."""
    if source == "poll":
        ci_peer.busy = True
        clock.now += timedelta(seconds=5)
        await ci_controller.run_once(app)
    else:
        response = await deliver(
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
        assert response.status_code == 202
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "running"
        return clock.now


async def observed_watch(client, route, ci_peer):
    """Ask the signed public guard endpoint without refreshing or recreating a reservation."""
    response = await client.post(
        f"/v1/github/ci/routes/{route['id']}/watch",
        headers={"Authorization": "Bearer " + ci_peer.token()},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("source", ["webhook", "poll"])
@pytest.mark.parametrize("outcome_error", [404, 503])
async def test_watch_remembers_dispatch_after_exit_outage_cleanup_and_expiry(
    client, api, route, ci_peer, github, outcome_peer, powercell, app, clock, source, outcome_error
):
    """A fast completed runner cannot erase proof that the queue already dispatched its job.

    Exercise real enrollment, command completion, VM cleanup and reopened SQL
    connections. A missing or temporarily unavailable GitHub result must not make
    a later matrix guard cancel the dispatched workload, even after lease expiry.
    """
    result, task = await ready_runner(client, route, ci_peer, powercell, app, clock)
    started_at = await start_job(client, github, ci_peer, app, clock, result, source)
    response = await powercell.client.post(
        f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 200
    await ci_controller.local_tick(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.status == "completed" and row.reason == "runner_exited"
    # This is the original bug: persisted start disappears behind provisional exit.
    assert await observed_watch(client, route, ci_peer) == {"status": "started"}
    outcome_peer.error = outcome_error
    await ci_controller.run_once(app)
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
        assert row.started_at == started_at and row.credentials is None
        assert row.reason == (
            "github_outcome_unavailable" if outcome_error == 404 else "runner_exited"
        )
        assert not (await session.get(Isolate, row.isolate_id)).reserved
        expiry = row.expires_at
    await app.state.db.close()
    clock.now = expiry + timedelta(seconds=1)
    assert await observed_watch(client, route, ci_peer) == {"status": "started"}
    outcome_peer.error = None
    outcome_peer.jobs = [exact_job(result["runs_on"][0], "success")]
    ci_peer.run_status, ci_peer.conclusion = "completed", "success"
    await ci_controller.run_once(app)
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.started_at == started_at
        assert row.cleaned_at and row.credentials is None
    assert await observed_watch(client, route, ci_peer) == {"status": "started"}
    history_response = await api.get(f"/v1/github/routes/{route['id']}/runs")
    assert history_response.status_code == 200
    assert "started_at" not in history_response.json()[0]
    assert history_response.json()[0]["reason"] == "github_success"


async def test_unconfirmed_runner_exit_does_not_create_start_evidence(
    client, route, ci_peer, powercell, app, clock
):
    """A process can exit before receiving a job; return code zero alone is never dispatch proof."""
    result, task = await ready_runner(client, route, ci_peer, powercell, app, clock)
    assert (
        await powercell.client.post(
            f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
        )
    ).status_code == 200
    await ci_controller.local_tick(app)
    assert await observed_watch(client, route, ci_peer) == {"status": "waiting"}
    async with app.state.db.read() as session:
        row = await session.get(CIReservation, result["id"])
        assert row.started_at is None
        expiry = row.expires_at
    clock.now = expiry
    assert await observed_watch(client, route, ci_peer) == {
        "status": "failed",
        "reason": "runner_timeout",
    }


async def test_mark_running_preserves_the_first_verified_timestamp(
    client, route, ci_peer, powercell, app, clock
):
    """Repeated valid start observations cannot rewrite the original dispatch timestamp."""
    from throng_api.models import CIRoute

    result, _ = await ready_runner(client, route, ci_peer, powercell, app, clock)
    first = clock.now
    for _ in range(2):
        async with app.state.db.write() as session:
            row = await session.get(CIReservation, result["id"])
            binding = await session.get(CIRoute, row.route_id)
            await ci_controller.mark_running(session, row, binding, clock.now)
            assert row.status == "running" and row.started_at == first
        clock.now += timedelta(seconds=1)


async def history(session):
    """Read every historical field independently of the newly added nullable column."""
    columns = [column for column in CIReservation.__table__.columns if column.key != "started_at"]
    return [
        tuple(row)
        for row in (await session.execute(select(*columns).order_by(CIReservation.id))).all()
    ]


async def start_column(app):
    """Inspect the actual database schema instead of assuming model and migration agreement."""
    async with app.state.db.engine.connect() as connection:
        return await connection.run_sync(
            lambda c: {column["name"] for column in inspect(c).get_columns("ci_reservations")}
        )


async def test_start_migration_backfills_only_running_and_preserves_unknown_history(
    route, ci_peer, app, clock
):
    """Schema upgrades preserve history and never invent starts for ambiguous completed runners."""
    assert "started_at" in await start_column(app)
    states = [
        ("running", None),
        ("selected", None),
        ("preparing", None),
        ("completed", "runner_exited"),
        ("completed", "github_success"),
        ("cancelled", "bootstrap_cancelled"),
        ("failed", "no_capacity"),
    ]
    async with app.state.db.write() as session:
        for index, (status, reason) in enumerate(states):
            session.add(
                CIReservation(
                    route_id=route["id"],
                    run_id=5000 + index,
                    run_attempt=1,
                    instance="default",
                    label="powercell-history-start-" + str(index),
                    status=status,
                    reason=reason,
                    created_at=clock.now - timedelta(days=1),
                    credentials="opaque-historical-state" if index == 0 else None,
                    deadline=clock.now + timedelta(seconds=90),
                    expires_at=clock.now + timedelta(hours=1),
                    next_check=clock.now,
                )
            )
    async with app.state.db.read() as session:
        before = await history(session)
    configuration = migration_config(app.state.settings)
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.downgrade, configuration, "0012")
    assert "started_at" not in await start_column(app)
    async with app.state.db.read() as session:
        assert await history(session) == before
    earliest = datetime.now(UTC) - timedelta(seconds=1)
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.upgrade, configuration, "head")
    latest = datetime.now(UTC) + timedelta(seconds=1)
    async with app.state.db.read() as session:
        assert await history(session) == before
        rows = (await session.scalars(select(CIReservation).order_by(CIReservation.run_id))).all()
        assert rows[0].started_at is not None
        assert earliest <= rows[0].started_at <= latest
        assert rows[0].started_at.tzinfo is UTC
        assert all(row.started_at is None for row in rows[1:])
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.downgrade, configuration, "0012")
    assert "started_at" not in await start_column(app)
    async with app.state.db.read() as session:
        assert await history(session) == before
    await app.state.db.close()
    await anyio.to_thread.run_sync(command.upgrade, configuration, "head")
