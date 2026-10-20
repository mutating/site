"""Keep-alive covers the actual authorization, JIT and readiness request chain."""

from datetime import timedelta

import httpx
import pytest

from throng_api import ci_controller

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import provision_only, reserve, schedule
from .test_ci import route as route

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: registration/readiness each own exactly one HTTP connection pool,
# but still perform all fresh App/user/scoped-repository/run/runner checks and
# token revocations. Only an online idle runner with a started command is selected.
async def test_controller_reuses_connections_without_skipping_authorization(
    client, route, ci_peer, powercell, app, clock
):
    """Reuse controller HTTP connections while checking each operation's current authorization."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    service = app.state.github.client
    delegate, closed, calls = service._transport, [], []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append((request.method, request.url.path))
            return await delegate.handle_async_request(request)

        async def aclose(self):
            closed.append(True)

    service._transport = Transport()
    try:
        await ci_controller.run_once(app)
        assert closed == [True]
        assert calls[:5] == [
            ("GET", "/app/installations/456"),
            ("GET", "/repositories/42"),
            ("POST", "/app/installations/456/access_tokens"),
            ("GET", "/repositories/42"),
            ("DELETE", "/installation/token"),
        ]
        assert calls[-1] == ("DELETE", "/installation/token")
        await schedule(app, clock)
        [run] = (
            await powercell.client.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
        ).json()["tasks"]
        assert run["kind"] == "run"
        assert (
            await powercell.client.post(f"/agent/v1/tasks/{run['id']}/started", json={})
        ).status_code == 200
        ci_peer.online = True
        clock.now += timedelta(seconds=5)
        calls.clear()
        await ci_controller.run_once(app)
        assert closed == [True, True]
        assert calls[:5] == [
            ("GET", "/app/installations/456"),
            ("GET", "/repositories/42"),
            ("POST", "/app/installations/456/access_tokens"),
            ("GET", "/repositories/42"),
            ("DELETE", "/installation/token"),
        ]
        assert calls[-3:] == [
            ("GET", "/repos/test-org/repo/actions/runs/1234/attempts/1"),
            ("GET", "/repos/test-org/repo/actions/runners/55"),
            ("DELETE", "/installation/token"),
        ]
        result = (await reserve(client, route, ci_peer)).json()
        assert result["status"] == "ready"
    finally:
        service._transport = delegate


# Assertions: a provider that times out after earlier successful pooled requests
# still fails closed, closes the pool, and never registers a JIT runner.
async def test_timeout_on_a_reused_connection_fails_closed(
    client, route, ci_peer, powercell, app, clock
):
    """Reject a reservation when a reused GitHub connection times out."""
    await provision_only(client, route, ci_peer, powercell, app, clock)
    service = app.state.github.client
    delegate, closed, calls = service._transport, [], []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request.url.path)
            if request.method == "POST" and request.url.path.endswith("/access_tokens"):
                raise httpx.ReadTimeout("fixture-private-diagnostic", request=request)
            return await delegate.handle_async_request(request)

        async def aclose(self):
            closed.append(True)

    service._transport = Transport()
    try:
        await ci_controller.run_once(app)
        assert calls == [
            "/app/installations/456",
            "/repositories/42",
            "/app/installations/456/access_tokens",
        ]
        assert closed == [True] and ci_peer.jit_count == 0
        result = (await reserve(client, route, ci_peer)).json()
        assert result["status"] == "failed" and result["reason"] == "github_unavailable"
    finally:
        service._transport = delegate
