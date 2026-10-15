"""Opt-in real macOS application/model, Rust, TCP fault and database scenarios."""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.anyio


class BenchmarkFaultProxy:
    """Forward real HTTP, optionally reject uploads or lose their committed ACK.

    Unlike an ASGI mock, aborting this socket exercises URLSession's transport
    error handling. Only the benchmark upload is faulted; authentication and
    independent result reads always reach the actual API.
    """

    def __init__(self, upstream, fault=None, recover_after=None):
        self.upstream = upstream
        self.fault = fault
        self.recover_after = recover_after
        self.attempts = 0
        self.committed = []
        self.errors = []
        self.connections = set()

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=10, trust_env=False)
        self.listener = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.listener.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *_):
        self.listener.close()
        await self.listener.wait_closed()
        for writer in tuple(self.connections):
            writer.close()
        await self.client.aclose()

    async def handle(self, reader, writer):
        self.connections.add(writer)
        try:
            async with asyncio.timeout(10):
                head = await reader.readuntil(b"\r\n\r\n")
                request_line, *header_lines = head.decode("latin-1").split("\r\n")
                method, target, _ = request_line.split(" ", 2)
                headers = dict(line.split(": ", 1) for line in header_lines if line)
                lower = {key.lower(): value for key, value in headers.items()}
                body = await reader.readexactly(int(lower.get("content-length", "0")))
                upload = method == "PUT" and target == "/agent/v1/benchmark-results"
                fault = None
                if upload:
                    self.attempts += 1
                    if self.recover_after is None or self.attempts <= self.recover_after:
                        fault = self.fault
                if fault == "disconnect":
                    writer.transport.abort()
                    return
                if fault == "503":
                    status, data = 503, b'{"detail":"Injected benchmark outage"}'
                else:
                    response = await self.client.request(
                        method,
                        self.upstream + target,
                        headers={
                            key: value
                            for key, value in headers.items()
                            if key.lower() not in {"host", "connection", "accept-encoding"}
                        },
                        content=body,
                    )
                    status, data = response.status_code, response.content
                    if upload and status == 200:
                        self.committed.append(response.json())
                    if fault == "lost_ack":
                        assert status == 200, response.text
                        writer.transport.abort()
                        return
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                    + data
                )
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            self.connections.discard(writer)
            writer.close()
            await writer.wait_closed()


