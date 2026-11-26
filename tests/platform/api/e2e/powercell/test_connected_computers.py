"""Connected-device inventory and ownership with a signed, attested worker."""

import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession

from throng_api.cli import migration_config
from throng_api.models import AvailabilityBaseline, Computer

from .conftest import proof_headers

pytestmark = pytest.mark.anyio
PATH = "/v1/connected-computers"


@pytest.fixture
async def connected_worker(client, bound_device, clock):
    """Open an agent session and publish available resources."""
    device = bound_device
    identity = device.identity
    headers = {
        "Authorization": "Bearer " + device.binding["credential"],
        "Content-Type": "application/json",
    }

    async def post(path, body=None):
        content = json.dumps(body).encode() if body is not None else b""
        return await client.post(
            path,
            content=content,
            headers=proof_headers(
                identity.key,
                identity.certificate,
                "POST",
                path,
                content,
                {**headers, "Idempotency-Key": str(uuid4())},
                clock.now,
            ),
        )

    started = await post("/agent/v1/session", {"clean_start": True})
    assert started.status_code == 200, started.text
    session = started.json()
    headers["X-Agent-Session"] = session["session_id"]
    reported = await post(
        "/agent/v1/capacity",
        {
            "revision": 0,
            "accepting_work": True,
            "mode": "idle",
            "cpu_millis": 2000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
            "running_commands": 0,
            "cpu_used_millis": 0,
            "memory_used_bytes": 0,
        },
    )
    assert reported.status_code == 200, reported.text
    return session, post


async def test_inventory_is_account_scoped_and_reports_leases(
    api, other_api, client, computer, company_token, connected_worker, clock
):
    """List the signed-in account's computers with their current lease state and pools."""
    session, _ = connected_worker
    reply = await api.get(PATH, params={"limit": 1})
    assert int(reply.headers["x-computer-pool-check-revision"]) >= 0
    assert reply.status_code == 200, reply.text
    page = reply.json()
    assert page["total"] == 1 and page["online"] == 1
    row = page["items"][0]
    assert row["pool_revision"] >= 0
    row["pool_revision"] = 0
    assert row == {
        "id": session["computer_id"],
        "name": "My Mac",
        "os": "linux",
        "architecture": "arm64",
        "runtime": "powercell-ubuntu-arm64-v1",
        "online": True,
        "pool": {"company_id": None, "name": None},
        "pool_membership_id": None,
        "pool_revision": 0,
        "can_move": True,
        "transfer_pending": False,
    }
    assert computer["id"] != session["computer_id"]
    assert (await api.get(PATH, params={"offset": 1})).json() == {
        "items": [],
        "total": 1,
        "online": 1,
    }
    assert (await other_api.get(PATH)).json() == {"items": [], "total": 0, "online": 0}
    expected_user = (await api.get("/v1/users/me")).json()["id"]
    stale_tab = await other_api.get(PATH, headers={"X-Expected-User-Id": expected_user})
    assert stale_tab.status_code == 409 and stale_tab.json()["code"] == "account_session_changed"
    assert (
        await client.get(PATH, headers={"Authorization": "Bearer " + company_token["token"]})
    ).status_code == 403
    connected_position = f"{PATH}/{session['computer_id']}/position"
    assert (await api.get(connected_position)).json() == 0
    assert (await api.get(f"{PATH}/{computer['id']}/position")).json() is None
    assert (await other_api.get(connected_position)).json() is None
    assert (
        await client.get(
            connected_position, headers={"Authorization": "Bearer " + company_token["token"]}
        )
    ).status_code == 403
    clock.now = datetime.fromisoformat(session["lease_expires_at"])
    expired = (await api.get(PATH)).json()
    assert expired["total"] == 1 and expired["online"] == 0
    assert not expired["items"][0]["online"]


