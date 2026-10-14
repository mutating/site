"""Command and isolate lifecycles with HTTP agents and the embedded scheduler."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_command_round_trip_preserves_payload_and_serializes_execution(
    api, agent_api, isolate
):
    """Execute two commands in order, preserving exact arguments, output and nonzero exit codes."""
    payload = {
        "command": "  pytest -q  ",
        "shell": True,
        "cwd": "tests",
        "env": {"VALUE": "  spaced  "},
        "timeout_seconds": 47,
    }
    commands = []
    for index in range(2):
        response = await api.post(
            f"/api/v1/isolates/{isolate['id']}/commands",
            headers={"Idempotency-Key": f"command-{index}"},
            json=payload,
        )
        assert response.status_code == 202
        commands.append(response.json())
    assert [row["sequence"] for row in commands] == [1, 2]
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["command_id"] == commands[0]["id"]
    task = tasks[0]
    assert task["payload"] == payload
    response = await api.post(
        "/api/v1/commands/statuses", json={"ids": [row["id"] for row in commands]}
    )
    assert response.status_code == 200
    assert [row["status"] for row in response.json()] == ["dispatched", "queued"]
    response = await agent_api.post(f"/agent/v1/tasks/{task['id']}/ack")
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ] == []
    assert (await agent_api.post(f"/agent/v1/tasks/{task['id']}/started")).status_code == 200
    response = await agent_api.post(
        f"/agent/v1/tasks/{task['id']}/complete",
        json={"returncode": 1, "stdout": " 1 failed\n", "stderr": ""},
    )
    assert response.status_code == 200
    response = await api.get(f"/api/v1/commands/{commands[0]['id']}/result")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed" and result["result"] is not None
    assert result["result"]["returncode"] == 1 and not result["result"]["success"]
    assert result["result"]["stdout"] == " 1 failed\n" and result["result"]["stderr"] == ""
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    task = response.json()["tasks"][0]
    assert task["command_id"] == commands[1]["id"]
    assert (
        await agent_api.post(f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0})
    ).status_code == 200
    response = await api.post(
        "/api/v1/commands/results",
        json={"ids": [commands[1]["id"], commands[0]["id"], commands[1]["id"]]},
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [
        commands[1]["id"],
        commands[0]["id"],
        commands[1]["id"],
    ]
    assert response.json()[0]["result"]["success"]


async def test_cancellation_before_delivery_never_launches_work(api, agent_api, command):
    """An undelivered command is cancelled atomically without issuing a run or stop task."""
    response = await api.post(
        "/api/v1/commands/cancel", json={"ids": [command["id"], command["id"]]}
    )
    assert response.status_code == 202
    assert len(response.json()) == 1 and response.json()[0]["status"] == "cancelled"
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})).json()[
        "tasks"
    ] == []
    response = await api.get(f"/api/v1/commands/{command['id']}/result")
    assert response.json()["result"]["returncode"] is None
    assert not response.json()["result"]["success"]
    assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).json()[
        "status"
    ] == "cancelled"


@pytest.mark.parametrize("started", [False, True])
async def test_external_cancel_waits_for_stop_confirmation(
    api, agent_api, command, run_task, started
):
    """Delivered commands stay cancel_requested until the worker confirms process termination."""
    if started:
        assert (
            await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/started")
        ).status_code == 200
    for _ in range(2):
        response = await api.post(f"/api/v1/commands/{command['id']}/cancel")
        assert response.status_code == 202 and response.json()["status"] == "cancel_requested"
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()["result"] is None
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    cancels = [task for task in tasks if task["kind"] == "cancel"]
    assert len(cancels) == 1 and cancels[0]["payload"]["run_task_id"] == run_task["id"]
    path = f"/agent/v1/tasks/{cancels[0]['id']}/complete"
    payload = {"returncode": -15, "stdout": "partial output"}
    first = await agent_api.post(path, json=payload)
    repeated = await agent_api.post(path, json=payload)
    assert first.status_code == repeated.status_code == 200 and first.json() == repeated.json()
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == "cancelled" and result["result"]["stdout"] == "partial output"


@pytest.mark.parametrize("winner", ["run", "cancel"])
async def test_completion_cancel_race_has_one_immutable_result(
    api, agent_api, command, run_task, winner
):
    """The first valid terminal report wins and delayed reports cannot overwrite its result."""
    assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    cancel = next(task for task in tasks if task["kind"] == "cancel")
    first, last = (run_task, cancel) if winner == "run" else (cancel, run_task)
    assert (
        await agent_api.post(
            f"/agent/v1/tasks/{first['id']}/complete", json={"returncode": 0, "stdout": winner}
        )
    ).status_code == 200
    assert (
        await agent_api.post(
            f"/agent/v1/tasks/{last['id']}/complete", json={"returncode": 0, "stdout": "late"}
        )
    ).status_code == 409
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == ("completed" if winner == "run" else "cancelled")
    assert result["result"]["stdout"] == winner


@pytest.mark.parametrize("settings", [{"task_ack_timeout_seconds": 3}], indirect=True)
@pytest.mark.parametrize("late_start", [False, True])
async def test_cancellation_replaces_launch_deadline(
    api, agent_api, clock, command, run_task, late_start
):
    """Cancellation grants the full stop interval even when the launch deadline is about to expire."""
    assert (await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/ack")).status_code == 200
    clock.now += timedelta(seconds=2)
    assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    cancel = next(task for task in tasks if task["kind"] == "cancel")
    if late_start:
        response = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/started")
        assert response.status_code == 200 and response.json()["deadline"] == cancel["deadline"]
    clock.now += timedelta(seconds=2)
    response = await agent_api.get(f"/agent/v1/tasks/{run_task['id']}")
    assert response.json()["deadline"] == cancel["deadline"]
    assert (await api.get(f"/api/v1/commands/{command['id']}")).json()[
        "status"
    ] == "cancel_requested"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{cancel['id']}/complete", json={})
    ).status_code == 200
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()[
        "status"
    ] == "cancelled"


@pytest.mark.parametrize("command", [{"timeout_seconds": 5}], indirect=True)
async def test_controller_timeout_requires_confirmed_stop(api, agent_api, clock, running_command):
    """The controller requests cancellation on timeout and records timed_out only after confirmation."""
    command = running_command["command"]
    clock.now += timedelta(seconds=6)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    cancel = response.json()["tasks"][0]
    assert cancel["kind"] == "cancel" and cancel["payload"]["reason"] == "timeout"
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()[
        "status"
    ] == "cancel_requested"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{cancel['id']}/complete", json={})
    ).status_code == 200
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()[
        "status"
    ] == "timed_out"


async def test_agent_can_report_local_timeout_without_server_cancellation(
    api, agent_api, running_command
):
    """A local worker timeout preserves logs and returncode even before the controller requests a stop."""
    task, command = running_command["task"], running_command["command"]
    response = await agent_api.post(
        f"/agent/v1/tasks/{task['id']}/complete",
        json={"outcome": "timed_out", "returncode": -9, "stdout": "partial"},
    )
    assert response.status_code == 200
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == "timed_out" and result["result"]["returncode"] == -9
    assert result["result"]["stdout"] == "partial"


async def test_unconfirmed_cancel_fails_isolate_and_retains_reservation(
    api, agent_api, clock, running_command, computer
):
    """A worker that never confirms cancellation loses its isolate while capacity stays reserved."""
    command = running_command["command"]
    assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 202
    clock.now += timedelta(seconds=31)
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert any(task["kind"] == "destroy" for task in response.json()["tasks"])
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert (
        result["status"] == "isolate_failed"
        and result["result"]["error"] == "cancellation_unconfirmed"
    )
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000


@pytest.mark.parametrize("settings", [{"command_queue_timeout_seconds": 3}], indirect=True)
async def test_queue_timeout_does_not_interrupt_the_running_command(
    api, agent_api, clock, isolate, running_command
):
    """A command that waits behind active work times out without disturbing that active work."""
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": "waiting"},
        json={"command": "second"},
    )
    assert response.status_code == 202
    queued = response.json()
    clock.now += timedelta(seconds=4)
    for _ in range(100):
        result = (await api.get(f"/api/v1/commands/{queued['id']}/result")).json()
        if result["result"] is not None:
            break
        await asyncio.sleep(0.01)
    assert result["status"] == "timed_out" and result["result"]["error"] == "queue_timeout"
    assert (await api.get(f"/api/v1/commands/{running_command['command']['id']}")).json()[
        "status"
    ] == "running"
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ] == []


@pytest.mark.parametrize("settings", [{"task_ack_timeout_seconds": 3}], indirect=True)
async def test_unstarted_dispatch_expires_and_rejects_late_reports(
    api, agent_api, clock, command, run_task
):
    """A delivered run that never starts expires and rejects stale started or completed events."""
    clock.now += timedelta(seconds=4)
    for suffix, payload in [("started", None), ("complete", {"returncode": 0}), ("ack", None)]:
        response = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/{suffix}", json=payload)
        assert response.status_code == 409
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert any(task["kind"] == "destroy" for task in response.json()["tasks"])
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json()[
        "status"
    ] == "isolate_failed"


async def test_destroy_stops_all_commands_before_releasing_capacity(
    api, agent_api, isolate, running_command, computer
):
    """Destroy confirmation terminates active and queued commands and releases reservations exactly once."""
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": "queued"},
        json={"command": "second"},
    )
    assert response.status_code == 202
    queued = response.json()
    assert (await api.delete(f"/api/v1/isolates/{isolate['id']}")).status_code == 202
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 1000
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    assert tasks[0]["kind"] == "destroy"
    destroy = tasks[0]
    for _ in range(2):
        assert (
            await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
        ).status_code == 200
    computer_state = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert [
        computer_state[field] for field in ["reserved_cpu", "reserved_memory", "reserved_disk"]
    ] == [0, 0, 0]
    for row in [queued, running_command["command"]]:
        assert (await api.get(f"/api/v1/commands/{row['id']}/result")).json()[
            "status"
        ] == "cancelled"
    assert (await api.delete(f"/api/v1/isolates/{isolate['id']}")).json()["status"] == "destroyed"


@pytest.mark.parametrize("control_task", ["create", "snapshot", "cancel", "destroy"], indirect=True)
@pytest.mark.parametrize("outcome", ["cancelled", "timed_out"])
async def test_run_only_outcomes_cannot_finish_control_operations(agent_api, control_task, outcome):
    """Only run tasks accept cancelled or timed_out; control operations remain pending after rejection."""
    path = f"/agent/v1/tasks/{control_task['id']}"
    response = await agent_api.post(path + "/complete", json={"outcome": outcome})
    assert response.status_code == 422
    assert (await agent_api.get(path)).json()["status"] == "dispatched"
    if control_task["kind"] == "snapshot":
        assert (
            await agent_api.put(path + "/artifacts/snapshot", content=b"snapshot")
        ).status_code == 200
    assert (await agent_api.post(path + "/complete", json={})).status_code == 200


@pytest.mark.parametrize("control_task", ["create", "snapshot", "cancel", "destroy"], indirect=True)
async def test_failed_control_operations_have_correct_cleanup_semantics(
    api, agent_api, computer, control_task
):
    """Control failures fail the isolate except snapshots, and failed destroy quarantines the worker."""
    response = await agent_api.post(
        f"/agent/v1/tasks/{control_task['id']}/complete",
        json={"outcome": "failed", "error": "runtime failure"},
    )
    assert response.status_code == 200 and response.json()["status"] == "failed"
    isolate = (await api.get(f"/api/v1/isolates/{control_task['isolate_id']}")).json()
    assert isolate["status"] == ("ready" if control_task["kind"] == "snapshot" else "failed")
    assert isolate["reserved"]
    if control_task["kind"] == "destroy":
        assert not (await api.get(f"/api/v1/computers/{computer['id']}")).json()["online"]
        assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
    elif control_task["kind"] != "snapshot":
        tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
            "tasks"
        ]
        destroy = next(task for task in tasks if task["kind"] == "destroy")
        assert (
            await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
        ).status_code == 200
        assert not (await api.get(f"/api/v1/isolates/{isolate['id']}")).json()["reserved"]


@pytest.mark.parametrize("control_task", ["snapshot", "destroy"], indirect=True)
@pytest.mark.parametrize(
    "settings", [{"snapshot_timeout_seconds": 3, "cancellation_timeout_seconds": 3}], indirect=True
)
async def test_control_deadlines_prevent_indefinite_reservations(
    api, agent_api, clock, control_task, computer
):
    """Expired snapshots fail their isolate and unconfirmed destroy quarantines the whole worker."""
    clock.now += timedelta(seconds=4)
    for _ in range(100):
        state = (await api.get(f"/api/v1/isolates/{control_task['isolate_id']}")).json()
        if state["status"] == "failed":
            break
        await asyncio.sleep(0.01)
    assert state["status"] == "failed" and state["reserved"]
    if control_task["kind"] == "destroy":
        assert not (await api.get(f"/api/v1/computers/{computer['id']}")).json()["online"]
    assert (
        await agent_api.post(f"/agent/v1/tasks/{control_task['id']}/complete", json={})
    ).status_code == 409


async def test_failed_run_and_invalid_completion_are_distinct(api, agent_api, command, run_task):
    """Missing returncode and forged cancellation are rejected; an actual launch failure is recorded."""
    path = f"/agent/v1/tasks/{run_task['id']}/complete"
    assert (await agent_api.post(path, json={})).status_code == 422
    assert (await agent_api.post(path, json={"outcome": "cancelled"})).status_code == 409
    assert (
        await agent_api.post(
            path, json={"outcome": "failed", "error": "executable not found", "stderr": "ENOENT"}
        )
    ).status_code == 200
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == "failed" and result["result"]["error"] == "executable not found"
    assert not result["result"]["success"] and result["result"]["returncode"] is None


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_renewed_isolate_survives_original_ttl(api, agent_api, isolate, clock):
    """Renewal extends an isolate's lifetime and permits work after its original expiry."""
    clock.now += timedelta(seconds=9)
    response = await api.post(f"/api/v1/isolates/{isolate['id']}/renew")
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["expires_at"]) > datetime.fromisoformat(
        isolate["expires_at"]
    )
    clock.now += timedelta(seconds=2)
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": "after-renewal"},
        json={"command": "pytest"},
    )
    assert response.status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    assert tasks[0]["kind"] == "run"
    assert (
        await agent_api.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={"returncode": 0})
    ).status_code == 200


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_expired_isolate_rejects_renewal_and_new_work(
    api, agent_api, isolate, clock, run_task
):
    """Expired isolates reject commands, snapshots, acknowledgements and starts while allowing cleanup."""
    clock.now += timedelta(seconds=11)
    path = f"/api/v1/isolates/{isolate['id']}"
    assert (await api.post(path + "/renew")).status_code == 409
    assert (
        await api.post(
            path + "/commands", headers={"Idempotency-Key": "expired"}, json={"command": "pytest"}
        )
    ).status_code == 409
    assert (
        await api.post(path + "/snapshots", headers={"Idempotency-Key": "expired"})
    ).status_code == 409
    for suffix in ["ack", "started"]:
        assert (
            await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/{suffix}")
        ).status_code == 409
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    destroy = next(task for task in tasks if task["kind"] == "destroy")
    assert (
        await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
    ).status_code == 200
    assert (await api.get(path)).json()["status"] == "destroyed"


