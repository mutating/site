"""Additional execution resources created through the live API."""

import pytest


@pytest.fixture
async def paired_isolates(api, agent_api, isolate, isolate_payload):
    """Fill one worker with two ready isolates to exercise cleanup of multiple assignments."""
    response = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": "second-ready-isolate"},
        json=isolate_payload,
    )
    assert response.status_code == 202, response.text
    second = response.json()
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["isolate_id"] == second["id"]
    response = await agent_api.post(f"/agent/v1/tasks/{tasks[0]['id']}/complete", json={})
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/isolates/{second['id']}")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    return [isolate, response.json()]
