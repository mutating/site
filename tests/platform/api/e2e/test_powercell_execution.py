"""Power Cell policy changes use real HTTP workers and the durable scheduler."""

import asyncio
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def capacity_report():
    """Offer the same inventory as the regular worker with explicit runtime policy."""
    return {
        "revision": 0,
        "accepting_work": True,
        "mode": "idle",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "default_isolation": "shared",
        "network_enabled": False,
        "internet_only": True,
        "running_commands": 0,
        "cpu_used_millis": 0,
        "memory_used_bytes": 0,
    }


async def test_capabilities_fail_closed_without_issuer(client, api):
    """An unconfigured server never advertises enrollment or creates an unverifiable device."""
    response = await client.get("/api/v1/agent-capabilities")
    assert response.status_code == 200
    assert response.json()["attestation_required"] is True
    assert "powercell.enrollment.v1" not in response.json()["extensions"]
    response = await api.post(
        "/api/v1/agent-enrollments",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "installation_id": str(uuid4()),
            "isolation_modes": ["vm", "shared"],
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 503


async def test_targets_below_reservations_and_revisions_are_atomic(
    api, agent_api, computer, isolate, capacity_report
):
    """Zero offered capacity preserves reservations; stale and conflicting reports cannot restore work."""
    before = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    report = {
        **capacity_report,
        "cpu_millis": 0,
        "memory_bytes": 0,
        "disk_bytes": 0,
        "accepting_work": False,
    }
    key = str(uuid4())
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": key}, json=report
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"revision": 0, "accepted": True}
    after = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert after["reserved_cpu"] == before["reserved_cpu"] == 1000
    assert after["target_cpu"] == after["target_memory"] == after["target_disk"] == 0
    assert after["lease_expires_at"] == before["lease_expires_at"]
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": key}, json=report
    )
    assert response.status_code == 200
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": str(uuid4())}, json=report
    )
    assert response.status_code == 200
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": key}, json=capacity_report
    )
    assert response.status_code == 409
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": str(uuid4())}, json=capacity_report
    )
    assert response.status_code == 409
    reports = [{**capacity_report, "revision": i} for i in [1, 3, 2]]
    responses = await asyncio.gather(
        *(
            agent_api.post(
                "/agent/v1/capacity", headers={"Idempotency-Key": str(uuid4())}, json=row
            )
            for row in reports
        )
    )
    assert all(row.status_code in {200, 409} for row in responses)
    after = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert after["capacity_revision"] == 3
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": str(uuid4())}, json=report
    )
    assert response.status_code == 409


@pytest.mark.parametrize("mode", [None, "vm", "shared"])
async def test_plugin_override_is_resolved_once(
    api, agent_api, isolate_payload, capacity_report, mode
):
    """An explicit plugin choice overrides the agent default and later policy cannot rewrite an isolate."""
    response = await agent_api.post(
        "/agent/v1/capacity", headers={"Idempotency-Key": str(uuid4())}, json=capacity_report
    )
    assert response.status_code == 200
    response = await api.post(
        "/api/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={**isolate_payload, "isolation_mode": mode, "retry_allowed": False},
    )
    assert response.status_code == 202
    isolate = response.json()
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    task = response.json()["tasks"][0]
    assert task["payload"]["isolation_mode"] == (mode or "shared")
    assert task["payload"]["retry_allowed"] is False
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            **capacity_report,
            "revision": 1,
            "default_isolation": "vm",
            "network_enabled": True,
            "internet_only": False,
        },
    )
    assert response.status_code == 200
    current = (await api.get(f"/api/v1/isolates/{isolate['id']}")).json()
    assert current["effective_isolation"] == (mode or "shared")
    assert current["network_enabled"] is False and current["internet_only"] is True
    response = await agent_api.post(
        f"/agent/v1/isolates/{isolate['id']}/evict",
        headers={"Idempotency-Key": str(uuid4())},
        json={"reason": "isolate_lost", "cleanup_confirmed": True, "retry_allowed": True},
    )
    assert response.status_code == 200 and response.json()["retry_allowed"] is False