async def test_pool_inventory_reads_rows_and_count_from_one_snapshot(
    api, user, company, connected_worker, monkeypatch
):
    """Moving the last computer out mid-read cannot leave one row and a zero count."""
    worker, _ = connected_worker
    computer_id = worker["computer_id"]
    rows_read = asyncio.Event()
    resume = asyncio.Event()
    scalars = AsyncSession.scalars

    async def pause_inventory(session, statement, *args, **kwargs):
        rows = await scalars(session, statement, *args, **kwargs)
        if (
            not rows_read.is_set()
            and statement.column_descriptions[0].get("entity") is Computer
            and "computer_ownership" in str(statement)
        ):
            rows_read.set()
            await resume.wait()
        return rows

    monkeypatch.setattr(AsyncSession, "scalars", pause_inventory)
    # Start in the personal pool so /v1/computers has a row to move.
    personal = await api.patch(
        f"{PATH}/{computer_id}/pool",
        json={"company_id": None, "expected_company_id": None},
    )
    assert personal.status_code == 200, personal.text
    params = {"user_id": user["id"], "only_available": "false", "limit": 3}
    reading = asyncio.create_task(api.get("/v1/computers", params=params))
    try:
        await asyncio.wait_for(rows_read.wait(), timeout=5)
        moved = await api.patch(
            f"{PATH}/{computer_id}/pool",
            json={"company_id": company["id"], "expected_company_id": None},
        )
        assert moved.status_code == 200, moved.text
    finally:
        resume.set()
        original = await reading

    assert original.status_code == 200, original.text
    assert [row["id"] for row in original.json()] == [computer_id]
    assert original.headers["x-total-count"] == "1"
    refreshed = await api.get("/v1/computers", params=params)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json() == [] and refreshed.headers["x-total-count"] == "0"


async def test_lookup_confirms_management_for_the_connected_account_without_pool_read_access(
    api,
    other_api,
    user,
    connected_worker,
):
    """Keep account-owned computers actionable for members with resource management access."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    pool = await other_api.post("/v1/companies", json={"name": "Managed pool"})
    assert pool.status_code == 201, pool.text
    company_id = pool.json()["id"]
    member = await other_api.post(
        "/v1/memberships",
        json={
            "company_id": company_id,
            "user_id": user["id"],
            "role": "user",
            "permissions": ["resources.manage"],
        },
    )
    assert member.status_code == 201, member.text
    moved = await api.patch(
        f"{PATH}/{computer_id}/pool",
        json={"company_id": company_id, "expected_company_id": None},
    )
    assert moved.status_code == 200, moved.text
    assert all(
        row["computer_id"] != computer_id
        for row in (await api.get("/v1/computer-ownerships")).json()
    )
    reply = await api.get(PATH, params={"computer_id": computer_id})
    assert reply.status_code == 200, reply.text
    result = reply.json()
    assert result["total"] == result["online"] == 1
    assert [row["id"] for row in result["items"]] == [computer_id]
    assert result["items"][0]["can_move"]
    for account, requested in [(other_api, computer_id), (api, str(uuid4()))]:
        lookup = await account.get(PATH, params={"computer_id": requested})
        assert lookup.json() == {"items": [], "total": 0, "online": 0}
    removed = await other_api.delete(f"/v1/memberships/{member.json()['id']}")
    assert removed.status_code == 204, removed.text
    hidden_pool = (await api.get(PATH, params={"computer_id": computer_id})).json()["items"][0]
    assert hidden_pool["id"] == computer_id
    assert hidden_pool["pool"] is None and hidden_pool["pool_membership_id"] is None
    assert not hidden_pool["can_move"]


async def test_pool_manager_can_check_the_pool_using_the_bound_device_key(
    api, other_api, other_user, company, client, connected_worker, bound_device, clock
):
    """Return the verified pool to the credential-bearing agent while preserving scoped recovery."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    member = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "user",
            "permissions": ["resources.manage"],
        },
    )
    assert member.status_code == 201, member.text
    moved = await api.patch(
        f"{PATH}/{computer_id}/pool",
        json={"company_id": company["id"], "expected_company_id": None},
    )
    assert moved.status_code == 200, moved.text
    visible_pool = await other_api.get(f"/v1/companies/{company['id']}")
    assert visible_pool.status_code == 200, visible_pool.text
    assert "resources.manage" in visible_pool.json()["my_permissions"]
    assert "resources.read" not in visible_pool.json()["my_permissions"]
    assert (await other_api.get(PATH)).json()["items"] == []
    assert (await other_api.get(f"{PATH}/{computer_id}/recovery?snapshot=true")).json() == {
        "revision": None
    }
    token = bound_device.binding["credential"]
    agent_headers = {"Authorization": "Bearer " + token}

    async def get_session(path):
        return await client.get(
            path,
            headers=proof_headers(
                bound_device.identity.key,
                bound_device.identity.certificate,
                "GET",
                path,
                b"",
                agent_headers,
                clock.now,
            ),
        )

    basic = await get_session("/agent/v1/session")
    assert basic.status_code == 200, basic.text
    assert "pool_owner" not in basic.json()
    details = "/agent/v1/session?include_pool=true"
    checked = await get_session(details)
    assert checked.status_code == 200, checked.text
    assert checked.json()["session_id"] == session["session_id"]
    assert not checked.json()["transfer_pending"]
    assert checked.json()["pool_owner"] == {"user_id": None, "company_id": company["id"]}


