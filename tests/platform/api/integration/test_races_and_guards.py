"""Races and defensive transitions using real persisted resources and narrowly controlled scheduling."""

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import StatementError

from throng_api.execution import check_new_work, fail_isolate, finish, release_reservation
from throng_api.models import (
    AgentTask,
    Command,
    CommandResult,
    ComputerResourceChange,
    Isolate,
    User,
)

pytestmark = pytest.mark.anyio


async def test_unknown_task_completion_cannot_destroy_an_isolate_or_finish_a_command(
    app, api, agent_api, run_task, command, isolate, unknown_task_kind
):
    """An unsupported stored task kind returns 409 without reporting success, deleting a sandbox or releasing its reserved capacity."""
    before = (await api.get(f"/api/v1/isolates/{isolate['id']}")).json()
    async with app.state.db.read() as session:
        count = await session.scalar(select(func.count()).select_from(ComputerResourceChange))
    response = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/complete", json={})
    assert response.status_code == 409 and response.json() == {"detail": "Unknown task kind"}
    assert (await api.get(f"/api/v1/isolates/{isolate['id']}")).json() == before
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == "dispatched" and result["result"] is None
    async with app.state.db.read() as session:
        task = await session.get(AgentTask, run_task["id"])
        assert (
            task.status == "dispatched"
            and task.finished_at is None
            and task.completion_hash is None
        )
        assert (
            await session.scalar(select(func.count()).select_from(ComputerResourceChange)) == count
        )


async def test_company_deleted_between_access_check_and_read_returns_hidden_404(
    api, company, paused_company_read
):
    """Concurrent company deletion after authorization returns the same 404 as an unknown resource instead of a server error."""
    async with asyncio.TaskGroup() as group:
        read = group.create_task(api.get(f"/api/v1/companies/{company['id']}"))
        await asyncio.wait_for(paused_company_read["entered"].wait(), timeout=3)
        assert (await api.delete(f"/api/v1/companies/{company['id']}")).status_code == 204
        paused_company_read["resume"].set()
    assert read.result().status_code == 404
    assert read.result().json() == {"detail": "Resource not found"}


async def test_concurrent_token_ownership_deletion_fails_authentication(
    api, client, access_token, token_ownership, paused_token_owner_lookup
):
    """An authentication already reading a token still rejects it if its ownership is deleted before owner lookup."""
    async with asyncio.TaskGroup() as group:
        read = group.create_task(
            client.get(
                "/api/v1/users/me", headers={"Authorization": "Bearer " + access_token["token"]}
            )
        )
        await asyncio.wait_for(paused_token_owner_lookup["entered"].wait(), timeout=3)
        assert (
            await api.delete(f"/api/v1/token-ownerships/{token_ownership['id']}")
        ).status_code == 204
        paused_token_owner_lookup["resume"].set()
    assert read.result().status_code == 401 and read.result().json() == {
        "detail": "Invalid token owner"
    }


async def test_concurrent_token_issuer_deletion_fails_authentication(
    api, client, user, access_token, paused_token_issuer_lookup
):
    """An authentication holding an old token and ownership cannot proceed after the issuing user has been deleted."""
    async with asyncio.TaskGroup() as group:
        read = group.create_task(
            client.get(
                "/api/v1/users/me", headers={"Authorization": "Bearer " + access_token["token"]}
            )
        )
        await asyncio.wait_for(paused_token_issuer_lookup["entered"].wait(), timeout=3)
        assert (await api.delete(f"/api/v1/users/{user['id']}")).status_code == 204
        paused_token_issuer_lookup["resume"].set()
    assert read.result().status_code == 401 and read.result().json() == {
        "detail": "Invalid token issuer"
    }


@pytest.mark.parametrize("membership", ["creator"], indirect=True)
async def test_concurrent_issuer_demotion_rechecks_membership_after_token_read(
    api, client, user, company, membership, company_token, paused_corporate_issuer_lookup
):
    """A corporate token read before revocation cannot finish authentication after its issuer loses company administration."""
    rows = (await api.get("/api/v1/memberships", params={"company_id": company["id"]})).json()
    issuer = next(row for row in rows if row["user_id"] == user["id"])
    async with asyncio.TaskGroup() as group:
        read = group.create_task(
            client.get(
                f"/api/v1/companies/{company['id']}",
                headers={"Authorization": "Bearer " + company_token["token"]},
            )
        )
        await asyncio.wait_for(paused_corporate_issuer_lookup["entered"].wait(), timeout=3)
        response = await api.patch(f"/api/v1/memberships/{issuer['id']}", json={"role": "user"})
        assert response.status_code == 200, response.text
        paused_corporate_issuer_lookup["resume"].set()
    assert read.result().status_code == 401
    assert read.result().json() == {"detail": "Token issuer no longer administers this company"}


