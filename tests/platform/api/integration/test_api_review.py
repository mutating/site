"""End-to-end regressions for API review using real HTTP, database reads and controlled races."""

import asyncio
import json

import pytest

pytestmark = pytest.mark.anyio


async def test_poll_result_and_status_share_one_database_snapshot(
    api, agent_api, running_command, paused_command_poll
):
    """A completion between polling reads must never pair the old running status with a newly committed terminal result."""
    command_id = running_command["command"]["id"]
    async with asyncio.TaskGroup() as group:
        poll = group.create_task(api.get(f"/v1/commands/{command_id}/result"))
        await asyncio.wait_for(paused_command_poll["entered"].wait(), timeout=5)
        response = await agent_api.post(
            f"/agent/v1/tasks/{running_command['task']['id']}/complete", json={"returncode": 0}
        )
        assert response.status_code == 200, response.text
        paused_command_poll["resume"].set()
    response = poll.result()
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["status"] == "running" and data["result"] is None) or (
        data["status"] == "completed" and data["result"]["returncode"] == 0
    ), data
    latest = (await api.get(f"/v1/commands/{command_id}/result")).json()
    assert latest["status"] == "completed" and latest["result"]["returncode"] == 0


@pytest.mark.parametrize("endpoint", ["statuses", "results"])
@pytest.mark.parametrize("size", [1, 12])
async def test_command_poll_batch_has_constant_query_count_and_preserves_input_order(
    api, polling_commands, polling_query_observer, endpoint, size
):
    """Polling uses a fixed number of queries and preserves input order and repeated IDs without N+1 reads."""
    ids = [row["id"] for row in reversed(polling_commands[:size])]
    ids.append(ids[0])
    response = await api.post(f"/v1/commands/{endpoint}", json={"ids": ids})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == ids
    assert len(polling_query_observer) == (2 if endpoint == "results" else 1), (
        polling_query_observer
    )


@pytest.mark.parametrize("number", ["1e9999", "-1e9999", "NaN", "Infinity"])
async def test_validation_diagnostics_remain_valid_json_for_nonfinite_numbers(agent_api, number):
    """Overflowing JSON numbers and nonfinite parser extensions receive a parseable 422 instead of emitting forbidden NaN or Infinity in the diagnostic input."""
    response = await agent_api.post(
        "/agent/v1/tasks/claim",
        content='{"wait_seconds":' + number + "}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    diagnostic = json.loads(response.text, parse_constant=pytest.fail)
    assert diagnostic["detail"][0]["input"] is None


async def test_non_utf8_body_for_json_endpoint_returns_parseable_validation_error(api):
    """A binary body sent to a JSON endpoint receives 422 even when diagnostic input bytes cannot be decoded as UTF-8."""
    response = await api.post(
        "/v1/companies",
        content=b"\xff\x00",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 422, response.text
    diagnostic = json.loads(response.text, parse_constant=pytest.fail)
    assert diagnostic["detail"][0]["type"] == "model_attributes_type"
    assert "\ufffd" in diagnostic["detail"][0]["input"]