@pytest.mark.parametrize(
    "settings", [{"max_owner_isolates": 1, "max_pending_commands_per_isolate": 1}], indirect=True
)
async def test_isolate_and_command_quotas_allow_retry_and_recover_after_cancel(
    api, isolate, isolate_payload, command
):
    """Quotas reject new work but idempotent retries and replacement after cancellation remain possible."""
    assert (
        await api.post(
            "/api/v1/isolates", headers={"Idempotency-Key": "overflow"}, json=isolate_payload
        )
    ).status_code == 409
    path = f"/api/v1/isolates/{isolate['id']}/commands"
    assert (
        await api.post(path, headers={"Idempotency-Key": "second"}, json={"command": "next"})
    ).status_code == 409
    assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 202
    assert (
        await api.post(path, headers={"Idempotency-Key": "second"}, json={"command": "next"})
    ).status_code == 202


async def test_isolate_creation_is_idempotent_and_validates_initial_state(
    api, isolate_payload, artifact, company
):
    """Isolate creation deduplicates requests, detects key conflicts and validates state ownership and size."""
    headers = {"Idempotency-Key": "isolate"}
    first, duplicate = await asyncio.gather(
        api.post("/api/v1/isolates", headers=headers, json=isolate_payload),
        api.post("/api/v1/isolates", headers=headers, json=isolate_payload),
    )
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    assert (
        await api.post(
            "/api/v1/isolates", headers=headers, json={**isolate_payload, "image": "other"}
        )
    ).status_code == 409
    for overrides in [{"company_id": company["id"]}, {"disk_bytes": 1}]:
        assert (
            await api.post(
                "/api/v1/isolates",
                headers={"Idempotency-Key": str(uuid4())},
                json={**isolate_payload, **overrides},
            )
        ).status_code == 422
    response = await api.get("/api/v1/isolates")
    assert response.status_code == 200 and len(response.json()) == 1
    assert (await api.get("/api/v1/isolates", params={"offset": 1})).json() == []


