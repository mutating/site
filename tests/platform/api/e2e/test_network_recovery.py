"""Recovery contracts over real HTTP, including ambiguous committed requests."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_completion_receipt_survives_expiry_and_rejects_changed_or_foreign_result(
    agent_api, run_task, clock, other_agent, client
):
    """A device can reconcile its exact result without reviving an expired session."""
    path = f"/agent/v1/tasks/{run_task['id']}"
    report = {"returncode": 0, "stdout": "done"}
    pending = await agent_api.post(path + "/receipt", json=report)
    assert pending.status_code == 200, pending.text
    assert pending.json() == {"accepted": False, "active": True}
    assert (await agent_api.post(path + "/complete", json=report)).status_code == 200
    clock.now += timedelta(seconds=120)
    receipt = await agent_api.post(path + "/receipt", json=report)
    assert receipt.status_code == 200
    assert receipt.json() == {"accepted": True, "active": False}
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


@pytest.mark.parametrize("disconnected", [False, True])
async def test_pending_but_paused_assignment_rechecks_eligibility_before_dispatch(
    app, agent_api, create_task, monkeypatch, disconnected
):
    """The read fast path never authorizes a task excluded by the locked eligibility check."""
    from starlette.requests import Request

    from throng_api.models import Computer

    async with app.state.db.write() as session:
        computer = await session.get(Computer, create_task["computer_id"])
        computer.accepting_work = False

    async def is_disconnected(request):
        return disconnected

    monkeypatch.setattr(Request, "is_disconnected", is_disconnected)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.03})
    assert response.status_code == 200 and response.json()["tasks"] == []


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
