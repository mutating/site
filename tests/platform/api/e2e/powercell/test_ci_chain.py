"""Complete workflow contracts, declared before each scenario's implementation."""

import json
from datetime import timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from sqlalchemy import select

from throng_api import ci_controller
from throng_api.models import CIReservation, Isolate

from .ci_chain_peer import WorkflowChain
from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci import schedule

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


@pytest.fixture
def chain(api, client, app, clock, github, ci_peer, tmp_path, capsys, caplog):
    workflow = WorkflowChain(api, client, app, clock, github, ci_peer, tmp_path)
    yield workflow
    captured = capsys.readouterr()
    exposed = captured.out + captured.err + caplog.text + getattr(workflow, "yaml", "")
    exposed += "".join(path.read_text() for path in workflow.outputs if path.exists())
    for credential in workflow.credentials:
        assert credential not in exposed


# Assertions: OAuth/discovery/binding/workflow feed the REAL embedded bootstrap;
# only an online, started runner is committed; the assigned agent receives the
# requested image/resources/JIT; signed job events update history; every outcome
# links to the GitHub result, deletes runner/VM and releases all reservations.
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
async def test_complete_powercell_workflow(chain, powercell, conclusion):
    await chain.bind()
    chain.agent = powercell
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "powercell"
    assert chain.trace[:5] == ["oauth", "discover", "bind", "workflow", "oidc"]
    assert (
        chain.trace.index("agent:create")
        < chain.trace.index("agent:run")
        < chain.trace.index("bootstrap:commit")
    )
    selected = await chain.state()
    assert selected.status == "selected" and "test-private-jit-config" not in selected.credentials
    isolate = (await chain.api.get(f"/v1/isolates/{selected.isolate_id}")).json()
    assert isolate["computer_id"] == powercell.computer_id and isolate["image"].startswith(
        "ghcr.io/actions/actions-runner:"
    )
    await chain.start_job(outputs)
    running = await chain.state()
    assert running.status == "running" and running.expires_at == chain.clock.now + timedelta(
        seconds=60
    )
    await chain.finish_job(conclusion)
    history = (await chain.api.get(f"/v1/github/routes/{chain.route['id']}/runs")).json()
    assert len(history) == 1
    assert history[0]["status"] == ("cancelled" if conclusion == "cancelled" else "completed")
    assert history[0]["reason"] == "github_" + conclusion
    assert history[0]["url"] == "https://github.com/test-org/repo/actions/runs/1234"
    assert "test-private-jit-config" not in json.dumps(history)
    run = next(task for task in chain.tasks if task["kind"] == "run")
    response = await powercell.client.post(
        f"/agent/v1/tasks/{run['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 200
    await chain.drain()
    assert chain.peer.deletes == 1
    assert [task["kind"] for task in chain.tasks].count("destroy") == 1


# Assertions: no registered computer yields a bounded hosted choice, no JIT or
# commit, one reservation despite polls; hosted job events cannot invent a local VM.
async def test_complete_hosted_fallback_without_a_mac(chain):
    await chain.bind()
    outputs = await chain.bootstrap()
    assert outputs == {"runs_on": '"ubuntu-24.04-arm"', "backend": "github"}
    assert chain.clock.now - (await chain.state()).deadline < timedelta(seconds=5)
    assert "bootstrap:commit" not in chain.trace and chain.peer.jit_count == 0
    await chain.start_job(outputs)
    await chain.finish_job()
    await chain.drain()
    async with chain.app.state.db.read() as session:
        assert len((await session.scalars(select(CIReservation))).all()) == 1
        assert await session.scalar(select(Isolate).where(Isolate.reserved.is_(True))) is None
    assert (await chain.state()).reason == "no_capacity"


# Assertions: image preparation failure, an offline runner and failed GitHub JIT
# each select hosted fallback without committing a local runner; late preparation
# cannot leak credentials or retain resources after confirmed destruction.
@pytest.mark.parametrize("failure", ["image", "offline", "github"])
async def test_preparation_failure_reaches_fallback_and_cleans_up(chain, powercell, failure):
    await chain.bind()
    chain.agent = powercell
    chain.create_failure = failure == "image"
    chain.online = failure != "offline"
    if failure == "github":
        chain.peer.failures[("POST", "/repos/test-org/repo/actions/runners/generate-jitconfig")] = (
            503
        )
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "github" and "bootstrap:commit" not in chain.trace
    if failure == "image":
        assert chain.peer.jit_count == 0 and not any(task["kind"] == "run" for task in chain.tasks)
    if failure == "github":
        assert (await chain.state()).reason == "github_unavailable"
        chain.peer.failures.clear()
    await chain.start_job(outputs)
    await chain.finish_job()
    await chain.drain()


# Assertions: a lost HTTP acknowledgment AFTER a real reserve/commit triggers cancel;
# bootstrap outputs hosted selection, and the committed local runner is destroyed.
@pytest.mark.parametrize("operation", ["reserve", "commit"])
async def test_lost_response_never_leaves_two_eligible_runners(chain, powercell, operation):
    await chain.bind()
    chain.agent = powercell
    chain.lost_response = operation
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "github"
    assert chain.trace.index("bootstrap:" + operation) < chain.trace.index("bootstrap:cancel")
    assert (await chain.state()).status == "fallback"
    await chain.drain()
    assert not chain.peer.registered and chain.peer.deletes == (1 if operation == "commit" else 0)


# Assertions: failure to reach reserve still sends a real cancellation tombstone;
# the hosted decision does not require a platform secret or create any isolate.
async def test_api_outage_before_reserve_creates_only_a_cancellation_tombstone(chain):
    await chain.bind()
    chain.offline_api = True
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "github" and chain.trace[-1] == "bootstrap:cancel"
    row = await chain.state()
    assert row.reason == "bootstrap_cancelled" and row.isolate_id is None and row.cleaned_at
    assert chain.peer.jit_count == 0


# Assertions: fallback=none produces no runs-on output and never starts any hosted
# job; expiry still clears the local provisioning record and its resource claim.
async def test_no_fallback_policy_fails_the_generated_routing_job(chain):
    await chain.bind(fallback="none")
    with pytest.raises(RuntimeError, match="CI reservation failed"):
        await chain.bootstrap()
    assert not chain.outputs[0].exists() and chain.job is None
    await chain.drain()


# Assertions: cancellation during preparation prevents commit/output, and signed
# workflow completion causes cleanup even when the runner never became ready.
async def test_workflow_cancelled_during_preparation_stops_the_whole_chain(chain, powercell):
    await chain.bind()
    chain.agent = powercell

    async def cancel():
        chain.on_pump = None
        await chain.webhook(
            "workflow_run",
            "completed",
            {"id": 1234, "run_attempt": 1, "conclusion": "cancelled"},
        )

    chain.on_pump = cancel
    with pytest.raises(RuntimeError, match="CI reservation failed"):
        await chain.bootstrap()
    assert not chain.outputs[0].exists() and "bootstrap:commit" not in chain.trace
    await chain.drain()
    assert (await chain.state()).status == "cancelled"


# Assertions: a fresh controller loop can recover selected/running state entirely
# from DB and GitHub polling; missing webhooks do not prevent timeout/cleanup.
async def test_missing_start_webhook_is_recovered_then_job_timeout_cleans_up(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    await chain.bootstrap()
    chain.peer.busy = True
    await chain.pump(5)
    assert (await chain.state()).status == "running"
    await chain.pump(61)
    assert (await chain.state()).status == "timed_out"
    await chain.drain()
    assert not chain.peer.registered


# Assertions: completing an attempt and rerunning the workflow creates a fresh
# reservation/label/JIT, while history keeps both attempts and the old VM is gone.
async def test_rerun_after_cleanup_gets_a_fresh_runner_and_keeps_attempt_history(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    first = await chain.bootstrap()
    await chain.start_job(first)
    await chain.finish_job()
    await chain.drain()
    chain.attempt = chain.peer.run_attempt = 2
    chain.peer.run_status = "in_progress"
    chain.peer.online = chain.peer.busy = False
    second = await chain.bootstrap()
    assert json.loads(first["runs_on"])[1] != json.loads(second["runs_on"])[1]
    assert chain.peer.jit_count == 2
    await chain.start_job(second)
    await chain.finish_job()
    await chain.drain()
    history = (await chain.api.get(f"/v1/github/routes/{chain.route['id']}/runs")).json()
    assert {row["run_attempt"] for row in history} == {1, 2}
    assert all(row["status"] == "completed" for row in history)


# Assertions: even a genuinely signed OIDC identity for another repository may
# only choose hosted fallback; reserve/cancel allocate no foreign resources.
async def test_foreign_workflow_identity_cannot_enter_the_powercell_chain(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    chain.oidc_claims = {"repository_id": "999"}
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "github" and await chain.state() is None
    assert chain.peer.jit_count == 0 and not chain.tasks


# Assertions: a fresh HTTP application and DB pool recover a committed runner,
# retain OAuth encryption/agent identity, observe job start via polling without
# webhook, and finish/clean up the same reservation without a second registration.
async def test_new_platform_instance_recovers_the_committed_workflow(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    outputs = await chain.bootstrap()
    before = await chain.state()
    async with chain.restarted_platform():
        assert (await chain.client.get("/health/ready")).status_code == 200
        account = (await chain.api.get("/v1/github/status")).json()["account"]
        assert account["id"] == 789
        assert (await chain.state()).id == before.id
        chain.peer.busy = True
        await chain.pump(5)
        assert (await chain.state()).status == "running"
        # GitHub routes the same committed label; no second JIT config is issued.
        chain.job = {
            "run_id": 1234,
            "run_attempt": 1,
            "runner_id": 55,
            "runner_name": json.loads(outputs["runs_on"])[1],
        }
        await chain.finish_job()
        await chain.drain()
        assert chain.peer.jit_count == 1 and chain.peer.deletes == 1
    assert (await chain.state()).cleaned_at


# Assertions: losing the Mac after job start retains occupied resources until
# the signed agent confirms a clean restart; reconnecting releases them and
# cleans the GitHub registration, without replaying the old runner command.
async def test_lost_mac_reconnects_cleanly_without_replaying_a_ci_job(chain, powercell):
    await chain.bind(timeout_seconds=120)
    chain.agent = powercell
    outputs = await chain.bootstrap()
    await chain.start_job(outputs)
    chain.agent = None
    chain.clock.now += timedelta(seconds=61)
    await schedule(chain.app, chain.clock)
    await ci_controller.run_once(chain.app)
    row = await chain.state()
    async with chain.app.state.db.read() as session:
        isolate = await session.get(Isolate, row.isolate_id)
        assert isolate.reserved and not row.cleaned_at and row.status == "failed"
    current = await powercell.client.get("/agent/v1/session")
    assert current.status_code == 200 and current.json()["active"] is False
    response = await powercell.client.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": "chain-reconnect"},
        json={"clean_start": True, "previous_session_id": current.json()["session_id"]},
    )
    assert response.status_code == 200, response.text
    powercell.client.headers["X-Agent-Session"] = response.json()["session_id"]
    chain.agent = powercell
    await chain.drain()
    assert len([task for task in chain.tasks if task["kind"] == "run"]) == 1


# Assertions: a transport retry of generated bootstrap uses the existing
# selected reservation, label and JIT, and a second matrix instance respects
# max_parallel rather than taking another slot from the shared resource ledger.
async def test_bootstrap_retry_and_parallel_instance_share_the_real_reservation_policy(
    chain, powercell
):
    await chain.bind()
    chain.agent = powercell
    first = await chain.bootstrap()
    retry = await chain.bootstrap()
    assert retry == first and chain.peer.jit_count == 1
    second = await chain.bootstrap(instance="matrix-other")
    assert second["backend"] == "github"
    history = (await chain.api.get(f"/v1/github/routes/{chain.route['id']}/runs")).json()
    assert len(history) == 2 and sum(row["reason"] == "concurrency_limit" for row in history) == 1
    await chain.start_job(first)
    await chain.finish_job()
    # state() normally selects a single attempt. Both rows must ultimately clean.
    await chain.drain()
    async with chain.app.state.db.read() as session:
        rows = (await session.scalars(select(CIReservation))).all()
        assert all(row.cleaned_at for row in rows)


# Assertions: disconnect after commit disables future assignments and cancels
# the selected runner; cleanup must still succeed with App credentials after
# the user's OAuth token has already been deleted.
async def test_disconnect_after_commit_cleans_the_selected_runner_without_user_credentials(
    chain, powercell
):
    await chain.bind()
    chain.agent = powercell
    await chain.bootstrap()
    assert (await chain.api.delete("/v1/github/connection")).status_code == 204
    await chain.pump(1)
    row = await chain.state()
    assert row.status == "cancelled" and row.reason == "authorization_revoked"
    await chain.drain()
    assert not chain.peer.registered and chain.peer.deletes == 1
    assert (await chain.api.get("/v1/github/status")).json()["account"] is None


# Assertions: GitHub deletion denial never marks registration cleanup complete;
# confirmed VM destruction may release resources, and a later provider retry
# removes the registration without re-running the CI command.
async def test_github_cleanup_denial_is_retried_after_vm_destruction(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    outputs = await chain.bootstrap()
    await chain.start_job(outputs)
    await chain.finish_job()
    chain.peer.failures[("DELETE", "/repos/test-org/repo/actions/runners/55")] = 403
    await chain.pump(5)
    row = await chain.state()
    assert row.cleaned_at is None and chain.peer.registered
    async with chain.app.state.db.read() as session:
        assert not (await session.get(Isolate, row.isolate_id)).reserved
    chain.peer.failures.clear()
    await chain.drain()
    assert chain.peer.deletes == 1 and not chain.peer.registered
    assert len([task for task in chain.tasks if task["kind"] == "run"]) == 1


# Assertions: duplicated completion and an out-of-order start remain terminal;
# each webhook is HMAC verified, attempt history is unchanged, and cleanup occurs
# exactly once instead of reviving or re-registering the runner.
async def test_duplicate_and_late_github_events_cannot_restart_the_finished_chain(chain, powercell):
    await chain.bind()
    chain.agent = powercell
    outputs = await chain.bootstrap()
    await chain.start_job(outputs)
    await chain.finish_job("failure")
    finished = await chain.state()
    for _ in range(2):
        await chain.webhook("workflow_job", "completed", chain.job, delivery="repeated-completion")
    await chain.webhook("workflow_job", "in_progress", chain.job)
    row = await chain.state()
    assert (
        row.status == "completed"
        and row.reason == "github_failure"
        and row.finished_at == finished.finished_at
    )
    await chain.drain()
    assert chain.peer.jit_count == chain.peer.deletes == 1


# Assertions: the complete workflow also works with the production browser
# cookie/CSRF contract, without a platform bearer token in the browser or YAML;
# a foreign Origin cannot begin OAuth, while valid cookie authorization can.
async def test_browser_cookie_authorization_runs_the_complete_chain(chain, powercell, user):
    csrf = {"Origin": "https://platform.mutating.tech", "X-Requested-With": "ThrongDashboard"}
    login = await chain.client.post(
        "/v1/auth/session",
        json={"login": user["email"], "password": user["password"]},
        headers=csrf,
    )
    assert login.status_code == 200
    # The API uses an artificial clock; parse the wire cookie without involving
    # the test process's real wall-clock expiry checks.
    cookie = SimpleCookie(login.headers["set-cookie"])["tmc_session"].value
    chain.credentials.append(cookie)
    assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]
    async with httpx.AsyncClient(
        base_url=chain.client.base_url,
        trust_env=False,
        headers={**csrf, "Cookie": "tmc_session=" + cookie},
    ) as browser:
        # Test loopback is HTTP; carrying the cookie explicitly preserves the
        # production Secure cookie policy rather than weakening test settings.
        chain.api = browser
        rejected = await browser.post(
            "/v1/github/authorize", headers={"Origin": "https://foreign.test"}
        )
        assert rejected.status_code == 403
        await chain.bind()
        chain.agent = powercell
        outputs = await chain.bootstrap()
        assert cookie not in chain.yaml and outputs["backend"] == "powercell"
        await chain.start_job(outputs)
        await chain.finish_job()
        await chain.drain()
        assert (await browser.delete("/v1/auth/session")).status_code == 204
        assert (await browser.get("/v1/github/status")).status_code == 401


# Assertions: a real organization-owned Mac can run the whole workflow; the
# binding and isolate belong exclusively to that organization, explicit machine
# selection is respected, and its shared CPU/RAM/disk ledger is cleared afterward.
async def test_organization_pool_and_selected_mac_run_the_complete_chain(chain, powercell, company):
    computer = (await chain.api.get(f"/v1/computers/{powercell.computer_id}")).json()
    ownerships = (await chain.api.get("/v1/computer-ownerships")).json()
    ownership = next(row for row in ownerships if row["computer_id"] == powercell.computer_id)
    moved = await chain.api.patch(
        f"/v1/computer-ownerships/{ownership['id']}", json={"company_id": company["id"]}
    )
    assert moved.status_code == 200, moved.text
    # Transferring a computer deliberately revokes the former owner's credential.
    assert (await powercell.client.get("/agent/v1/session")).status_code == 401
    credential = await chain.api.post(f"/v1/computers/{powercell.computer_id}/credentials")
    assert credential.status_code == 201, credential.text
    token = credential.json()["token"]
    chain.credentials.append(token)
    powercell.client.headers["Authorization"] = "Bearer " + token
    current_response = await powercell.client.get("/agent/v1/session")
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()
    response = await powercell.client.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": "organization-session"},
        json={"clean_start": True, "previous_session_id": current["session_id"]},
    )
    assert response.status_code == 200, response.text
    powercell.client.headers["X-Agent-Session"] = response.json()["session_id"]
    capacity = await powercell.client.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": "organization-capacity"},
        json=computer["capacity_report"],
    )
    assert capacity.status_code == 200, capacity.text
    await chain.bind(company_id=company["id"], computer_ids=[powercell.computer_id])
    assert chain.route["company_id"] == company["id"] and chain.route["user_id"] is None
    chain.agent = powercell
    outputs = await chain.bootstrap()
    assert outputs["backend"] == "powercell"
    row = await chain.state()
    isolate = (await chain.api.get(f"/v1/isolates/{row.isolate_id}")).json()
    assert isolate["company_id"] == company["id"] and isolate["user_id"] is None
    assert isolate["computer_id"] == powercell.computer_id
    await chain.start_job(outputs)
    await chain.finish_job()
    await chain.drain()