@pytest.mark.parametrize("move_arrived_first", [True, False])
async def test_reconciliation_serializes_with_a_pool_change_whose_response_was_lost(
    api,
    other_api,
    company,
    connected_worker,
    move_arrived_first,
):
    """Resolve uncertain browser moves consistently with the original write."""
    session, _ = connected_worker
    path = f"{PATH}/{session['computer_id']}/pool"
    payload = {"company_id": company["id"], "expected_company_id": None}
    headers = {"Idempotency-Key": str(uuid4())}
    if move_arrived_first:
        result = await api.patch(path, json=payload, headers=headers)
        assert result.status_code == 200, result.text
    reconciled = await api.post(f"{path}/reconcile", json=payload, headers=headers)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json() is move_arrived_first
    revision_header = "x-computer-pool-check-revision"
    if move_arrived_first:
        assert int(reconciled.headers[revision_header]) > int(result.headers[revision_header])
        assert reconciled.headers["x-computer-read-allowed"] == "true"
        assert reconciled.headers["x-organization-membership-id"] == company["my_membership_id"]
        returned = await api.patch(
            path, json={"company_id": None, "expected_company_id": company["id"]}
        )
        assert returned.status_code == 200, returned.text
        replayed = await api.patch(path, json=payload, headers=headers)
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["company_id"] is None
        assert int(replayed.headers[revision_header]) > int(returned.headers[revision_header])
        assert int(returned.headers[revision_header]) > int(reconciled.headers[revision_header])
        assert "x-organization-id" not in replayed.headers
    else:
        assert int(reconciled.headers[revision_header]) >= 0
        assert "x-computer-read-allowed" not in reconciled.headers
        delayed = await api.patch(path, json=payload, headers=headers)
        assert delayed.status_code == 409, delayed.text
        assert delayed.json()["detail"] == "Computer pool change was cancelled"
    row = (await api.get(PATH)).json()["items"][0]
    assert row["pool"]["company_id"] is None
    later = await api.post(f"{path}/reconcile", json=payload, headers=headers)
    assert later.json() is move_arrived_first
    if move_arrived_first:
        assert int(later.headers[revision_header]) > int(replayed.headers[revision_header])
        assert later.headers["x-computer-read-allowed"] == "true"
        assert "x-organization-id" not in later.headers
    else:
        assert int(later.headers[revision_header]) > int(reconciled.headers[revision_header])
        assert "x-computer-read-allowed" not in later.headers
    assert (
        await other_api.post(f"{path}/reconcile", json=payload, headers=headers)
    ).json() is False
    scoped_payload = {
        **payload,
        "expected_user_id": (await api.get("/v1/users/me")).json()["id"],
    }
    for method, endpoint in [
        (other_api.patch, path),
        (other_api.post, f"{path}/reconcile"),
    ]:
        forbidden = await method(endpoint, json=scoped_payload, headers=headers)
        assert forbidden.status_code == 403, forbidden.text