async def test_destroy_unassigned_isolate_needs_no_worker(api, provisioning_isolate):
    """A still-unassigned isolate can be destroyed immediately without inventing a cleanup task."""
    response = await api.delete(f"/api/v1/isolates/{provisioning_isolate['id']}")
    assert response.status_code == 202
    assert response.json()["status"] == "destroyed" and not response.json()["reserved"]


@pytest.mark.parametrize("settings", [{"provisioning_timeout_seconds": 3}], indirect=True)
async def test_capacity_wait_is_bounded_by_provisioning_deadline(api, provisioning_isolate, clock):
    """Lack of matching capacity produces a persistent provisioning timeout instead of infinite waiting."""
    clock.now += timedelta(seconds=4)
    for _ in range(100):
        response = await api.get(f"/api/v1/isolates/{provisioning_isolate['id']}")
        if response.json()["status"] == "failed":
            break
        await asyncio.sleep(0.01)
    assert (
        response.json()["status"] == "failed" and response.json()["error"] == "provisioning_timeout"
    )
    assert not response.json()["reserved"]


@pytest.mark.parametrize("settings", [{"provisioning_timeout_seconds": 3}], indirect=True)
async def test_provisioning_deadline_includes_capacity_wait(
    api, client, clock, provisioning_isolate, late_agent
):
    """Late capacity does not reset the total provisioning deadline or permit an expired create report."""
    response = await client.post(
        "/agent/v1/tasks/claim", headers=late_agent, json={"wait_seconds": 2}
    )
    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["deadline"] == provisioning_isolate["provisioning_deadline"]
    clock.now += timedelta(seconds=2)
    assert (
        await client.post(f"/agent/v1/tasks/{task['id']}/complete", headers=late_agent, json={})
    ).status_code == 409
    tasks = (
        await client.post("/agent/v1/tasks/claim", headers=late_agent, json={"wait_seconds": 2})
    ).json()["tasks"]
    assert any(task["kind"] == "destroy" for task in tasks)
    assert (await api.get(f"/api/v1/isolates/{provisioning_isolate['id']}")).json()[
        "status"
    ] == "failed"


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_expired_creation_cannot_make_an_isolate_ready(api, agent_api, clock, create_task):
    """A create report received after isolate TTL expiry cannot transition it to ready."""
    clock.now += timedelta(seconds=11)
    assert (
        await agent_api.post(f"/agent/v1/tasks/{create_task['id']}/complete", json={})
    ).status_code == 409
    assert (await api.get(f"/api/v1/isolates/{create_task['isolate_id']}")).json()[
        "status"
    ] != "ready"


