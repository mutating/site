"""Optional macOS test of the actual Swift binding against the real HTTP API."""

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


async def test_actual_swift_rust_measurements_upload_and_replay(api, server, user):
    executable = os.environ.get("POWER_CELL_BENCHMARK_CLI")
    if not executable:
        pytest.skip("Set POWER_CELL_BENCHMARK_CLI to the macOS powercell-dev executable")
    assert await asyncio.to_thread(Path(executable).is_file), (
        "Configured Swift benchmark client does not exist"
    )
    response = await api.post(
        "/v1/computers",
        json={
            "name": "Native benchset integration",
            "os": "macos",
            "architecture": "arm64",
            "runtime": "powercell-dev-benchmark",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 201, response.text
    computer = response.json()
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    environment = {
        **os.environ,
        "POWER_CELL_TEST_BENCHMARK_CREDENTIAL": response.json()["token"],
        "POWER_CELL_TEST_BENCHMARK_ACCOUNT_TOKEN": user["login"]["access_token"],
        "POWER_CELL_TEST_BENCHMARK_COMPUTER_ID": computer["id"],
    }
    process = await asyncio.create_subprocess_exec(
        executable,
        "benchmark-check",
        server + "/v1",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    assert b"Benchmark end-to-end check passed" in stdout
    rows = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    assert {"single_core", "multi_core"} <= {row["benchmark_name"] for row in rows}
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(row["number"] > 0 and row["benchmarker_version"] for row in rows)
