"""The scheduler and resource ledger share transactional reservations and cleanup semantics."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cancel", [False, True])
async def test_reservations_last_until_confirmed_isolate_cleanup(
    api,
    agent_api,
    user,
    computer,
    computer_resources,
    isolate,
    command,
    run_task,
    clock,
    history_range,
    cancel,
):
    """Command completion or cancellation keeps capacity reserved; only confirmed isolate destruction releases it, exactly once."""
    current = (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()
    assert {row["code"]: (row["occupied"], row["free"]) for row in current["resources"]} == {
        "cpu": (1000, 1000),
        "memory": (1024, 1024),
    }
    for row in computer_resources.values():
        path = f"/v1/computer-resources/{row['id']}"
        assert (await api.delete(path)).status_code == 409
        assert (await api.patch(path, json={"total": 0})).status_code == 409
    if cancel:
        assert (await api.post(f"/v1/commands/{command['id']}/cancel")).status_code == 202
        tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})).json()[
            "tasks"
        ]
        completion = next(task for task in tasks if task["kind"] == "cancel")
    else:
        completion = run_task
    assert (
        await agent_api.post(f"/agent/v1/tasks/{completion['id']}/complete", json={"returncode": 0})
    ).status_code == 200
    current = (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()
    assert {row["code"]: row["occupied"] for row in current["resources"]} == {
        "cpu": 1000,
        "memory": 1024,
    }
    assert (await api.delete(f"/v1/isolates/{isolate['id']}")).status_code == 202
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    destroy = next(task for task in tasks if task["kind"] == "destroy")
    assert (await api.get(f"/v1/computer-resources/{computer_resources['cpu']['id']}")).json()[
        "occupied"
    ] == 1000
    clock.now += timedelta(seconds=1)
    for _ in range(2):
        assert (
            await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
        ).status_code == 200
    report = (
        await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)
    ).json()
    assert len(report["points"]) == 3
    assert [
        {row["code"]: row["occupied"] for row in point["resources"]} for point in report["points"]
    ] == [
        {"cpu": 0, "memory": 0},
        {"cpu": 1000, "memory": 1024},
        {"cpu": 0, "memory": 0},
    ]
    for code in ["cpu", "memory"]:
        row = (await api.get(f"/v1/computer-resources/{computer_resources[code]['id']}")).json()
        assert row["version"] == 3 and row["occupied"] == 0 and row["free"] == row["total"]
    legacy = (await api.get(f"/v1/computers/{computer['id']}")).json()
    assert legacy["reserved_cpu"] == legacy["reserved_memory"] == 0


async def test_worker_loss_keeps_reservations_until_clean_start(
    api, agent_api, agent, isolate, user, computer_resources, clock, settings, history_range
):
    """A lost worker remains occupied in inventory and history until a fenced clean-start report confirms all old sandboxes were removed."""
    clock.now += timedelta(seconds=settings.agent_lease_seconds)
    assert (await agent_api.post("/agent/v1/heartbeat")).status_code == 409
    for code, amount in [("cpu", 1000), ("memory", 1024)]:
        assert (await api.get(f"/v1/computer-resources/{computer_resources[code]['id']}")).json()[
            "occupied"
        ] == amount
    before = (
        await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)
    ).json()
    assert len(before["points"]) == 2
    payload = {"clean_start": True, "previous_session_id": agent["session"]["session_id"]}
    for _ in range(2):
        response = await agent_api.post(
            "/agent/v1/session", headers={"Idempotency-Key": "resource-clean-start"}, json=payload
        )
        assert response.status_code == 200
    after = (
        await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)
    ).json()
    assert len(after["points"]) == 3
    assert all(row["occupied"] == 0 for row in after["points"][-1]["resources"])


@pytest.mark.parametrize("code,legacy", [("cpu", "cpu_millis"), ("memory", "memory_bytes")])
async def test_deleted_builtin_resource_blocks_allocation_until_restored(
    api, agent_api, computer, computer_resources, disabled_pending_isolate, code, legacy
):
    """A computer missing CPU or memory is ineligible; restoring the same record through the legacy capacity field enables allocation."""
    # Disable allocation while deleting the unoccupied record; no artificial scheduler mocks are needed.
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": False})
    ).status_code == 200
    resource_path = f"/v1/computer-resources/{computer_resources[code]['id']}"
    assert (await api.delete(resource_path)).status_code == 204
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": True})
    ).status_code == 200
    assert (await api.get(f"/v1/computers/{computer['id']}")).json()[legacy] == 0
    assert (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.05})).json()[
        "tasks"
    ] == []
    response = await api.patch(f"/v1/computers/{computer['id']}", json={legacy: computer[legacy]})
    assert response.status_code == 200
    tasks = (await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})).json()[
        "tasks"
    ]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] == disabled_pending_isolate["id"]
    resource = (await api.get(resource_path)).json()
    assert resource["occupied"] > 0 and resource["version"] == 4