async def test_internal_terminal_retries_preserve_results_and_do_not_release_capacity_twice(
    app, api, finished_execution, clock
):
    """Internal transition retries leave a completed result and destroyed isolate unchanged and add no duplicate resource releases."""
    command_id = finished_execution["command"]["id"]
    isolate_id = finished_execution["isolate"]["id"]
    before_result = (await api.get(f"/api/v1/commands/{command_id}/result")).json()
    before_isolate = (await api.get(f"/api/v1/isolates/{isolate_id}")).json()
    async with app.state.db.read() as session:
        count = await session.scalar(select(func.count()).select_from(ComputerResourceChange))
    async with app.state.db.write() as session:
        command = await session.get(Command, command_id)
        isolate = await session.get(Isolate, isolate_id)
        await finish(
            session, command, "failed", clock.now + timedelta(seconds=1), error="late failure"
        )
        await fail_isolate(session, isolate, clock.now, "late failure")
        await release_reservation(session, isolate, clock.now)
    assert (await api.get(f"/api/v1/commands/{command_id}/result")).json() == before_result
    assert (await api.get(f"/api/v1/isolates/{isolate_id}")).json() == before_isolate
    async with app.state.db.read() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ComputerResourceChange)) == count
        )
        assert await session.scalar(select(func.count()).select_from(CommandResult)) == 1


async def test_result_reconciliation_preserves_already_terminal_task(
    app, api, command, run_task, terminal_run_assignment, clock
):
    """Finishing a command whose task already failed preserves that task's original status, error and finish time."""
    async with app.state.db.write() as session:
        row = await session.get(Command, command["id"])
        await finish(
            session, row, "failed", clock.now + timedelta(seconds=1), error="reconciled result"
        )
    async with app.state.db.read() as session:
        task = await session.get(AgentTask, run_task["id"])
        assert (
            task.status == "failed"
            and task.error == "original worker failure"
            and task.finished_at == clock.now
        )
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == "failed" and result["result"]["error"] == "reconciled result"


async def test_missing_run_assignment_still_requires_confirmed_cancellation(
    api, agent_api, command, isolate, missing_run_assignment
):
    """A lost run assignment does not fabricate successful cancellation: the command remains pending until the worker confirms stopping it."""
    response = await api.post(f"/api/v1/commands/{command['id']}/cancel")
    assert response.status_code == 202 and response.json()["status"] == "cancel_requested"
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    cancel = response.json()["tasks"][0]
    assert cancel["kind"] == "cancel" and cancel["command_id"] == command["id"]
    assert cancel["payload"]["run_task_id"] is None
    assert (
        await agent_api.post(f"/agent/v1/tasks/{cancel['id']}/complete", json={})
    ).status_code == 200
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()[
        "status"
    ] == "cancelled"
    assert (await api.get(f"/api/v1/isolates/{isolate['id']}")).json()["reserved"] is True


@pytest.mark.parametrize(
    "settings", [{"agent_lease_seconds": 3600, "task_ack_timeout_seconds": 5}], indirect=True
)
async def test_completion_after_deadline_is_rejected_even_before_controller_tick(
    api, agent_api, run_task, command, clock, paused_controller
):
    """The completion handler independently enforces the task deadline when the controller has not yet marked the task obsolete."""
    clock.now += timedelta(seconds=5)
    response = await agent_api.post(
        f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 409 and response.json() == {
        "detail": "Task deadline has expired"
    }
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()["result"] is None


async def test_creation_guard_rejects_provisioning_deadline_with_live_isolate(
    app, create_task, paused_provisioning_controller
):
    """The shared execution guard independently rejects creation at its provisioning deadline even while the isolate TTL is still valid."""
    async with app.state.db.read() as session:
        isolate = await session.get(Isolate, create_task["isolate_id"])
        assert isolate.expires_at > isolate.provisioning_deadline
        with pytest.raises(HTTPException) as error:
            check_new_work(isolate, "create", isolate.provisioning_deadline)
        assert (
            error.value.status_code == 409
            and error.value.detail == "Isolate provisioning deadline has expired"
        )


async def test_database_rejects_timezone_naive_values_without_losing_existing_data(
    app, user, clock
):
    """The database type rejects naive timestamps even outside HTTP validation, rolling back the attempted write."""
    with pytest.raises(StatementError, match="Timezone-aware datetime required"):
        async with app.state.db.write() as session:
            row = await session.get(User, user["id"])
            row.created_at = clock.now.replace(tzinfo=None)
            await session.flush()
    async with app.state.db.read() as session:
        row = await session.get(User, user["id"])
        assert row.created_at.isoformat().replace("+00:00", "Z") == user["created_at"]