async def test_draining_blocks_delivery_and_start_but_allows_cleanup(
    api, agent_api, run_task, isolate, capacity_report
):
    """An already delivered run cannot newly start while draining; cancel and destroy remain deliverable."""
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={**capacity_report, "accepting_work": False},
    )
    assert response.status_code == 200
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0})
    assert response.json()["tasks"] == []
    for operation in ["ack", "started"]:
        response = await agent_api.post(f"/agent/v1/tasks/{run_task['id']}/{operation}")
        assert response.status_code == 409
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/snapshots", headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 409
    response = await api.delete(f"/api/v1/isolates/{isolate['id']}")
    assert response.status_code == 202
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    tasks = response.json()["tasks"]
    assert {t["kind"] for t in tasks} == {"cancel", "destroy"}
    destroy = next(t for t in tasks if t["kind"] == "destroy")
    response = await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/ack")
    assert response.status_code == 200
    response = await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
    assert response.status_code == 200


async def test_paused_worker_does_not_schedule_new_work(
    api, agent_api, isolate, capacity_report, isolate_payload
):
    """The controller leaves queued runs and new isolates pending until the agent offers capacity again."""
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={**capacity_report, "accepting_work": False},
    )
    assert response.status_code == 200
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "echo delayed"},
    )
    command = response.json()
    response = await api.post(
        "/api/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=isolate_payload
    )
    pending = response.json()
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.1})
    assert response.json()["tasks"] == []
    assert (await api.get(f"/api/v1/commands/{command['id']}")).json()["status"] == "queued"
    assert (await api.get(f"/api/v1/isolates/{pending['id']}")).json()["computer_id"] is None
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            **capacity_report,
            "revision": 1,
            "cpu_millis": 3000,
            "memory_bytes": 3072,
            "disk_bytes": 12288,
        },
    )
    assert response.status_code == 200
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert {t["kind"] for t in response.json()["tasks"]} == {"create", "run"}


