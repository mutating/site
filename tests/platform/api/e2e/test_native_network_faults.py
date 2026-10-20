"""The shipping Swift HTTP client against the real API through broken TCP sockets."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.platform.api.e2e.network_faults import FaultProxy

pytestmark = pytest.mark.anyio


@pytest.fixture
def native_network_cli():
    """Require the native binary in its dedicated CI job; ordinary Linux API jobs skip it."""
    executable = os.environ.get("POWER_CELL_NETWORK_CLI")
    if not executable:
        assert not os.environ.get("POWER_CELL_NETWORK_REQUIRED"), (
            "Native network checks must not skip"
        )
        pytest.skip("Dedicated macOS network job supplies POWER_CELL_NETWORK_CLI")
    assert Path(executable).is_file()
    return executable


@pytest.mark.parametrize(
    "mode",
    [
        "drop_before",
        "drop_after",
        "truncate_response",
        "delay_request",
        "delay_response",
        "cut_upload",
    ],
)
@pytest.mark.parametrize("operation", ["ack", "started", "complete", "upload"])
async def test_native_exact_replay_with_real_socket_faults(
    native_network_cli, agent_api, agent, run_task, server, tmp_path, mode, operation
):
    """Lost requests/responses and partial transfers preserve one task and one exact result."""
    report = {"returncode": 0, "stdout": "exact result"} if operation == "complete" else {}
    body = tmp_path / "body"
    body.write_bytes(b"binary\x00output" if operation == "upload" else json.dumps(report).encode())
    log = tmp_path / "attempts.ndjson"
    path = f"tasks/{run_task['id']}/" + ("artifacts/stdout" if operation == "upload" else operation)
    environment = {
        **os.environ,
        "POWER_CELL_NETWORK_CREDENTIAL": agent["headers"]["Authorization"].removeprefix("Bearer "),
        "POWER_CELL_NETWORK_SESSION": run_task["session_id"],
        "POWER_CELL_NETWORK_CONTENT_TYPE": "application/octet-stream"
        if operation == "upload"
        else "application/json",
    }
    async with FaultProxy(server, mode) as proxy:
        process = await asyncio.create_subprocess_exec(
            native_network_cli,
            "network-request-check",
            proxy.url + "/v1",
            "PUT" if operation == "upload" else "POST",
            path,
            str(body),
            str(log),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, stderr.decode()
        assert b"HTTP 200" in stdout
        json.loads(stdout.decode().splitlines()[1])
        forwarded_attempts = proxy.attempts
    attempts = [json.loads(line) for line in log.read_text().splitlines()]
    assert 1 <= len(attempts) <= 3
    assert all(row.get("created") for row in attempts)
    assert len({row["operation_id"] for row in attempts}) == 1
    assert len({row["request_id"] for row in attempts}) == len(attempts)
    if mode in {"delay_request", "delay_response"}:
        assert attempts[0]["duration_ms"] >= 250
    else:
        assert forwarded_attempts >= 2
        # Foundation may replay an idempotent PUT internally. POST replay must
        # remain explicit, with a new request ID and a visible failed attempt.
        if operation != "upload" or len(attempts) > 1:
            assert len(attempts) >= 2 and attempts[0]["error_code"] is not None
    response = await agent_api.get(f"/agent/v1/tasks/{run_task['id']}")
    assert response.status_code == 200
    if operation == "complete":
        receipt = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/receipt", json=report)
        assert receipt.json()["accepted"] is True
    elif operation in {"ack", "started"}:
        assert response.json()["status"] == "accepted"