@pytest.fixture
async def mac_benchmark(api, user, server, tmp_path):
    """Allocate one native-app state directory and one real Computer per test."""
    executable = os.environ.get("POWER_CELL_BENCHMARK_XCTEST")
    if not executable:
        pytest.skip("Set POWER_CELL_BENCHMARK_XCTEST to PowerCellPackageTests.xctest executable")
    assert await asyncio.to_thread(Path(executable).is_file), (
        "Configured macOS application test executable is missing"
    )
    response = await api.post(
        "/v1/computers",
        json={
            "name": "Mac application benchmark E2E",
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
        "POWER_CELL_E2E_DIRECTORY": str(tmp_path / "mac-application"),
        "POWER_CELL_E2E_LOGIN": user["email"],
        "POWER_CELL_E2E_PASSWORD": user["password"],
        "POWER_CELL_TEST_BENCHMARK_CREDENTIAL": response.json()["token"],
        "POWER_CELL_TEST_BENCHMARK_ACCOUNT_TOKEN": user["login"]["access_token"],
        "POWER_CELL_TEST_BENCHMARK_COMPUTER_ID": computer["id"],
    }

    async def run(scenario, proxy):
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/xcrun",
            "xctest",
            "-XCTest",
            "PowerCellCoreTests.BenchmarkEndToEndTests/testApplicationBenchmarkAgainstLiveAPI",
            str(Path(executable).parents[2]),
            env={
                **environment,
                "DEVELOPER_DIR": os.environ.get(
                    "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"
                ),
                "POWER_CELL_E2E_SERVER": proxy.url,
                "POWER_CELL_E2E_SCENARIO": scenario,
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if scenario == "network-crash":
                marker = Path(environment["POWER_CELL_E2E_DIRECTORY"]) / "ready-to-crash"
                async with asyncio.timeout(20):
                    while not await asyncio.to_thread(marker.exists):
                        assert process.returncode is None, (
                            "Mac process exited before durable queue checkpoint"
                        )
                        await asyncio.sleep(0.01)
                process.kill()
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=200)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        output = stdout.decode() + stderr.decode()
        if scenario == "network-crash":
            assert process.returncode == -9, output
        else:
            assert process.returncode == 0, output
            assert f"Mac benchmark E2E passed: {scenario}" in output, output
        assert not proxy.errors, proxy.errors
        archive_path = Path(environment["POWER_CELL_E2E_DIRECTORY"]) / "benchmarks.json"
        archive = json.loads(await asyncio.to_thread(archive_path.read_text))
        return archive

    return run, computer, server


@pytest.mark.parametrize(
    "scenario",
    [
        "onboarding-success",
        "onboarding-fail-first",
        "onboarding-fail-second",
        "onboarding-cancel-first",
        "onboarding-cancel-second",
        "settings-success",
        "settings-warmup",
        "settings-fail-first",
        "settings-fail-second",
        "settings-cancel-first",
        "settings-cancel-second",
        "settings-cancel-warmup",
        "settings-blocked",
        "settings-full",
    ],
)
async def test_mac_application_native_benchmark_scenarios(mac_benchmark, api, scenario):
    """Login/manual runs preserve each successful result through UI-model transitions, registration recovery, native cancellation/failure and SQL upserts; the full case measures 60 seconds per test."""
    run, computer, server = mac_benchmark
    async with BenchmarkFaultProxy(server) as proxy:
        archive = await run(scenario, proxy)
    assert archive["pending"] == []
    rows = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    scores = [score for report in archive["reports"] for score in report["results"]]
    assert {row["benchmark_name"]: row["number"] for row in rows} == {
        score["benchmark_name"]: score["number"] for score in scores
    }
    assert all(report["benchmarker_version"] for report in archive["reports"])
    assert len({row["id"] for row in rows}) == len(rows)


@pytest.mark.parametrize("fault", ["503", "disconnect", "lost_ack"])
@pytest.mark.parametrize("shutdown", ["network-produce", "network-crash"])
async def test_mac_network_failure_queue_survives_actual_process_restart(
    mac_benchmark, api, fault, shutdown
):
    """HTTP 503, broken TCP and a lost post-commit ACK survive both clean exit and SIGKILL; a fresh Mac process sends the durable queue without rerunning Rust or changing committed row UUIDs."""
    run, computer, server = mac_benchmark
    async with BenchmarkFaultProxy(server, fault=fault) as proxy:
        before = await run(shutdown, proxy)
        assert len(before["pending"]) == len(before["reports"]) >= 2
        assert proxy.attempts > 0
        committed = {row["id"] for batch in proxy.committed for row in batch}
        assert bool(committed) == (fault == "lost_ack")
        proxy.fault = None
        after = await run("network-recover", proxy)
    assert after["pending"] == []
    assert after["reports"] == before["reports"]
    rows = (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json()
    assert len(rows) == len(after["reports"])
    assert committed <= {row["id"] for row in rows}


@pytest.mark.parametrize("fault", ["503", "disconnect", "lost_ack"])
async def test_mac_network_recovery_retries_without_manual_action(mac_benchmark, fault):
    """After two real delivery failures the running application's retry loop drains the queue automatically, including replay of an already committed measurement."""
    run, _, server = mac_benchmark
    async with BenchmarkFaultProxy(server, fault=fault, recover_after=2) as proxy:
        archive = await run("network-retry", proxy)
        assert proxy.attempts >= 4
    assert archive["pending"] == []
    assert len(archive["reports"]) >= 2


async def test_mac_manual_retry_does_not_wait_for_automatic_retry_timer(mac_benchmark):
    """Explicit Retry restarts delivery immediately after a real 503, even when the automatic retry is sleeping for a minute, and sends both saved measurements."""
    run, _, server = mac_benchmark
    async with BenchmarkFaultProxy(server, fault="503", recover_after=1) as proxy:
        archive = await run("network-manual", proxy)
        assert proxy.attempts >= 3
    assert archive["pending"] == []