async def test_eviction_completion_race_preserves_one_result(
    api, agent_api, other_agent, isolate, run_task, command, computer, client
):
    """Concurrent completion and eviction settle exactly once without releasing another isolate's resources."""
    endpoint = f"/agent/v1/isolates/{isolate['id']}/evict"
    payload = {"reason": "capacity_reduced", "cleanup_confirmed": True, "retry_allowed": True}
    response = await client.post(
        endpoint, headers={**other_agent["headers"], "Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 404
    response = await agent_api.post(
        endpoint,
        headers={"Idempotency-Key": str(uuid4())},
        json={**payload, "cleanup_confirmed": False},
    )
    assert response.status_code == 422
    key = str(uuid4())
    responses = await asyncio.gather(
        agent_api.post(endpoint, headers={"Idempotency-Key": key}, json=payload),
        agent_api.post(f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0}),
    )
    assert responses[0].status_code == 200
    assert responses[1].status_code in {200, 409}
    result = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    response = await agent_api.post(endpoint, headers={"Idempotency-Key": key}, json=payload)
    assert response.status_code == 200
    response = await agent_api.post(
        endpoint, headers={"Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 200
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json() == result
    row = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert row["reserved_cpu"] == row["reserved_memory"] == row["reserved_disk"] == 0


async def test_disconnect_retry_and_session_fencing(
    api, agent_api, agent, computer, isolate, capacity_report
):
    """A lost disconnect response is recoverable and old sessions cannot evict newly connected work."""
    payload = {"reason": "disabled", "cleanup_confirmed": True}
    key = str(uuid4())
    for _ in range(2):
        response = await agent_api.post(
            "/agent/v1/disconnect", headers={"Idempotency-Key": key}, json=payload
        )
        assert response.status_code == 200
    response = await agent_api.post(
        "/agent/v1/disconnect", headers={"Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 409
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).json()["reserved_cpu"] == 0
    response = await agent_api.post(
        "/agent/v1/session",
        headers={"Idempotency-Key": str(uuid4())},
        json={"clean_start": True, "previous_session_id": agent["session"]["session_id"]},
    )
    assert response.status_code == 200
    new_session = response.json()["session_id"]
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4()), "X-Agent-Session": new_session},
        json=capacity_report,
    )
    assert response.status_code == 200
    response = await agent_api.post(
        f"/agent/v1/isolates/{isolate['id']}/evict",
        headers={"Idempotency-Key": str(uuid4()), "X-Agent-Session": new_session},
        json={"reason": "isolate_lost", "cleanup_confirmed": True},
    )
    assert response.status_code == 409
    response = await agent_api.post(
        "/agent/v1/disconnect",
        headers={"Idempotency-Key": key, "X-Agent-Session": new_session},
        json=payload,
    )
    assert response.status_code == 409


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_unbind_without_user_token_preserves_only_exact_retry(agent_api, agent, clock):
    """Logout works after user-token/session expiry and its revoked token authorizes no other operation."""
    clock.now += timedelta(days=2)
    key = str(uuid4())
    for _ in range(2):
        response = await agent_api.post(
            "/agent/v1/unbind", headers={"Idempotency-Key": key}, json={"cleanup_confirmed": True}
        )
        assert response.status_code == 200, response.text
    response = await agent_api.get("/agent/v1/session")
    assert response.status_code == 401
    response = await agent_api.post(
        "/agent/v1/unbind",
        headers={"Idempotency-Key": str(uuid4())},
        json={"cleanup_confirmed": True},
    )
    assert response.status_code == 401
    clock.now += timedelta(days=2)
    response = await agent_api.post(
        "/agent/v1/unbind", headers={"Idempotency-Key": key}, json={"cleanup_confirmed": True}
    )
    assert response.status_code == 401


async def test_late_diagnostics_are_owned_deduplicated_and_do_not_change_results(
    api, agent_api, other_api, other_agent, client, computer, command, run_task, clock
):
    """Failure logs survive expired sessions, stay private, and never become late execution results."""
    response = await agent_api.post(
        f"/agent/v1/tasks/{run_task['id']}/complete", json={"returncode": 0, "stdout": "done"}
    )
    assert response.status_code == 200
    before = (await api.get(f"/api/v1/commands/{command['id']}/result")).json()
    clock.now += timedelta(minutes=5)
    identifier = str(uuid4())
    content = json.dumps(
        {
            "id": identifier,
            "created": clock.now.isoformat(),
            "message": "failure",
            "task_id": run_task["id"],
            "session_id": run_task["session_id"],
            "uploaded": False,
        }
    ).encode()
    endpoint = f"/agent/v1/diagnostics/{identifier}"
    for _ in range(2):
        response = await agent_api.put(endpoint, content=content)
        assert response.status_code == 200, response.text
        assert response.json()["sha256"] == hashlib.sha256(content).hexdigest()
    response = await agent_api.put(endpoint, content=content.replace(b"failure", b"different"))
    assert response.status_code == 409
    response = await client.put(endpoint, headers=other_agent["headers"], content=content)
    assert response.status_code == 404
    endpoint = f"/api/v1/computers/{computer['id']}/diagnostics/{identifier}"
    response = await other_api.get(endpoint)
    assert response.status_code == 404
    response = await api.get(endpoint)
    assert response.status_code == 200 and response.content == content
    assert (await api.get(f"/api/v1/commands/{command['id']}/result")).json() == before


@pytest.mark.parametrize(
    "case", ["invalid_json", "wrong_id", "future", "old", "missing", "oversized"]
)
@pytest.mark.parametrize("settings", [{"max_diagnostic_bytes": 1024}], indirect=True)
async def test_diagnostic_bounds_and_retention(api, agent_api, computer, clock, case):
    """Malformed, oversized and out-of-retention diagnostics are rejected without creating downloadable data."""
    identifier = str(uuid4())
    created = clock.now
    if case == "future":
        created += timedelta(minutes=10)
    if case == "old":
        created -= timedelta(days=31)
    payload = {
        "id": str(uuid4()) if case == "wrong_id" else identifier,
        "created": created.isoformat(),
        "message": "x" * (2048 if case == "oversized" else 1),
    }
    content = b"not json" if case == "invalid_json" else json.dumps(payload).encode()
    if case != "missing":
        response = await agent_api.put(f"/agent/v1/diagnostics/{identifier}", content=content)
        assert response.status_code == (413 if case == "oversized" else 422), response.text
    response = await api.get(f"/api/v1/computers/{computer['id']}/diagnostics/{identifier}")
    assert response.status_code == 404


@pytest.mark.parametrize("settings", [{"max_computer_diagnostics_bytes": 400}], indirect=True)
async def test_diagnostic_quota_expires_and_metadata_without_task_is_allowed(
    api, agent_api, computer, clock
):
    """Database retention releases quota atomically, and expired logs cannot be downloaded."""
    ids = [str(uuid4()) for _ in range(3)]
    for identifier, status in zip(ids[:2], [200, 409], strict=True):
        response = await agent_api.put(
            f"/agent/v1/diagnostics/{identifier}",
            json={"id": identifier, "created": clock.now.isoformat(), "message": "x" * 150},
        )
        assert response.status_code == status, response.text
    clock.now += timedelta(days=31)
    # User token has expired as well; renew it through login to inspect retained access rules.
    response = await api.post(
        "/api/v1/auth/login",
        json={"login": "alice", "password": "  correct horse battery staple  "},
    )
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    response = await api.get(
        f"/api/v1/computers/{computer['id']}/diagnostics/{ids[0]}", headers=headers
    )
    assert response.status_code == 404
    response = await agent_api.put(
        f"/agent/v1/diagnostics/{ids[2]}",
        json={"id": ids[2], "created": clock.now.isoformat(), "message": "after retention"},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "header",
    [
        {"Idempotency-Key": "x" * 129},
        {"Idempotency-Key": str(uuid4()), "X-Agent-Session": ""},
        {"Idempotency-Key": str(uuid4()), "X-Agent-Session": "invalid"},
    ],
)
async def test_cleanup_idempotency_headers_are_bounded(agent_api, header):
    """Session-free cleanup validates stored header values before database insertion."""
    response = await agent_api.post(
        "/agent/v1/unbind", headers=header, json={"cleanup_confirmed": True}
    )
    assert response.status_code == 422


async def test_powercell_runtime_cannot_bypass_attestation(api):
    """The legacy computer registration path cannot create a Power Cell execution worker."""
    response = await api.post(
        "/api/v1/computers",
        json={
            "os": "linux",
            "architecture": "arm64",
            "runtime": "powercell-ubuntu-arm64-v1",
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 422


async def test_target_free_capacity_and_available_summary(
    api, agent_api, user, computer, isolate, capacity_report
):
    """Offered resource totals and free quantities respect reduced targets without negative free capacity."""
    response = await agent_api.post(
        "/agent/v1/capacity",
        headers={"Idempotency-Key": str(uuid4())},
        json={**capacity_report, "cpu_millis": 500, "memory_bytes": 512, "disk_bytes": 1024},
    )
    assert response.status_code == 200
    row = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert row["free_cpu"] == row["free_memory"] == row["free_disk"] == 0
    summary = (
        await api.get(
            f"/api/v1/users/{user['id']}/resources/summary", params={"only_available": True}
        )
    ).json()
    cpu = next(row for row in summary["resources"] if row["code"] == "cpu")
    assert cpu["total"] == 500 and cpu["occupied"] == 1000 and cpu["free"] == 0
    response = await api.post(
        f"/api/v1/isolates/{isolate['id']}/commands",
        headers={"Idempotency-Key": str(uuid4())},
        json={"command": "echo pending"},
    )
    assert response.status_code == 202
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 0.1})
    assert response.json()["tasks"] == []
