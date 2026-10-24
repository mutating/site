"""Recovery contracts over real HTTP, including ambiguous committed requests."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("reconciled", [False, True])
@pytest.mark.parametrize("status", ["obsolete", "failed", "delivered"])
async def test_receipt_exposes_original_task_state_without_reviving_session(
    app, api, agent_api, provisioning_isolate, clock, status, reconciled
):
    """Recovery must distinguish terminal controls from merely inactive ones.
    The ordinary task GET remains fenced after lease expiry; receipt provides
    the assigned task state without renewing execution rights or its lease.
    """
    from throng_api.controller import run_once
    from throng_api.models import AgentTask

    # Drive the real scheduler explicitly so expiry reconciliation cannot race
    # the receipt assertions. Provision and dispatch through the ordinary API.
    await run_once(app)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    create = response.json()["tasks"][0]
    assert create["kind"] == "create" and create["isolate_id"] == provisioning_isolate["id"]
    assert (
        await agent_api.post(f"/agent/v1/tasks/{create['id']}/complete", json={})
    ).status_code == 200
    response = await api.post(
        f"/v1/isolates/{provisioning_isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest -q"},
    )
    assert response.status_code == 202, response.text
    await run_once(app)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    run_task = response.json()["tasks"][0]
    assert run_task["kind"] == "run"
    async with app.state.db.write() as session:
        task = await session.get(AgentTask, run_task["id"])
        task.kind, task.status = "cancel", status
    clock.now += timedelta(seconds=120)
    if reconciled:
        await run_once(app)
    expected = "obsolete" if reconciled and status == "delivered" else status
    path = f"/agent/v1/tasks/{run_task['id']}"
    assert (await agent_api.get(path)).status_code == 409
    response = await agent_api.post(path + "/receipt", json={})
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": False, "active": False, "task_status": expected}
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
    async with app.state.db.read() as session:
        task = await session.get(AgentTask, run_task["id"])
        assert task.status == expected


async def test_completion_receipt_survives_expiry_and_rejects_changed_or_foreign_result(
    agent_api, run_task, clock, other_agent, client
):
    """A device can reconcile its exact result without reviving an expired session."""
    path = f"/agent/v1/tasks/{run_task['id']}"
    report = {"returncode": 0, "stdout": "done"}
    pending = await agent_api.post(path + "/receipt", json=report)
    assert pending.status_code == 200, pending.text
    assert pending.json() == {"accepted": False, "active": True, "task_status": run_task["status"]}
    assert (await agent_api.post(path + "/complete", json=report)).status_code == 200
    clock.now += timedelta(seconds=120)
    receipt = await agent_api.post(path + "/receipt", json=report)
    assert receipt.status_code == 200
    assert receipt.json() == {"accepted": True, "active": False, "task_status": "completed"}
    conflict = await agent_api.post(path + "/receipt", json={"returncode": 7})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "completion_conflict"
    foreign = await client.post(
        path + "/receipt",
        headers={**other_agent["headers"], "X-Agent-Session": run_task["session_id"]},
        json=report,
    )
    assert foreign.status_code == 404


async def test_receipt_requires_original_session_and_unknown_tasks_stay_private(
    agent_api, run_task
):
    """Receipt lookup is scoped to both the assigned device and original session."""
    response = await agent_api.post(
        f"/agent/v1/tasks/{run_task['id']}/receipt",
        headers={"X-Agent-Session": str(uuid4())},
        json={},
    )
    assert response.status_code == 404
    assert (await agent_api.post(f"/agent/v1/tasks/{uuid4()}/receipt", json={})).status_code == 404


async def test_expired_session_has_machine_code_without_changing_detail(agent_api, clock):
    """Clients distinguish session expiry from task and payload conflicts."""
    clock.now += timedelta(seconds=120)
    response = await agent_api.post("/agent/v1/heartbeat")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Agent session expired; a clean start is required",
        "code": "session_expired",
    }


async def test_request_logs_correlate_attempts_and_never_contain_credentials(agent_api, caplog):
    """Every HTTP attempt records a bounded ID, status and processing duration."""
    caplog.set_level("INFO", logger="throng.transport")
    request_id = str(uuid4())
    response = await agent_api.post("/agent/v1/heartbeat", headers={"X-Request-ID": request_id})
    assert response.headers["x-request-id"] == request_id
    rows = [json.loads(r.message) for r in caplog.records if r.name == "throng.transport"]
    row = next(row for row in rows if row.get("request_id") == request_id)
    assert row["event"] == "http_request"
    assert row["status"] == 200 and row["duration_ms"] >= 0 and row["db_wait_ms"] >= 0
    assert row["method"] == "POST" and row["path"] == "/agent/v1/heartbeat"
    assert agent_api.headers["authorization"] not in json.dumps(rows)
    response = await agent_api.get("/does-not-exist", headers={"X-Request-ID": "untrusted-value"})
    assert response.status_code == 404
    assert response.headers["x-request-id"] != "untrusted-value"


async def test_empty_claim_does_not_take_global_write_lock(agent_api, caplog):
    """Empty polling performs authentication and reads without serialized mutation commits."""
    caplog.set_level("INFO", logger="throng.transport")
    request_id = str(uuid4())
    response = await agent_api.post(
        "/agent/v1/tasks/claim",
        json={"wait_seconds": 0.02},
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 200 and response.json()["tasks"] == []
    row = next(json.loads(r.message) for r in caplog.records if request_id in r.message)
    assert row["db_writes"] == 0 and row["db_wait_ms"] == 0


@pytest.fixture
async def claim_policy_task(request, app, api, agent_api, provisioning_isolate):
    """Build real tasks through HTTP with explicit scheduling and no background tick."""
    from throng_api.controller import run_once

    kind = request.param
    await run_once(app)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    create = response.json()["tasks"][0]
    assert create["kind"] == "create" and create["isolate_id"] == provisioning_isolate["id"]
    if kind == "create":
        return create
    response = await agent_api.post(f"/agent/v1/tasks/{create['id']}/complete", json={})
    assert response.status_code == 200, response.text
    isolate_id = provisioning_isolate["id"]
    if kind == "snapshot":
        response = await api.post(
            f"/v1/isolates/{isolate_id}/snapshots", headers={"Idempotency-Key": str(uuid4())}
        )
    else:
        assert kind == "run"
        response = await api.post(
            f"/v1/isolates/{isolate_id}/commands",
            headers={"Idempotency-Key": str(uuid4())},
            json={"command": "pytest -q"},
        )
    assert response.status_code == 202, response.text
    await run_once(app)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    return next(task for task in response.json()["tasks"] if task["kind"] == kind)


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("claim_policy_task", ["create", "run", "snapshot"], indirect=True)
@pytest.mark.parametrize("disconnected", [False, True])
async def test_pending_but_paused_assignment_rechecks_eligibility_before_dispatch(
    app, agent_api, claim_policy_task, monkeypatch, caplog, disconnected
):
    """Known paused new work cannot repeatedly serialize an empty long poll."""
    from starlette.requests import Request

    from throng_api.models import Computer

    async with app.state.db.write() as session:
        computer = await session.get(Computer, claim_policy_task["computer_id"])
        computer.accepting_work = False

    async def is_disconnected(request):
        return disconnected

    monkeypatch.setattr(Request, "is_disconnected", is_disconnected)
    caplog.set_level("INFO", logger="throng.transport")
    request_id = str(uuid4())
    response = await agent_api.post(
        "/agent/v1/tasks/claim",
        json={"wait_seconds": 0.03},
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 200 and response.json()["tasks"] == []
    row = next(json.loads(r.message) for r in caplog.records if request_id in r.message)
    assert row["db_writes"] == 0 and row["db_wait_ms"] == 0


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("claim_policy_task", ["run"], indirect=True)
async def test_paused_claim_delivers_destroy_and_cancel_before_new_work(
    app, api, agent_api, claim_policy_task, caplog
):
    """A read-only new-work hint must preserve cleanup delivery and its priority."""
    from throng_api.models import Computer

    response = await api.post(f"/v1/commands/{claim_policy_task['command_id']}/cancel")
    assert response.status_code == 202, response.text
    response = await api.delete(f"/v1/isolates/{claim_policy_task['isolate_id']}")
    assert response.status_code == 202, response.text
    async with app.state.db.write() as session:
        computer = await session.get(Computer, claim_policy_task["computer_id"])
        computer.accepting_work = False
    caplog.set_level("INFO", logger="throng.transport")
    request_id = str(uuid4())
    response = await agent_api.post(
        "/agent/v1/tasks/claim",
        json={"wait_seconds": 0},
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 200, response.text
    assert [task["kind"] for task in response.json()["tasks"]] == ["destroy", "cancel"]
    row = next(json.loads(r.message) for r in caplog.records if request_id in r.message)
    assert row["db_writes"] == 1


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("claim_policy_task", ["create"], indirect=True)
async def test_claim_read_hint_observes_policy_resume_within_the_same_poll(
    app, agent_api, claim_policy_task, monkeypatch
):
    """Read hints are refreshed each iteration instead of caching the paused policy."""
    from starlette.requests import Request

    from throng_api.models import Computer
    from throng_api.transport import db_wait

    computer_id = claim_policy_task["computer_id"]
    async with app.state.db.write() as session:
        computer = await session.get(Computer, computer_id)
        computer.accepting_work = False
    writes_before_resume = []

    async def resume(request):
        if not writes_before_resume:
            writes_before_resume.append(db_wait.get()[1])
            async with app.state.db.write() as session:
                computer = await session.get(Computer, computer_id)
                computer.accepting_work = True
        return False

    monkeypatch.setattr(Request, "is_disconnected", resume)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 1})
    assert response.status_code == 200, response.text
    assert writes_before_resume == [0]
    assert [task["id"] for task in response.json()["tasks"]] == [claim_policy_task["id"]]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("claim_policy_task", ["create"], indirect=True)
async def test_claim_rechecks_policy_after_read_hint_before_dispatch(
    app, agent_api, claim_policy_task, monkeypatch
):
    """A positive read hint cannot grant delivery rights after policy changes."""
    from contextlib import asynccontextmanager

    from throng_api.models import AgentTask, Computer

    original_write = app.state.db.write
    paused = False

    @asynccontextmanager
    async def pause_before_write():
        nonlocal paused
        if not paused:
            paused = True
            async with original_write() as session:
                computer = await session.get(Computer, claim_policy_task["computer_id"])
                computer.accepting_work = False
        async with original_write() as session:
            yield session

    monkeypatch.setattr(app.state.db, "write", pause_before_write)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200 and response.json()["tasks"] == []
    assert paused
    async with app.state.db.read() as session:
        task = await session.get(AgentTask, claim_policy_task["id"])
        assert task.status == claim_policy_task["status"]
        assert (
            task.delivered_at.isoformat().replace("+00:00", "Z")
            == claim_policy_task["delivered_at"]
        )


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_claim_read_hint_observes_work_queued_after_an_empty_read(
    app, agent_api, provisioning_isolate, monkeypatch
):
    """A later scheduler transaction can make work visible within the same long poll."""
    from starlette.requests import Request

    from throng_api.controller import run_once
    from throng_api.transport import db_wait

    writes_before_queue = []

    async def queue(request):
        if not writes_before_queue:
            writes_before_queue.append(db_wait.get()[1])
            await run_once(app)
        return False

    monkeypatch.setattr(Request, "is_disconnected", queue)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 1})
    assert response.status_code == 200, response.text
    assert writes_before_queue == [0]
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["kind"] == "create"
    assert tasks[0]["isolate_id"] == provisioning_isolate["id"]


async def test_withheld_delivery_payload_is_never_dispatched(agent_api, create_task, monkeypatch):
    """A permission/credential change during payload construction cannot leak a partial task."""
    from throng_api import agents

    async def withhold(*args):
        return None

    monkeypatch.setattr(agents, "delivery_payload", withhold)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200 and response.json()["tasks"] == []


async def test_structured_network_diagnostic_round_trips_without_losing_error_context(
    agent_api, api, agent, clock
):
    """Uploaded native failure telemetry preserves timestamps, correlation and error codes."""
    identifier = str(uuid4())
    network = {
        "created": clock.now.isoformat(),
        "operation_id": str(uuid4()),
        "request_id": str(uuid4()),
        "method": "POST",
        "path": "/agent/v1/heartbeat",
        "transport": "relay",
        "attempt": 1,
        "duration_ms": 1234.5,
        "error_domain": "NSURLErrorDomain",
        "error_code": -1005,
    }
    record = {
        "id": identifier,
        "created": clock.now.isoformat(),
        "message": "Network request failed",
        "network": network,
    }
    response = await agent_api.put("/agent/v1/diagnostics/" + identifier, json=record)
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/computers/{agent['computer']['id']}/diagnostics/{identifier}")
    assert response.status_code == 200 and response.json()["network"] == network


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("claim_policy_task", ["create"], indirect=True)
@pytest.mark.parametrize("disconnect", [True, False])
async def test_claim_waits_or_disconnects_when_policy_changes_after_a_positive_hint(
    app, agent_api, claim_policy_task, monkeypatch, disconnect
):
    """A positive hint can become empty under the dispatch lock; long polling must then honor disconnect or wait for resumed policy."""
    from contextlib import asynccontextmanager

    from starlette.requests import Request

    from throng_api.models import AgentTask, Computer
    from throng_api.transport import db_wait

    original = app.state.db.write
    paused = False
    observations = []

    @asynccontextmanager
    async def pause_before_dispatch():
        """Pause only after the read hint so the final dispatch query, rather than the hint, filters the task."""
        nonlocal paused
        if not paused:
            paused = True
            async with original() as session:
                computer = await session.get(Computer, claim_policy_task["computer_id"])
                computer.accepting_work = False
        async with original() as session:
            yield session

    async def observe_after_dispatch(request):
        """Resume the computer after observing the empty dispatch, or model the client's disconnect."""
        observations.append(db_wait.get()[1])
        if not disconnect:
            async with original() as session:
                computer = await session.get(Computer, claim_policy_task["computer_id"])
                computer.accepting_work = True
        return disconnect

    monkeypatch.setattr(app.state.db, "write", pause_before_dispatch)
    monkeypatch.setattr(Request, "is_disconnected", observe_after_dispatch)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    assert observations == [2]  # Policy update plus the dispatch transaction.
    assert [task["id"] for task in response.json()["tasks"]] == (
        [] if disconnect else [claim_policy_task["id"]]
    )
    async with app.state.db.read() as session:
        task = await session.get(AgentTask, claim_policy_task["id"])
        assert task.status == (claim_policy_task["status"] if disconnect else "dispatched")
