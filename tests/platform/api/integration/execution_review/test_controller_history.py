"""Controller work remains bounded by unfinished execution instead of retained failure history."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("controller_failure_history", [1, 32], indirect=True)
async def test_controller_skips_cleaned_failures_but_reconciles_unfinished_cleanup(
    api, agent_api, controller_failure_history
):
    """Retained failed isolates are never materialized by a tick; unallocated failures receive a cleanup timestamp and reserved failures retain capacity until real worker confirmation."""
    state = controller_failure_history
    state["resume"].set()
    await asyncio.wait_for(state["completed"].wait(), timeout=5)
    loaded = state["ticks"][0]
    assert set(loaded) == {state["reserved_id"], state["unfinished_id"]}
    assert not state["historical_ids"].intersection(loaded)
    assert len(loaded) == 2

    response = await api.get(f"/v1/isolates/{state['unfinished_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["reserved"] is False
    assert response.json()["destroyed_at"] is not None
    response = await api.get(f"/v1/computers/{state['computer_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["reserved_cpu"] == 1000

    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["kind"] == "destroy"
    assert tasks[0]["isolate_id"] == state["reserved_id"]
    response = await agent_api.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/isolates/{state['reserved_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["reserved"] is False
    assert response.json()["destroyed_at"] is not None
    response = await api.get(f"/v1/computers/{state['computer_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["reserved_cpu"] == 0

    state["completed"].clear()
    state["resume"].set()
    await asyncio.wait_for(state["completed"].wait(), timeout=5)
    assert state["ticks"][1] == ()
