"""The exact generated hosted guard observes the real HTTP/DB/agent lifecycle.

GitHub's matrix cancellation is verified separately on real Actions: these tests
exercise the decision and exit status that trigger that native mechanism.
"""

import pytest

from .test_ci_chain import chain as chain
from .test_ci_chain import ci_peer as ci_peer
from .test_ci_chain import github as github
from .test_ci_chain import powercell as powercell

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: the delivered guard tolerates a real HTTP 503 once, then sees the
# exact signed job-start event; it never reserves, publishes a label or cancels.
@pytest.mark.parametrize("gateway_failure", [False, True])
async def test_generated_guard_observes_start_through_real_http(chain, powercell, gateway_failure):
    """A transient gateway failure cannot turn observation into allocation or cancellation."""
    await chain.bind()
    chain.agent = powercell
    selection = await chain.bootstrap()
    before = await chain.state()
    chain.watch_failures = [503] if gateway_failure else []
    offset = len(chain.trace)

    async def start():
        chain.on_pump = None
        await chain.start_job(selection)

    chain.on_pump = start
    assert await chain.bootstrap(watch=True) == {}
    assert chain.trace[offset:].count("bootstrap:watch") == 2
    assert "bootstrap:reserve" not in chain.trace[offset:]
    assert "bootstrap:cancel" not in chain.trace[offset:]
    assert (await chain.state()).id == before.id and chain.peer.jit_count == 1
    await chain.finish_job()
    await chain.drain()


# Assertions: if the selected runner never receives its job, the real guard exits
# nonzero when the platform lease expires; VM/runner/credentials clean normally.
async def test_generated_guard_fails_after_selection_expires_without_job_start(chain, powercell):
    """End an unserviceable selection without pretending the GitHub queue completed itself."""
    await chain.bind()
    chain.agent = powercell
    await chain.bootstrap()
    # Exercise expiry at its boundary rather than spending the harness watchdog
    # replaying hundreds of otherwise identical healthy heartbeat/poll cycles.
    from datetime import timedelta

    from throng_api.models import CIReservation

    selected = await chain.state()
    async with chain.app.state.db.write() as session:
        row = await session.get(CIReservation, selected.id)
        row.expires_at = chain.clock.now + timedelta(seconds=2)
    offset = len(chain.trace)
    with pytest.raises(RuntimeError, match="runner_timeout"):
        await chain.bootstrap(watch=True)
    row = await chain.state()
    assert row.status == "timed_out" and chain.job is None
    assert "bootstrap:reserve" not in chain.trace[offset:]
    assert "bootstrap:cancel" not in chain.trace[offset:]
    await chain.drain()


# Assertions: a permanent identity refusal fails immediately instead of silently
# retrying or exposing another reservation, while the original selection remains unchanged.
async def test_generated_guard_rejects_foreign_oidc_without_touching_selection(chain, powercell):
    """The observer keeps the same repository/attempt authorization boundary as reserve."""
    await chain.bind()
    chain.agent = powercell
    await chain.bootstrap()
    before = await chain.state()
    chain.oidc_claims = {"repository_id": "999"}
    offset = len(chain.trace)
    with pytest.raises(RuntimeError, match="routing unavailable"):
        await chain.bootstrap(watch=True)
    after = await chain.state()
    assert after.status == before.status == "selected" and after.expires_at == before.expires_at
    assert chain.trace[offset:] == ["oidc", "bootstrap:watch"]
    # GitHub owns the sibling cancellation. Its authenticated completion event
    # supplies normal cleanup, independently of the rejected observer identity.
    await chain.webhook(
        "workflow_run", "completed", {"id": 1234, "run_attempt": 1, "conclusion": "failure"}
    )
    await chain.drain()


# Assertions: Retry-After survives the actual HTTP gateway boundary and spaces
# retries, while successful observation still uses the existing reservation.
async def test_generated_guard_honors_gateway_cooldown_over_real_http(chain, powercell):
    """Respect a gateway cooldown without another reservation or a change to resource policy."""
    from datetime import timedelta

    await chain.bind()
    chain.agent = powercell
    selection = await chain.bootstrap()
    before = chain.clock.now
    chain.watch_failures = [503]
    chain.watch_retry_after = "3"

    async def start():
        chain.on_pump = None
        await chain.start_job(selection)

    chain.on_pump = start
    assert await chain.bootstrap(watch=True) == {}
    assert chain.clock.now - before == timedelta(seconds=3)
    await chain.finish_job()
    await chain.drain()


# Assertions: when proactive renewal encounters a real issuer HTTP 503, the
# previously issued signed JWT remains valid at the API and observes the start.
async def test_generated_guard_observes_start_while_oidc_refresh_is_unavailable(chain, powercell):
    """An issuer outage must not hide an already running job from a valid observer identity."""
    from datetime import timedelta

    await chain.bind(timeout_seconds=300)
    chain.agent = powercell
    selection = await chain.bootstrap()
    started = chain.clock.now

    async def progress():
        elapsed = chain.clock.now - started
        if elapsed >= timedelta(seconds=120):
            chain.oidc_failures = [503] * 200
        if elapsed >= timedelta(seconds=122):
            chain.on_pump = None
            await chain.start_job(selection)

    chain.on_pump = progress
    assert await chain.bootstrap(watch=True) == {}
    assert chain.clock.now - started == timedelta(seconds=122)
    assert (await chain.state()).status == "running"
    await chain.finish_job()
    await chain.drain()


# Assertions: an EOF inside an HTTP response is retried as transport failure,
# whereas a complete invalid JSON document remains an immediate refusal.
async def test_generated_guard_recovers_a_truncated_http_response(chain, powercell):
    """A real broken Content-Length response cannot cancel a healthy workload sibling."""
    await chain.bind()
    chain.agent = powercell
    selection = await chain.bootstrap()
    chain.watch_failures = [200]  # Gateway sends one byte then closes a 100-byte body.
    offset = len(chain.trace)

    async def start():
        chain.on_pump = None
        await chain.start_job(selection)

    chain.on_pump = start
    assert await chain.bootstrap(watch=True) == {}
    assert chain.trace[offset:].count("bootstrap:watch") == 2
    assert (
        "bootstrap:reserve" not in chain.trace[offset:]
        and "bootstrap:cancel" not in chain.trace[offset:]
    )
    await chain.finish_job()
    await chain.drain()
