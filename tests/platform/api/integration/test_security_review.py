"""Security checks requiring controlled concurrent HTTP requests."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("settings", [{"max_results_response_bytes": 8192}], indirect=True)
async def test_result_completion_between_size_check_and_read_is_still_bounded(
    api, agent_api, running_command, paused_command_poll
):
    """A worker completing after the lightweight size check cannot bypass the batch response limit."""
    command_id = running_command["command"]["id"]
    async with asyncio.TaskGroup() as group:
        poll = group.create_task(api.post("/api/v1/commands/results", json={"ids": [command_id]}))
        await asyncio.wait_for(paused_command_poll["entered"].wait(), timeout=5)
        response = await agent_api.post(
            f"/agent/v1/tasks/{running_command['task']['id']}/complete",
            json={"returncode": 0, "stdout": "a" * 2048},
        )
        assert response.status_code == 200, response.text
        paused_command_poll["resume"].set()
    response = poll.result()
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "Result batch exceeds response size limit"}
