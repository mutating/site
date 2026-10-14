"""Security regressions through the public HTTP API and a real agent session."""

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("settings", [{"max_results_response_bytes": 8192}], indirect=True)
async def test_repeated_command_result_ids_cannot_amplify_a_large_response(
    api, agent_api, running_command
):
    """Repeated IDs must not turn one authorized result into an unbounded batch response."""
    command_id = running_command["command"]["id"]
    response = await agent_api.post(
        f"/agent/v1/tasks/{running_command['task']['id']}/complete",
        json={"returncode": 0, "stdout": "a" * 1024},
    )
    assert response.status_code == 200, response.text

    single = await api.get(f"/api/v1/commands/{command_id}/result")
    assert single.status_code == 200, single.text
    assert single.json()["result"]["stdout"] == "a" * 1024

    repeated = await api.post("/api/v1/commands/results", json={"ids": [command_id] * 4})
    assert repeated.status_code == 422, repeated.text
    assert repeated.json() == {"detail": "Result batch exceeds response size limit"}

    statuses = await api.post("/api/v1/commands/statuses", json={"ids": [command_id] * 4})
    assert statuses.status_code == 200, statuses.text
    assert [item["status"] for item in statuses.json()] == ["completed"] * 4


@pytest.mark.parametrize("settings", [{"max_results_response_bytes": 8192}], indirect=True)
async def test_large_batch_with_foreign_command_remains_hidden(
    api, agent_api, running_command, foreign_command
):
    """A result-size rejection must not reveal whether a foreign command ID exists."""
    command_id = running_command["command"]["id"]
    response = await agent_api.post(
        f"/agent/v1/tasks/{running_command['task']['id']}/complete",
        json={"returncode": 0, "stdout": "a" * 1024},
    )
    assert response.status_code == 200, response.text

    response = await api.post(
        "/api/v1/commands/results",
        json={"ids": [command_id] * 4 + [foreign_command["id"]]},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Resource not found"}
