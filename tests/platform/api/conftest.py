"""Isolated infrastructure and HTTP-created resources shared by the test suite."""

import asyncio
import os
import socket
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import anyio
import pytest
import uvicorn
from httpx import AsyncClient
from sqlalchemy.engine import make_url

from throng_api.app import create_app
from throng_api.cli import migrate
from throng_api.config import Settings


@pytest.fixture
def anyio_backend():
    """Run async fixtures and tests on the application's asyncio backend."""
    return "asyncio"


@pytest.fixture
async def database_url(tmp_path):
    """Allocate a fresh SQLite file or a disposable PostgreSQL database per test."""
    admin_url = os.environ.get("THRONG_TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        yield f"sqlite+aiosqlite:///{tmp_path}/database/throng.db"
        return
    import asyncpg

    admin = await asyncpg.connect(admin_url)
    name = "throng_test_" + uuid4().hex
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield (
            make_url(admin_url)
            .set(drivername="postgresql+asyncpg", database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def settings(database_url, tmp_path, request):
    """Migrate a real database and configure bounded, fast controller polling."""
    values = {
        "database_url": database_url,
        "storage_path": tmp_path / "artifacts",
        "controller_interval": 0.01,
        "long_poll_interval": 0.01,
    }
    values.update(getattr(request, "param", {}))
    settings = Settings(**values, _env_file=None)
    await anyio.to_thread.run_sync(migrate, settings)
    return settings


@pytest.fixture
def clock():
    """Provide an explicit application clock without waiting minutes for leases."""
    return SimpleNamespace(now=datetime(2026, 10, 8, 12, tzinfo=UTC))


@pytest.fixture
def app(settings, clock):
    """Create the unmocked API with its embedded controller and a controllable clock."""
    return create_app(settings, clock=lambda: clock.now)


@pytest.fixture
async def http_server(app):
    """Serve the app over a real, prebound loopback socket and shut it down reliably."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server.capture_signals = nullcontext
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("HTTP server exited before startup")
                await asyncio.sleep(0.01)
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{listener.getsockname()[1]}", server=server, task=task
        )
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            listener.close()


@pytest.fixture
def server(http_server):
    """Expose the running server's URL to native HTTP client fixtures."""
    return http_server.url


@pytest.fixture
async def client(server):
    """Open an anonymous HTTP client; application errors arrive as HTTP responses."""
    async with AsyncClient(base_url=server, timeout=10, trust_env=False) as client:
        yield client


@pytest.fixture
async def user(client):
    """Register Alice and obtain her full-scope login token through the public API."""
    data = {
        "email": "alice@example.com",
        "password": "  correct horse battery staple  ",
        "first_name": "Alice",
        "last_name": "Example",
        "nickname": "alice",
    }
    response = await client.post("/v1/users", json=data)
    assert response.status_code == 201, response.text
    profile = response.json()
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": data["email"],
            "password": data["password"],
        },
    )
    assert response.status_code == 200, response.text
    return {
        **profile,
        "password": data["password"],
        "login": response.json(),
        "headers": {"Authorization": "Bearer " + response.json()["access_token"]},
    }


@pytest.fixture
async def other_user(client):
    """Register an independent user for ownership and membership scenarios."""
    response = await client.post(
        "/v1/users",
        json={
            "email": "bob@example.com",
            "password": "bob's sufficiently long password",
            "first_name": "Bob",
            "last_name": "Example",
            "nickname": "bob",
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": "bob@example.com",
            "password": "bob's sufficiently long password",
        },
    )
    assert response.status_code == 200, response.text
    return {**profile, "headers": {"Authorization": "Bearer " + response.json()["access_token"]}}


@pytest.fixture
async def api(server, user):
    """Provide Alice's native HTTP client, without wrapping request or assertion logic."""
    async with AsyncClient(
        base_url=server, headers=user["headers"], timeout=10, trust_env=False
    ) as client:
        yield client


@pytest.fixture
def cli_environment(settings):
    """Point real CLI subprocesses exclusively at this test's isolated database and storage."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("THRONG_")}
    environment.update(
        THRONG_DATABASE_URL=settings.database_url, THRONG_STORAGE_PATH=str(settings.storage_path)
    )
    return environment


@pytest.fixture
async def system_admin(client, cli_environment):
    """Register a separate operator and grant system administration through the real host CLI."""
    response = await client.post(
        "/v1/users",
        json={
            "email": "operator@example.com",
            "password": "operator long password",
            "first_name": "System",
            "last_name": "Operator",
            "nickname": "operator",
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()
    response = await client.post(
        "/v1/auth/login",
        json={"email": "operator@example.com", "password": "operator long password"},
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "throng_api.cli",
        "system-admin",
        user["id"],
        env=cli_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0, errors.decode()
    response = await client.get("/v1/users/me", headers=headers)
    assert response.status_code == 200 and response.json()["is_system_admin"] is True
    return {**user, "headers": headers}


@pytest.fixture
async def admin_api(server, system_admin):
    """Open a native HTTP client with the operator's personal bearer token."""
    async with AsyncClient(
        base_url=server, headers=system_admin["headers"], timeout=10, trust_env=False
    ) as client:
        yield client


@pytest.fixture
async def resource_type(admin_api, request):
    """Create a custom resource type using the system administrator API."""
    payload = {"code": "gpu", "name": "GPU", "description": "Accelerator count", "unit": "device"}
    payload.update(getattr(request, "param", {}))
    response = await admin_api.post("/v1/resource-types", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def computer_resources(api, computer):
    """Read the computer's automatically created CPU and memory resources over HTTP."""
    response = await api.get(f"/v1/computers/{computer['id']}/resources")
    assert response.status_code == 200, response.text
    return {row["resource_type"]["code"]: row for row in response.json()}


@pytest.fixture
async def other_api(server, other_user):
    """Provide Bob's independently authenticated native HTTP client."""
    async with AsyncClient(
        base_url=server, headers=other_user["headers"], timeout=10, trust_env=False
    ) as client:
        yield client


@pytest.fixture
async def company(api):
    """Create a company whose initial creator is Alice."""
    response = await api.post("/v1/companies", json={"name": "Example company"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def membership(api, company, other_user, request):
    """Add Bob to Alice's company with the requested role (ordinary user by default)."""
    response = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": getattr(request, "param", "user"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def token_ownership(api, user):
    """Create an empty personal token ownership, independent of the login token."""
    response = await api.post("/v1/token-ownerships", json={"user_id": user["id"]})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def access_token(api, token_ownership, request):
    """Issue a personal access token with parametrizable scopes and lifetime."""
    payload = {
        "ownership_id": token_ownership["id"],
        "name": "CI",
        "scopes": ["read", "manage", "execute"],
    }
    payload.update(getattr(request, "param", {}))
    response = await api.post("/v1/access-tokens", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def company_token(api, company):
    """Issue a corporate token with all scopes, without granting personal rights."""
    response = await api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201, response.text
    response = await api.post(
        "/v1/access-tokens",
        json={
            "ownership_id": response.json()["id"],
            "name": "Company CI",
            "scopes": ["read", "manage", "execute"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def computer(api, request):
    """Register Alice's computer with independently configurable capabilities and capacity."""
    data = {
        "name": "worker",
        "os": "linux",
        "architecture": "amd64",
        "runtime": "oci",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
    }
    data.update(getattr(request, "param", {}))
    response = await api.post("/v1/computers", json=data)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def agent(api, client, computer):
    """Enroll the computer and establish a clean agent session through HTTP."""
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    handshake = {"clean_start": True, "previous_session_id": None}
    key = str(uuid4())
    response = await client.post(
        "/agent/v1/session", headers={**headers, "Idempotency-Key": key}, json=handshake
    )
    assert response.status_code == 200, response.text
    session = response.json()
    return {
        "headers": {**headers, "X-Agent-Session": session["session_id"]},
        "session": session,
        "handshake": handshake,
        "key": key,
        "computer": computer,
    }


@pytest.fixture
async def agent_api(server, agent):
    """Open a separate native HTTP client authenticated as the enrolled agent."""
    async with AsyncClient(
        base_url=server, headers=agent["headers"], timeout=10, trust_env=False
    ) as client:
        yield client


@pytest.fixture
async def other_agent(other_api, client):
    """Enroll Bob's worker for tests that cross both owner and agent boundaries."""
    response = await other_api.post(
        "/v1/computers",
        json={
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    computer = response.json()
    response = await other_api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"clean_start": True},
    )
    assert response.status_code == 200, response.text
    return {
        "computer": computer,
        "headers": {**headers, "X-Agent-Session": response.json()["session_id"]},
    }


@pytest.fixture
async def artifact(api):
    """Upload opaque initial-state bytes into real artifact storage."""
    response = await api.post(
        "/v1/artifacts", params={"filename": "state.tar"}, content=b"initial state"
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def isolate_payload(artifact, request):
    """Supply a valid isolate request whose initial state belongs to Alice."""
    payload = {
        "state_artifact_id": artifact["id"],
        "image": "python:3.12",
        "os": "linux",
        "architecture": "amd64",
        "runtime": "oci",
        "cpu_millis": 1000,
        "memory_bytes": 1024,
        "disk_bytes": 4096,
    }
    payload.update(getattr(request, "param", {}))
    return payload


@pytest.fixture
async def provisioning_isolate(api, isolate_payload):
    """Request an isolate without claiming or acknowledging its create operation."""
    response = await api.post(
        "/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=isolate_payload
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.fixture
async def create_task(agent_api, provisioning_isolate):
    """Wait via agent long polling for the real controller to allocate the isolate."""
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2, "limit": 1})
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["kind"] == "create", tasks
    assert tasks[0]["isolate_id"] == provisioning_isolate["id"]
    return tasks[0]


@pytest.fixture
async def isolate(api, agent_api, create_task):
    """Confirm actual provisioning through the agent API and return the ready isolate."""
    response = await agent_api.post(f"/agent/v1/tasks/{create_task['id']}/complete", json={})
    assert response.status_code == 200, response.text
    response = await api.get(f"/v1/isolates/{create_task['isolate_id']}")
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    return response.json()


@pytest.fixture
async def command(api, isolate, request):
    """Submit a user command without pretending it has already started."""
    payload = {"command": "pytest -q"}
    payload.update(getattr(request, "param", {}))
    response = await api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.fixture
async def run_task(agent_api, command):
    """Receive the queued run task through the real agent long-poll endpoint."""
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2, "limit": 1})
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["kind"] == "run", tasks
    assert tasks[0]["command_id"] == command["id"]
    return tasks[0]


@pytest.fixture
async def running_command(agent_api, command, run_task):
    """Notify the API that the delivered command has started on its worker."""
    response = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/started")
    assert response.status_code == 200, response.text
    return {"command": command, "task": response.json()}


@pytest.fixture
async def control_task(api, agent_api, create_task, request):
    """Prepare one requested create, snapshot, cancel or destroy agent operation."""
    kind = getattr(request, "param", "create")
    if kind == "create":
        return create_task
    response = await agent_api.post(f"/agent/v1/tasks/{create_task['id']}/complete", json={})
    assert response.status_code == 200, response.text
    isolate_id = create_task["isolate_id"]
    if kind == "snapshot":
        response = await api.post(
            f"/v1/isolates/{isolate_id}/snapshots", headers={"Idempotency-Key": str(uuid4())}
        )
    elif kind == "destroy":
        response = await api.delete(f"/v1/isolates/{isolate_id}")
    else:
        response = await api.post(
            f"/v1/isolates/{isolate_id}/commands",
            headers={"Idempotency-Key": str(uuid4())},
            json={"command": "pytest"},
        )
        assert response.status_code == 202, response.text
        command = response.json()
        response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
        assert response.status_code == 200, response.text
        assert response.json()["tasks"][0]["command_id"] == command["id"]
        response = await api.post(f"/v1/commands/{command['id']}/cancel")
    assert response.status_code == 202, response.text
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    return next(task for task in response.json()["tasks"] if task["kind"] == kind)


@pytest.fixture
async def secondary_client(settings, clock, server):
    """Run an independent API/controller instance against the same persistent database and storage."""
    application = create_app(settings, clock=lambda: clock.now)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    instance = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False))
    instance.capture_signals = nullcontext
    task = asyncio.create_task(instance.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not instance.started:
                if task.done():
                    await task
                    raise RuntimeError("Secondary HTTP server exited before startup")
                await asyncio.sleep(0.01)
        async with AsyncClient(
            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=10, trust_env=False
        ) as client:
            yield client
    finally:
        instance.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            listener.close()


@pytest.fixture
async def company_agent(api, client, company):
    """Enroll a worker belonging exclusively to the company pool."""
    response = await api.post(
        "/v1/computers",
        json={
            "company_id": company["id"],
            "name": "company worker",
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    computer = response.json()
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"clean_start": True},
    )
    assert response.status_code == 200, response.text
    return {
        "computer": computer,
        "headers": {**headers, "X-Agent-Session": response.json()["session_id"]},
    }


@pytest.fixture
async def foreign_command(other_api, client, other_agent):
    """Create an unclaimed command in Bob's separate resource pool."""
    response = await other_api.post("/v1/artifacts", content=b"foreign initial state")
    assert response.status_code == 201, response.text
    response = await other_api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "state_artifact_id": response.json()["id"],
            "image": "python:3.12",
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 202, response.text
    isolate = response.json()
    response = await client.post(
        "/agent/v1/tasks/claim", headers=other_agent["headers"], json={"wait_seconds": 2}
    )
    assert response.status_code == 200, response.text
    task = response.json()["tasks"][0]
    response = await client.post(
        f"/agent/v1/tasks/{task['id']}/complete", headers=other_agent["headers"], json={}
    )
    assert response.status_code == 200, response.text
    response = await other_api.post(
        f"/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "pytest"},
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.fixture
async def late_agent(api, client, computer, provisioning_isolate, clock):
    """Make capacity appear only after most of the provisioning deadline has elapsed."""
    from datetime import timedelta

    clock.now += timedelta(seconds=2)
    response = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert response.status_code == 201, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    response = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"clean_start": True},
    )
    assert response.status_code == 200, response.text
    return {**headers, "X-Agent-Session": response.json()["session_id"]}


@pytest.fixture
async def upload_connection(server):
    """Open a raw HTTP connection for interrupted-upload transport scenarios."""
    from urllib.parse import urlsplit

    address = urlsplit(server)
    reader, writer = await asyncio.open_connection(address.hostname, address.port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.fixture
async def cli_server(tmp_path):
    """Launch the installed CLI in its own process with fresh storage and an automatically migrated database."""
    import sys

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("THRONG_")}
    environment.update(
        {
            "THRONG_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/cli/throng.db",
            "THRONG_STORAGE_PATH": str(tmp_path / "cli" / "artifacts"),
            "THRONG_CONTROLLER_INTERVAL": "0.01",
        }
    )
    log = tmp_path / "cli-server.log"
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "throng_api.cli",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            env=environment,
            stdout=output,
            stderr=output,
        )
        try:
            async with AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False
            ) as client:
                import httpx

                async with asyncio.timeout(20):
                    while True:
                        if process.returncode is not None:
                            raise RuntimeError(log.read_text())
                        try:
                            response = await client.get("/health/ready")
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.02)
                yield client
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
