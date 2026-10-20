"""Wire-level workflow harness: real generated bootstrap, API, DB and signed agent.

GitHub's OIDC issuer/API and VM execution are peers. No platform service,
bootstrap decision, scheduler, persistence or authorization function is mocked.
"""

import asyncio
import hashlib
import hmac
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import uvicorn
import yaml
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from tests.platform.api.e2e.test_github import connect
from throng_api import ci_controller
from throng_api.app import create_app
from throng_api.models import AgentTask, CIReservation, Command, Computer, Isolate

from .test_ci import schedule


class WorkflowChain:
    def __init__(self, api, client, app, clock, github, peer, tmp_path):
        self.api, self.client, self.app, self.clock = api, client, app, clock
        self.github, self.peer, self.tmp_path = github, peer, tmp_path
        self.agent = None
        self.trace = []
        self.tasks = []
        self.outputs = []
        self.credentials = [
            "test-private-jit-config",
            "user-token",
            "refresh-token",
            "client-secret",
            "scoped-installation-token",
        ]
        self.attempt = 1
        self.online = True
        self.create_failure = False
        self.lose_response = False
        self.offline_api = False
        self.watch_failures = []
        self.watch_retry_after = None
        self.oidc_failures = []
        self.on_pump = None
        self.oidc_claims = {}
        self.job = None
        self.jobs = {}
        transport = github.service.client._transport

        async def jobs(request):
            """Expose completed jobs with their original attempt and runner identity."""
            prefix = "/repos/test-org/repo/actions/runs/1234/attempts/"
            if (
                request.method == "GET"
                and request.url.path.startswith(prefix)
                and request.url.path.endswith("/jobs")
            ):
                attempt = int(request.url.path.split("/")[-2])
                job = self.jobs.get(attempt)
                rows = [job] if job else []
                return httpx.Response(200, json={"total_count": len(rows), "jobs": rows})
            return await transport.handle_async_request(request)

        github.service.client._transport = httpx.MockTransport(jobs)

        # The issuer uses the real loopback HTTP server, reached only by the
        # test's DNS/HTTPS boundary adapter after bootstrap URL validation.
        async def oidc(request: Request):
            assert request.headers["authorization"] == "Bearer workflow-request-token"
            assert request.query_params["audience"] == github.service.oidc.audience
            self.trace.append("oidc")
            if self.oidc_failures:
                return JSONResponse(
                    {"detail": "private-issuer-error"},
                    status_code=self.oidc_failures.pop(0),
                    headers={"Retry-After": "120"},
                )
            token = peer.token(run_attempt=str(self.attempt), **self.oidc_claims)
            self.credentials.append(token)
            return {"value": token}

        app.add_api_route("/__test__/github-oidc", oidc)

        async def gateway_failure():
            status = self.watch_failures.pop(0)
            if status == 200:
                # Real EOF before Content-Length: urllib raises IncompleteRead,
                # a transport fault rather than a valid malformed JSON response.
                return Response(b"{", headers={"Content-Length": "100"})
            return JSONResponse(
                {"detail": "private-gateway-error"},
                status_code=status,
                headers={"Retry-After": self.watch_retry_after} if self.watch_retry_after else None,
            )

        app.add_api_route("/__test__/watch-gateway", gateway_failure, methods=["POST"])

    async def bind(self, **policy):
        """Authorize, discover the selected private repo and save a binding over HTTP."""
        await self.api.delete("/v1/github/connection")
        assert (await self.api.get("/v1/github/status")).json()["account"] is None
        await connect(self.api)
        assert (await self.api.get("/v1/github/status")).json()["account"]["id"] == 789
        self.github.peer.repository_response = {
            "total_count": 1,
            "repositories": [
                {
                    "id": 42,
                    "full_name": "test-org/repo",
                    "private": True,
                    "default_branch": "main",
                    "permissions": {"admin": True},
                }
            ],
        }
        installations = (await self.api.get("/v1/github/installations")).json()
        assert [row["id"] for row in installations["installations"]] == [456]
        repositories = (await self.api.get("/v1/github/installations/456/repositories")).json()
        assert repositories["repositories"][0]["can_administer"] is True
        response = await self.api.post(
            "/v1/github/routes",
            json={
                "installation_id": 456,
                "repository_id": 42,
                "workflow_path": ".github/workflows/ci.yml",
                "job_key": "test",
                "cpu_millis": 1000,
                "memory_bytes": 2 * 1024**3,
                "disk_bytes": 4 * 1024**3,
                "preparation_seconds": 10,
                "timeout_seconds": 60,
                **policy,
            },
        )
        assert response.status_code == 201, response.text
        self.route = response.json()
        workflow = await self.api.get(f"/v1/github/routes/{self.route['id']}/workflow")
        assert workflow.status_code == 200
        self.yaml = workflow.json()["yaml"]
        # Parse the delivered YAML, including indentation and env quoting. The
        # base loader keeps GitHub's `on` key rather than treating it as YAML 1.1 true.
        document = yaml.load(self.yaml, Loader=yaml.BaseLoader)
        assert document["on"]["push"]["branches"] == [self.route["ref"].removeprefix("refs/heads/")]
        assert "workflow_dispatch" in document["on"]
        router_job = "powercell_route_" + self.route["id"].replace("-", "")
        routing = document["jobs"][router_job]
        assert routing["runs-on"] == "ubuntu-24.04-arm"
        assert routing["permissions"] == {"id-token": "write"}
        assert int(routing["timeout-minutes"]) * 60 > self.route["preparation_seconds"] + 10
        self.routing_step = next(step for step in routing["steps"] if step["id"] == "route")
        assert self.routing_step["shell"] == "python"
        job = document["jobs"][self.route["job_key"]]
        assert job["needs"] == router_job
        assert routing["outputs"]["run_attempt"] == "${{ github.run_attempt }}"
        workload = next(
            row for row in job["strategy"]["matrix"]["include"] if row["role"] == "workload"
        )
        assert job["runs-on"] == "${{ fromJSON(matrix.runs_on) }}"
        assert workload["runs_on"] == (
            "${{ needs." + router_job + ".outputs.run_attempt == github.run_attempt "
            "&& needs."
            + router_job
            + ".outputs.runs_on || '"
            + "Re-run the routing job or all jobs to refresh Power Cell selection"
            + "' }}"
        )
        self.guard_step = next(
            step for step in job["steps"] if step.get("env", {}).get("POWERCELL_MODE") == "watch"
        )
        self.trace.extend(["oauth", "discover", "bind", "workflow"])
        return self.route

    async def state(self, instance="default"):
        async with self.app.state.db.read() as session:
            row = await session.scalar(
                select(CIReservation).where(
                    CIReservation.route_id == self.route["id"],
                    CIReservation.run_attempt == self.attempt,
                    CIReservation.instance == instance,
                )
            )
            return (
                None
                if row is None
                else SimpleNamespace(
                    **{
                        column.key: getattr(row, column.key)
                        for column in CIReservation.__table__.columns
                    }
                )
            )

    async def deliver_tasks(self):
        if not self.agent:
            return
        response = await self.agent.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
        assert response.status_code == 200, response.text
        for task in response.json()["tasks"]:
            self.tasks.append(task)
            kind, payload = task["kind"], task["payload"]
            self.trace.append("agent:" + kind)
            report = {}
            if kind == "create":
                assert payload["image"] == "ghcr.io/actions/actions-runner:latest"
                assert payload["isolation_mode"] == "vm" and payload["retry_allowed"] is False
                assert payload["network_enabled"] is True
                assert payload["cpu_millis"] == self.route["cpu_millis"]
                assert payload["memory_bytes"] == self.route["memory_bytes"]
                assert payload["disk_bytes"] == self.route["disk_bytes"]
                if self.create_failure:
                    report = {
                        "outcome": "failed",
                        "error": "mock image preparation failed",
                    }
            elif kind == "run":
                assert payload["env"] == {"POWERCELL_GITHUB_JIT_CONFIG": "test-private-jit-config"}
                assert payload["cwd"] == "." and "./run.sh --jitconfig" in payload["command"]
                started = await self.agent.client.post(
                    f"/agent/v1/tasks/{task['id']}/started", json={}
                )
                assert started.status_code == 200, started.text
                self.peer.online = self.online
                continue
            else:
                assert kind in {"cancel", "destroy"}, kind
            completed = await self.agent.client.post(
                f"/agent/v1/tasks/{task['id']}/complete", json=report
            )
            if kind == "cancel" and completed.status_code == 409:
                # A preceding destroy in the same claim supersedes cancellation.
                current = await self.agent.client.get(f"/agent/v1/tasks/{task['id']}")
                assert current.status_code == 200 and current.json()["status"] == "obsolete"
            else:
                assert completed.status_code == 200, (kind, completed.text)

    async def pump(self, seconds=2):
        # Keep the simulated Mac alive through the real signed heartbeat API.
        # Advance in small slices so tests do not accidentally expire its lease.
        if self.agent:
            remaining = seconds
            while remaining > 0:
                step = min(remaining, 2)
                self.clock.now += timedelta(seconds=step)
                response = await self.agent.client.post("/agent/v1/heartbeat")
                assert response.status_code == 200, response.text
                remaining -= step
        else:
            self.clock.now += timedelta(seconds=seconds)
        if self.on_pump:
            await self.on_pump()
        await schedule(self.app, self.clock)
        await self.deliver_tasks()
        await ci_controller.run_once(self.app)
        await schedule(self.app, self.clock)
        await self.deliver_tasks()

    async def bootstrap(self, instance="default", *, watch=False, watchdog=15):
        """Execute the exact embedded script; preserve its urllib serialization and HTTP errors."""
        step = self.guard_step if watch else self.routing_step
        script = step["run"]
        namespace = {"__name__": "generated_workflow"}
        exec(compile(script, "generated-workflow.py", "exec"), namespace)
        output = self.tmp_path / f"outputs-{len(self.outputs)}"
        environment = dict(step["env"])
        environment.update(
            POWERCELL_INSTANCE=instance,
            ACTIONS_ID_TOKEN_REQUEST_URL="https://pipelines.actions.githubusercontent.com/token?api-version=1",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="workflow-request-token",
        )
        if not watch:
            environment["GITHUB_OUTPUT"] = str(output)
        namespace["os"] = SimpleNamespace(environ=environment)
        loop = asyncio.get_running_loop()
        started = self.clock.now
        namespace["time"] = SimpleNamespace(
            monotonic=lambda: (self.clock.now - started).total_seconds(),
            sleep=lambda seconds: asyncio.run_coroutine_threadsafe(self.pump(seconds), loop).result(
                timeout=10
            ),
        )
        original = urllib.request.build_opener(namespace["NoRedirect"]())
        chain = self

        async def progress(waiting):
            # Real controllers and the Mac keep progressing while urllib waits.
            while waiting.is_set():
                await asyncio.sleep(0.01)
                if waiting.is_set():
                    await chain.pump(1)

        class Boundary:
            @contextmanager
            def open(self, request, timeout):
                parsed = urllib.parse.urlsplit(request.full_url)
                if parsed.hostname == "pipelines.actions.githubusercontent.com":
                    target = (
                        str(chain.client.base_url).rstrip("/")
                        + "/__test__/github-oidc?"
                        + parsed.query
                    )
                else:
                    assert request.full_url.startswith(
                        chain.github.service.api_origin + "/v1/github/ci/"
                    )
                    action = parsed.path.rsplit("/", 1)[1]
                    chain.trace.append("bootstrap:" + action)
                    if chain.offline_api and action == "reserve":
                        raise urllib.error.URLError("mock connection failure")
                    target = str(chain.client.base_url).rstrip("/") + parsed.path
                    if action == "watch" and chain.watch_failures:
                        target = str(chain.client.base_url).rstrip("/") + "/__test__/watch-gateway"
                    if parsed.query:
                        target += "?" + parsed.query
                forwarded = urllib.request.Request(
                    target,
                    data=request.data,
                    headers=dict(request.header_items()),
                    method=request.get_method(),
                )
                waiting = threading.Event()
                if parsed.path.endswith("/reserve") and parsed.query:
                    waiting.set()
                    pump = asyncio.run_coroutine_threadsafe(progress(waiting), loop)
                else:
                    pump = None
                try:
                    with original.open(forwarded, timeout=timeout) as response:
                        yield response  # Preserve the delivered reader's bounds and HTTP framing.
                finally:
                    waiting.clear()
                    if pump:
                        pump.result(timeout=10)
                if chain.lose_response and parsed.path.endswith("/reserve"):
                    chain.lose_response = False
                    raise urllib.error.URLError("mock lost acknowledgment")

        namespace["urllib"] = SimpleNamespace(
            parse=urllib.parse,
            error=urllib.error,
            request=SimpleNamespace(
                Request=urllib.request.Request, build_opener=lambda *args: Boundary()
            ),
        )
        self.outputs.append(output)
        await asyncio.wait_for(asyncio.to_thread(namespace["main"]), timeout=watchdog)
        if watch:
            assert not output.exists()
            return {}
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    async def webhook(self, event, action, item, *, delivery=None):
        body = json.dumps(
            {
                "action": action,
                "installation": {"id": 456},
                "repository": {"id": 42},
                event: item,
            }
        ).encode()
        response = await self.client.post(
            "/v1/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": event,
                "X-GitHub-Delivery": delivery or str(uuid4()),
                "X-Hub-Signature-256": "sha256="
                + hmac.new(
                    self.github.service.webhook_secret.encode(), body, hashlib.sha256
                ).hexdigest(),
            },
        )
        assert response.status_code == 202, response.text
        self.trace.append("webhook:" + event + ":" + action)

    async def start_job(self, outputs):
        """The GitHub peer dispatches to the exact committed label; it owns job logs/artifacts."""
        selection = json.loads(outputs["runs_on"])
        row = await self.state()
        assert row.status == "selected" and self.peer.registered and self.peer.online
        assert selection == [self.peer.label] == [row.label]
        assert set(selection) <= set(self.peer.labels)
        assert "self-hosted" not in self.peer.labels
        self.peer.busy = True
        runner_id, runner_name = 55, row.label
        self.job = {
            "id": 700,
            "run_id": 1234,
            "run_attempt": self.attempt,
            "runner_id": runner_id,
            "runner_name": runner_name,
            "status": "in_progress",
        }
        await self.webhook("workflow_job", "in_progress", self.job)

    async def finish_job(self, conclusion="success"):
        self.job.setdefault("id", 700)
        self.job["status"] = "completed"
        self.job["conclusion"] = conclusion
        self.jobs[self.job["run_attempt"]] = dict(self.job)
        await self.webhook("workflow_job", "completed", self.job)
        self.peer.run_status = "completed"
        await self.webhook(
            "workflow_run",
            "completed",
            {"id": 1234, "run_attempt": self.attempt, "conclusion": conclusion},
        )

    async def drain(self):
        for _ in range(10):
            await self.pump(10)
            row = await self.state()
            if row.cleaned_at:
                break
        # A historical runner ID may remain for exact matching of late events;
        # cleanup must remove the real registration and all reusable credentials.
        assert row.cleaned_at and row.credentials is None and not self.peer.registered
        async with self.app.state.db.read() as session:
            isolates = (await session.scalars(select(Isolate))).all()
            assert all(not isolate.reserved for isolate in isolates)
            for computer in (await session.scalars(select(Computer))).all():
                assert computer.reserved_disk == 0
                assert computer.reserved_cpu == 0 and computer.reserved_memory == 0
            # Runtime-only credentials never persist in commands or task payloads.
            for model in (Command, AgentTask):
                for item in (await session.scalars(select(model))).all():
                    stored = item.env if model is Command else item.payload
                    assert "test-private-jit-config" not in json.dumps(stored)

    @asynccontextmanager
    async def restarted_platform(self):
        """Start a fresh app/DB pool/crypto client and move all further HTTP to it.

        The original fixture stays quiescent with its controllers disabled. No
        in-memory CI state, database session or JWKS cache is reused by the new app.
        """
        configured = self.app.state.settings.model_copy(
            update={
                name: getattr(self.github.settings, name)
                for name in type(self.github.settings).model_fields
                if name.startswith("github_")
            }
        )
        application = create_app(configured, clock=lambda: self.clock.now)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False))
        server.capture_signals = nullcontext
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        saved = self.app, self.api, self.client, self.github, self.agent
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError("Restarted test platform failed to start")
                    await asyncio.sleep(0.01)
            application.state.github.client._transport = self.github.service.client._transport
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            async with (
                httpx.AsyncClient(base_url=origin, trust_env=False) as client,
                httpx.AsyncClient(
                    base_url=origin, headers=self.api.headers, trust_env=False
                ) as api,
                httpx.AsyncClient(
                    base_url=origin,
                    headers=self.agent.client.headers,
                    auth=self.agent.client.auth,
                    trust_env=False,
                ) as agent,
            ):
                self.app, self.api, self.client = application, api, client
                self.github = SimpleNamespace(
                    service=application.state.github,
                    peer=self.github.peer,
                    settings=configured,
                )
                self.agent = SimpleNamespace(client=agent, computer_id=self.agent.computer_id)
                yield
        finally:
            self.app, self.api, self.client, self.github, self.agent = saved
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
            listener.close()