@pytest.mark.parametrize("settings", [{"isolate_ttl_seconds": 10}], indirect=True)
async def test_renewal_keeps_already_dispatched_work_valid(
    api, agent_api, clock, isolate, run_task
):
    """Renewing an isolate extends validity of an existing run task beyond the original isolate TTL."""
    clock.now += timedelta(seconds=9)
    assert (await api.post(f"/api/v1/isolates/{isolate['id']}/renew")).status_code == 200
    clock.now += timedelta(seconds=2)
    assert (await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/started")).status_code == 200
    assert (
        await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0})
    ).status_code == 200


@pytest.mark.parametrize(
    "order",
    [
        ("run", "cancel", "destroy"),
        ("run", "destroy", "cancel"),
        ("cancel", "run", "destroy"),
        ("cancel", "destroy", "run"),
        ("destroy", "run", "cancel"),
        ("destroy", "cancel", "run"),
    ],
)
async def test_concurrent_run_cancel_destroy_keeps_one_result_and_releases_capacity(
    api, agent_api, isolate, command, run_task, computer, order
):
    """Concurrent terminal events preserve the winning result and release worker capacity exactly once."""
    assert (await api.delete(f"/api/v1/isolates/{isolate['id']}")).status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
        "tasks"
    ]
    by_kind = {task["kind"]: task for task in tasks}
    by_kind["run"] = run_task
    responses = await asyncio.gather(
        *(
            agent_api.post(
                f"/agent/v1/tasks/{by_kind[kind]['id']}/complete",
                json={"returncode": 0, "stdout": kind},
            )
            for kind in order
        )
    )
    outcomes = dict(zip(order, responses, strict=True))
    assert outcomes["destroy"].status_code == 200
    assert all(response.status_code in {200, 409} for response in responses)
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    if outcomes["run"].status_code == 200:
        assert result["status"] == "completed" and result["result"]["stdout"] == "run"
    elif outcomes["cancel"].status_code == 200:
        assert result["status"] == "cancelled" and result["result"]["stdout"] == "cancel"
    else:
        assert result["status"] == "cancelled" and result["result"]["error"] == "isolate_deleted"
    state = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert [state[field] for field in ["reserved_cpu", "reserved_memory", "reserved_disk"]] == [
        0,
        0,
        0,
    ]
    assert (
        await agent_api.post(
            f"/agent/v1/tasks/{by_kind['destroy']['id']}/complete",
            json={"returncode": 0, "stdout": "destroy"},
        )
    ).status_code == 200
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json() == result


@pytest.mark.parametrize("command", [{"timeout_seconds": 5}], indirect=True)
@pytest.mark.parametrize("reason", ["user", "timeout"])
async def test_run_report_can_confirm_requested_cancellation(
    api, agent_api, clock, running_command, reason
):
    """A run's cancelled report acknowledges stopping and distinguishes external cancellation from timeout."""
    command, task = running_command["command"], running_command["task"]
    if reason == "user":
        assert (await api.post(f"/api/v1/commands/{command['id']}/cancel")).status_code == 202
    else:
        clock.now += timedelta(seconds=6)
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    cancel = next(task for task in tasks if task["kind"] == "cancel")
    assert (
        await agent_api.post(
            f"/agent/v1/tasks/{task['id']}/complete",
            json={"outcome": "cancelled", "returncode": -15, "stdout": "stopped"},
        )
    ).status_code == 200
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    assert result["status"] == ("cancelled" if reason == "user" else "timed_out")
    assert result["result"]["stdout"] == "stopped" and result["result"]["returncode"] == -15
    assert (
        await agent_api.post(f"/agent/v1/tasks/{cancel['id']}/complete", json={})
    ).status_code == 409