async def test_transfer_receipt_reflects_current_read_permission_and_membership(
    api, other_api, client, user, connected_worker
):
    """Order membership checks separately from transfers and keep denied details private."""
    session, _ = connected_worker
    company = await other_api.post("/v1/companies", json={"name": "Transferred computer's pool"})
    assert company.status_code == 201, company.text
    company_id = company.json()["id"]

    async def grant(permissions):
        membership = await other_api.post(
            "/v1/memberships",
            json={
                "company_id": company_id,
                "user_id": user["id"],
                "role": "user",
                "permissions": permissions,
            },
        )
        assert membership.status_code == 201, membership.text
        return membership.json()["id"]

    membership = await grant(["resources.manage"])
    computer_path = f"/v1/computers/{session['computer_id']}"
    path = f"{PATH}/{session['computer_id']}/pool"
    payload = {"company_id": company_id, "expected_company_id": None}
    headers = {"Idempotency-Key": str(uuid4())}
    moved = await api.patch(path, json=payload, headers=headers)
    assert moved.status_code == 200, moved.text
    revision_header = "x-computer-pool-check-revision"
    access_header = "x-computer-read-allowed"
    revision = int(moved.headers[revision_header])
    assert moved.headers["x-organization-membership-id"] == membership
    assert moved.headers[access_header] == "false"
    ownership_revision = (await api.get(PATH)).json()["items"][0]["pool_revision"]
    info = await api.get(computer_path)
    assert info.status_code == 403, info.text
    assert int(info.headers[revision_header]) >= revision
    assert info.headers["x-organization-membership-id"] == membership
    denied = await api.post(f"{path}/reconcile", json=payload, headers=headers)
    assert denied.status_code == 200 and denied.json() is True
    assert denied.headers[access_header] == "false"
    assert denied.headers["x-organization-membership-id"] == membership

    assert (await other_api.delete(f"/v1/memberships/{membership}")).status_code == 204
    current = await grant(["resources.manage", "resources.read"])
    fresh = await api.post(f"{path}/reconcile", json=payload, headers=headers)
    assert fresh.status_code == 200 and fresh.json() is True
    assert int(fresh.headers[revision_header]) > int(denied.headers[revision_header])
    assert fresh.headers["x-organization-membership-id"] == current
    assert fresh.headers[access_header] == "true"
    assert (await api.get(PATH)).json()["items"][0]["pool_revision"] == ownership_revision
    issued = await api.post(
        "/v1/api-tokens",
        json={"user_id": user["id"], "name": "Pool moves without reads", "scopes": ["manage"]},
    )
    assert issued.status_code == 201, issued.text
    limited_headers = {**headers, "Authorization": "Bearer " + issued.json()["token"]}
    limited = await client.post(f"{path}/reconcile", json=payload, headers=limited_headers)
    assert limited.status_code == 200 and limited.json() is True
    assert limited.headers[access_header] == "false"
    assert "x-organization-id" not in limited.headers

    for removal in [f"/v1/memberships/{current}", computer_path]:
        assert (await other_api.delete(removal)).status_code == 204
        receipt = await api.post(f"{path}/reconcile", json=payload, headers=headers)
        assert receipt.status_code == 200 and receipt.json() is True
        assert receipt.headers[access_header] == "false"
        assert int(receipt.headers[revision_header]) > int(fresh.headers[revision_header])
        assert "x-organization-id" not in receipt.headers
        assert "x-organization-membership-id" not in receipt.headers
        unavailable = await api.get(computer_path)
        assert unavailable.status_code == 404
        assert revision_header not in unavailable.headers
        assert access_header not in unavailable.headers


async def test_unlinking_an_ownership_returns_the_computer_to_the_unassigned_state(
    api, company, connected_worker
):
    """Removing a pool keeps the registering account's computer visible and assignable."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": company["id"], "expected_company_id": None}
    assert (await api.patch(path, json=payload)).status_code == 200
    ownerships = await api.get("/v1/computer-ownerships")
    owner = next(row for row in ownerships.json() if row["computer_id"] == computer_id)
    assert (await api.delete(f"/v1/computer-ownerships/{owner['id']}")).status_code == 204
    page = (await api.get(PATH, params={"computer_id": computer_id})).json()
    assert page["total"] == 1 and page["items"][0]["pool"] is None
    assert (await api.get(f"/v1/computers/{computer_id}")).status_code == 200
    confirmed = await api.patch(path, json={"company_id": company["id"], "expected_company_id": None})
    assert confirmed.status_code == 200 and confirmed.json()["company_id"] == company["id"]


async def test_enrollment_completed_with_an_api_token_is_excluded(api, user, client):
    """Limit the personal connected inventory to accounts that used password sign-in."""
    device_id = uuid4()
    response = await client.post(
        "/v1/auth/login",
        json={
            "login": "alice",
            "password": user["password"],
            "device_id": str(device_id),
        },
    )
    assert response.status_code == 200, response.text
    issued = await api.post(
        "/v1/api-tokens",
        json={"user_id": user["id"], "name": "Enrollment", "scopes": ["manage"]},
    )
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
    payload = {
        "device_id": str(device_id),
        "name": "API worker",
        "cpu_millis": 2000,
        "memory_bytes": 2048,
        "disk_bytes": 8192,
        "isolation_modes": ["vm", "shared"],
    }
    response = await client.post("/v1/agent-enrollments", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    enrollment = response.json()
    response = await client.post(
        f"/v1/agent-enrollments/{enrollment['id']}/finish",
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": "finish-" + enrollment["id"],
        },
        json={},
    )
    assert response.status_code == 200, response.text
    assert (await api.get(PATH)).json() == {"items": [], "total": 0, "online": 0}
    move = await api.patch(
        f"{PATH}/{response.json()['computer_id']}/pool",
        json={"company_id": None, "expected_company_id": None},
    )
    assert move.status_code == 404


async def reapply_connection_migration(settings):
    """Rebuild connected-account state from the same pre-upgrade tables."""
    config = migration_config(settings)
    await anyio.to_thread.run_sync(command.downgrade, config, "0026")
    await anyio.to_thread.run_sync(command.upgrade, config, "0027")


async def start_replacement_session(client, bound_device, token, clock, *, publish_capacity=False):
    """Prove possession of the replacement key and optionally advertise capacity."""
    path = "/agent/v1/session"
    content = b'{"clean_start":true}'
    response = await client.post(
        path,
        content=content,
        headers=proof_headers(
            bound_device.identity.key,
            bound_device.identity.certificate,
            "POST",
            path,
            content,
            {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Idempotency-Key": str(uuid4()),
            },
            clock.now,
        ),
    )
    assert response.status_code == 200, response.text
    if publish_capacity:
        capacity = "/agent/v1/capacity"
        content = json.dumps(
            {
                "revision": 0,
                "accepting_work": True,
                "mode": "idle",
                "cpu_millis": 2000,
                "memory_bytes": 2048,
                "disk_bytes": 8192,
                "running_commands": 0,
                "cpu_used_millis": 0,
                "memory_used_bytes": 0,
            }
        ).encode()
        published = await client.post(
            capacity,
            content=content,
            headers=proof_headers(
                bound_device.identity.key,
                bound_device.identity.certificate,
                "POST",
                capacity,
                content,
                {
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Idempotency-Key": str(uuid4()),
                    "X-Agent-Session": response.json()["session_id"],
                },
                clock.now,
            ),
        )
        assert published.status_code == 200, published.text
    return response.json()


async def test_move_preserves_signed_session_and_transfers_capacity(
    api, other_api, connected_worker, company, user, clock, app
):
    """Transfer available resources while preserving the signed agent session."""
    session, agent_post = connected_worker
    computer_id = session["computer_id"]
    path = f"{PATH}/{computer_id}/pool"
    assert (await api.patch(path, json={"company_id": None, "expected_company_id": None})).status_code == 200
    before = clock.now
    async with app.state.db.write() as database:
        baseline = await database.get(AvailabilityBaseline, 1)
        baseline.available_from = before
    clock.now += timedelta(seconds=1)
    personal = f"/v1/users/{user['id']}/resources"
    organization = f"/v1/companies/{company['id']}/resources"

    async def size(pool):
        response = await api.get(f"{pool}/summary", params={"only_available": True})
        assert response.status_code == 200, response.text
        return response.json()["computer_count"]

    assert [await size(personal), await size(organization)] == [1, 0]
    payload = {"company_id": company["id"], "expected_company_id": None}
    assert (await other_api.patch(path, json=payload)).status_code == 404
    responses = await asyncio.gather(*(api.patch(path, json=payload) for _ in range(2)))
    for response in responses:
        assert response.status_code == 200, response.text
        assert response.json() == {"name": company["name"], "company_id": company["id"]}
    assert [await size(personal), await size(organization)] == [0, 1]
    row = (await api.get(PATH)).json()["items"][0]
    assert row["online"] and row["pool"] == responses[0].json()
    clock.now += timedelta(seconds=1)
    heartbeat = await agent_post("/agent/v1/heartbeat")
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["session_id"] == session["session_id"]
    for pool, at in [(personal, before), (organization, before + timedelta(seconds=1))]:
        response = await api.get(
            f"{pool}/availability-history",
            params={"from": before.isoformat(), "to": clock.now.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert any(
            datetime.fromisoformat(point["at"]) == at and point["cpu_millis"] == 2000
            for point in response.json()["points"]
        )
    stale = await api.patch(path, json={"company_id": None, "expected_company_id": None})
    assert stale.status_code == 409 and stale.json()["detail"] == "Computer pool changed"
    assert (
        await api.patch(path, json={"company_id": None, "expected_company_id": company["id"]})
    ).json() == {"company_id": None, "name": None}
    assert [await size(personal), await size(organization)] == [1, 0]


async def test_move_respects_live_permissions_and_busy_worker(
    api, other_api, connected_worker, company, user, artifact
):
    """Revalidate source and destination roles and the current resource reservations."""
    session, agent_post = connected_worker
    path = f"{PATH}/{session['computer_id']}/pool"
    assert (await api.patch(path, json={"company_id": None, "expected_company_id": None})).status_code == 200
    created = await other_api.post("/v1/companies", json={"name": "Read-only pool"})
    other_company = created.json()
    joined = await other_api.post(
        "/v1/memberships",
        json={"company_id": other_company["id"], "user_id": user["id"], "role": "user"},
    )
    assert joined.status_code == 201, joined.text
    denied = await api.patch(
        path, json={"company_id": other_company["id"], "expected_company_id": None}
    )
    assert denied.status_code == 403, denied.text
    isolate = await api.post(
        "/v1/isolates",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "os": "linux",
            "architecture": "arm64",
            "runtime": "powercell-ubuntu-arm64-v1",
            "image": "ubuntu:24.04",
            "state_artifact_id": artifact["id"],
            "cpu_millis": 1000,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert isolate.status_code == 202, isolate.text
    claimed = await agent_post("/agent/v1/tasks/claim", {"wait_seconds": 2})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["tasks"][0]["isolate_id"] == isolate.json()["id"]
    response = await api.patch(
        path, json={"company_id": company["id"], "expected_company_id": None}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Computer has reserved resources"
    assert (await api.get(PATH)).json()["items"][0]["pool"] == {
        "company_id": None,
        "name": None,
    }


@pytest.mark.parametrize("native_mutation", ["same_target", "different_target", "rotate_only"])
@pytest.mark.parametrize("return_to_source", [False, True])
async def test_stale_native_mutation_cannot_revoke_key_or_overwrite_a_browser_move(
    api,
    user,
    client,
    company,
    bound_device,
    connected_worker,
    clock,
    native_mutation,
    return_to_source,
):
    """Use ownership revisions to protect a committed browser transfer from stale native writes."""
    live, _ = connected_worker
    computer_id = live["computer_id"]
    path = f"{PATH}/{computer_id}/pool"
    assert (await api.patch(path, json={"company_id": None, "expected_company_id": None})).status_code == 200
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    path = f"{PATH}/{computer_id}/pool"
    browser = await api.patch(path, json={"company_id": company["id"], "expected_company_id": None})
    assert browser.status_code == 200, browser.text
    if return_to_source:
        browser = await api.patch(
            path, json={"company_id": None, "expected_company_id": company["id"]}
        )
        assert browser.status_code == 200, browser.text
    expected = {"expected_user_id": user["id"], "expected_revision": owner["revision"]}
    if native_mutation == "rotate_only":
        stale = await api.post(f"/v1/computers/{computer_id}/credentials", params=expected)
    else:
        target = (
            {"company_id": company["id"]}
            if native_mutation == "same_target"
            else {"user_id": user["id"]}
        )
        stale = await api.patch(
            f"/v1/computer-ownerships/{owner['id']}", params=expected, json=target
        )
    assert stale.status_code == 409 and stale.json()["code"] == "computer_pool_changed", stale.text
    current = (await api.get(PATH)).json()["items"][0]
    assert current["pool"] == browser.json() and current["can_move"]
    session_path = "/agent/v1/session"
    verified = await client.get(
        session_path,
        headers=proof_headers(
            bound_device.identity.key,
            bound_device.identity.certificate,
            "GET",
            session_path,
            b"",
            {"Authorization": "Bearer " + bound_device.binding["credential"]},
            clock.now,
        ),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["session_id"] == live["session_id"]


async def test_delayed_browser_move_cannot_reapply_after_the_computer_returned_to_its_source(
    api,
    company,
    connected_worker,
):
    """Reject an outdated browser intent after a round trip through another pool."""
    agent, _ = connected_worker
    id_ = agent["computer_id"]
    path = f"{PATH}/{id_}/pool"
    assert (await api.patch(path, json={"company_id": None, "expected_company_id": None})).status_code == 200
    initial = (await api.get(PATH, params={"computer_id": id_})).json()["items"][0]
    next_pool = {"company_id": company["id"], "expected_company_id": None}
    assert (
        await api.patch(path, json={**next_pool, "expected_revision": initial["pool_revision"]})
    ).status_code == 200
    current = (await api.get(PATH, params={"computer_id": id_})).json()["items"][0]
    assert current["pool_revision"] > initial["pool_revision"]
    back = {
        "company_id": None,
        "expected_company_id": company["id"],
        "expected_revision": current["pool_revision"],
    }
    assert (await api.patch(path, json=back)).status_code == 200
    delayed = await api.patch(
        path, json={**next_pool, "expected_revision": initial["pool_revision"]}
    )
    assert delayed.status_code == 409 and delayed.json()["detail"] == "Computer pool changed", (
        delayed.text
    )
    returned = (await api.get(PATH, params={"computer_id": id_})).json()["items"][0]
    assert (
        returned["pool"] == initial["pool"] and returned["pool_revision"] > current["pool_revision"]
    )
