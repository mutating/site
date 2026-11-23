"""Connected-device inventory and ownership with a signed, attested worker."""

import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from alembic import command
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from throng_api.cli import migration_config
from throng_api.models import AgentCredential, AvailabilityBaseline, Computer

from .conftest import proof_headers

pytestmark = pytest.mark.anyio
PATH = "/v1/connected-computers"


@pytest.fixture
async def connected_worker(client, bound_device, clock):
    """Open an attested agent session and publish available resources."""
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
    assert reply.json() == {
        "items": [
            {
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
        ],
        "total": 1,
        "online": 1,
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
    """Return the verified pool to the attested agent while preserving account-scoped recovery."""
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
    assert (await client.get(details, headers=agent_headers)).status_code == 401
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
    assert (await api.get(PATH)).json()["items"][0]["pool"]["company_id"] is None
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


async def test_transfer_receipt_checks_read_access_after_unlinking(api, company, connected_worker):
    """An unlinked computer still has a readable card for its registering account."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": company["id"], "expected_company_id": None}
    headers = {"Idempotency-Key": str(uuid4())}
    assert (await api.patch(path, json=payload, headers=headers)).status_code == 200
    ownerships = await api.get("/v1/computer-ownerships")
    owner = next(row for row in ownerships.json() if row["computer_id"] == computer_id)
    assert (await api.delete(f"/v1/computer-ownerships/{owner['id']}")).status_code == 204
    assert (await api.get(PATH, params={"computer_id": computer_id})).json()["total"] == 0
    assert (await api.get(f"/v1/computers/{computer_id}")).status_code == 200
    confirmed = await api.post(f"{path}/reconcile", json=payload, headers=headers)
    assert confirmed.status_code == 200 and confirmed.json() is True
    assert confirmed.headers["x-computer-read-allowed"] == "true"
    assert confirmed.headers["x-computer-pool-assigned"] == "false"
    assert "x-organization-id" not in confirmed.headers


async def test_enrollment_completed_with_an_api_token_is_excluded(
    api, user, verified_enrollment, clock
):
    """Limit the personal connected inventory to accounts that used password sign-in."""
    issued = await api.post(
        "/v1/api-tokens",
        json={"user_id": user["id"], "name": "Enrollment", "scopes": ["manage"]},
    )
    assert issued.status_code == 201, issued.text
    identity = verified_enrollment
    path = f"/v1/agent-enrollments/{identity.order.enrollment.row['id']}/finish"
    response = await api.post(
        path,
        content=b"{}",
        headers=proof_headers(
            identity.key,
            identity.certificate,
            "POST",
            path,
            b"{}",
            {
                "Authorization": "Bearer " + issued.json()["token"],
                "Idempotency-Key": str(uuid4()),
            },
            clock.now,
        ),
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


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_upgrade_imports_existing_attested_binding(api, bound_device, computer, settings):
    """Import the account of a previously enrolled and attested computer."""
    await reapply_connection_migration(settings)
    page = (await api.get(PATH)).json()
    assert [item["id"] for item in page["items"]] == [bound_device.binding["computer_id"]]
    assert computer["id"] != page["items"][0]["id"]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("key_issued_before_upgrade", [False, True])
async def test_legacy_personal_transfer_preserves_new_account_after_migration(
    api,
    other_api,
    other_user,
    company,
    bound_device,
    settings,
    client,
    clock,
    key_issued_before_upgrade,
):
    """Associate a confirmed personal handoff with the new account."""
    computer_id = bound_device.binding["computer_id"]
    grant = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "admin",
        },
    )
    assert grant.status_code == 201, grant.text
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    owner_path = f"/v1/computer-ownerships/{owner['id']}"
    company_owner = {"company_id": company["id"]}
    assert (await api.patch(owner_path, json=company_owner)).status_code == 200
    personal = await other_api.patch(owner_path, json={"user_id": other_user["id"]})
    assert personal.status_code == 200, personal.text
    credentials = f"/v1/computers/{computer_id}/credentials"
    received = await other_api.post(credentials) if key_issued_before_upgrade else None
    await reapply_connection_migration(settings)
    assert (await api.get(PATH)).json()["items"] == []
    assert (await other_api.get(PATH)).json()["items"] == []
    if received is None:
        received = await other_api.post(credentials)
    assert received.status_code == 201, received.text
    await start_replacement_session(
        client,
        bound_device,
        received.json()["token"],
        clock,
        publish_capacity=True,
    )
    personal_items = (await other_api.get(PATH)).json()["items"]
    assert [row["id"] for row in personal_items] == [computer_id]
    assert personal_items[0]["pool"] == {"company_id": None, "name": None}

    returned = await other_api.patch(owner_path, json=company_owner)
    assert returned.status_code == 200, returned.text
    await reapply_connection_migration(settings)
    move_path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": None, "expected_company_id": company["id"]}
    assert (await api.get(PATH)).json()["items"] == []
    assert (await api.patch(move_path, json=payload)).status_code == 404
    assert [row["id"] for row in (await other_api.get(PATH)).json()["items"]] == [computer_id]
    restored = await other_api.post(credentials)
    assert restored.status_code == 201, restored.text
    await start_replacement_session(client, bound_device, restored.json()["token"], clock)
    shifted = await other_api.patch(move_path, json=payload)
    assert shifted.status_code == 200, shifted.text
    shifted_back = await other_api.patch(
        move_path, json={**company_owner, "expected_company_id": None}
    )
    assert shifted_back.status_code == 200, shifted_back.text
    assert (await other_api.get(PATH)).json()["items"][0]["pool"] == shifted_back.json()


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("previous_pool", ["unconfirmed_personal", "confirmed_organization"])
async def test_migration_recovers_previous_signed_account_when_legacy_target_was_changed(
    api,
    other_api,
    other_user,
    company,
    bound_device,
    settings,
    client,
    clock,
    previous_pool,
):
    """Keep the last verified account visible while a later transfer awaits proof."""
    computer_id = bound_device.binding["computer_id"]
    grant = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "admin",
        },
    )
    assert grant.status_code == 201, grant.text
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    ownership = f"/v1/computer-ownerships/{owner['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    first_pool = {"company_id": company["id"]}
    assert (await api.patch(ownership, json=first_pool)).status_code == 200
    if previous_pool == "unconfirmed_personal":
        target = {"user_id": other_user["id"]}
        expected_account, other_account = api, other_api
    else:
        new_pool = await other_api.post("/v1/companies", json={"name": "Other organization"})
        assert new_pool.status_code == 201, new_pool.text
        target = {"company_id": new_pool.json()["id"]}
        expected_account, other_account = other_api, api
    assert (await other_api.patch(ownership, json=target)).status_code == 200
    if previous_pool == "confirmed_organization":
        key = await other_api.post(credentials)
        assert key.status_code == 201, key.text
        await start_replacement_session(
            client, bound_device, key.json()["token"], clock, publish_capacity=True
        )
    assert (await other_api.patch(ownership, json=first_pool)).status_code == 200
    key = await other_api.post(credentials)
    assert key.status_code == 201, key.text
    await reapply_connection_migration(settings)
    rows = (await expected_account.get(PATH)).json()["items"]
    assert [row["id"] for row in rows] == [computer_id]
    assert rows[0]["transfer_pending"] and not rows[0]["can_move"]
    assert (await other_account.get(PATH)).json()["items"] == []
    await start_replacement_session(client, bound_device, key.json()["token"], clock)
    assert (await api.get(PATH)).json()["items"] == []
    assert [row["id"] for row in (await other_api.get(PATH)).json()["items"]] == [computer_id]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize(
    "legacy_stage",
    ["before_issuance", "after_issuance", "after_issuance_retry", "archived_resources"],
)
async def test_legacy_organization_transfer_with_replacement_key_preserves_new_account(
    api,
    other_api,
    app,
    user,
    other_user,
    client,
    company,
    bound_device,
    settings,
    clock,
    legacy_stage,
):
    """Promote the account of an organization transfer after its key is confirmed."""
    computer_id = bound_device.binding["computer_id"]
    grant = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "admin",
        },
    )
    assert grant.status_code == 201, grant.text
    target = await other_api.post("/v1/companies", json={"name": "New owner"})
    assert target.status_code == 201, target.text
    target_id = target.json()["id"]
    shared = await other_api.post(
        "/v1/memberships",
        json={"company_id": target_id, "user_id": user["id"], "role": "admin"},
    )
    assert shared.status_code == 201, shared.text
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    owner_path = f"/v1/computer-ownerships/{owner['id']}"
    credentials_path = f"/v1/computers/{computer_id}/credentials"
    assert (await api.patch(owner_path, json={"company_id": company["id"]})).status_code == 200
    # An administrator who did not perform the handoff can replace a key without
    # adopting the computer into their account.
    assert (await other_api.post(credentials_path)).status_code == 201
    assert (await other_api.get(PATH)).json()["items"] == []

    if legacy_stage == "archived_resources":
        resources = await api.get(f"/v1/computers/{computer_id}/resources")
        assert resources.status_code == 200 and len(resources.json()) == 2, resources.text
        for resource in resources.json():
            assert resource["computer_id"] == computer_id
            result = await api.delete(f"/v1/computer-resources/{resource['id']}")
            assert result.status_code == 204, result.text
    moved = await other_api.patch(owner_path, json={"company_id": target_id})
    assert moved.status_code == 200, moved.text
    saved_token = None
    if legacy_stage.startswith("after_issuance"):
        issued = await other_api.post(credentials_path)
        assert issued.status_code == 201, issued.text
        saved_token = issued.json()["token"]
    if legacy_stage == "after_issuance":
        # The ledger can keep a timestamp beyond wall time after a correction.
        async with app.state.db.write() as database:
            await database.execute(
                update(AgentCredential)
                .where(AgentCredential.computer_id == computer_id)
                .values(created_at=clock.now - timedelta(minutes=10))
            )
    if legacy_stage != "archived_resources":
        await reapply_connection_migration(settings)
    assert (await other_api.get(PATH)).json()["items"] == []
    path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": None, "expected_company_id": target_id}
    pending_row = (await api.get(PATH)).json()["items"][0]
    assert pending_row["transfer_pending"] and not pending_row["can_move"]
    blocked = await api.patch(path, json=payload)
    assert (
        blocked.status_code == 409
        and blocked.json()["detail"] == "Computer has an unfinished native transfer"
    )
    if saved_token is None or legacy_stage == "after_issuance_retry":
        replacement = await other_api.post(credentials_path)
        assert replacement.status_code == 201, replacement.text
        retried = await other_api.post(credentials_path)
        assert retried.status_code == 201, retried.text
        saved_token = retried.json()["token"]
    unsigned = await client.post(
        "/agent/v1/session",
        json={"clean_start": True},
        headers={
            "Authorization": "Bearer " + saved_token,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert unsigned.status_code == 401, unsigned.text
    assert (await other_api.get(PATH)).json()["items"] == []
    assert (await api.get(PATH)).json()["items"][0]["transfer_pending"]
    assert (await api.patch(path, json=payload)).status_code == 409
    await start_replacement_session(client, bound_device, saved_token, clock)
    if legacy_stage == "before_issuance":
        await reapply_connection_migration(settings)
    assert (await api.get(PATH)).json()["items"] == []
    current = (await other_api.get(PATH)).json()["items"][0]
    assert current["id"] == computer_id and current["online"] and current["can_move"]
    assert not current["transfer_pending"]
    back = await other_api.patch(path, json=payload)
    assert back.status_code == 200, back.text


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("key_confirmed_before_upgrade", [False, True])
async def test_migration_uses_newer_verified_history_after_return_to_an_old_pool(
    api,
    other_api,
    other_user,
    company,
    bound_device,
    client,
    clock,
    settings,
    key_confirmed_before_upgrade,
):
    """Prefer newer signed evidence when the computer returns to an earlier pool."""
    computer_id = bound_device.binding["computer_id"]
    membership = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "admin",
        },
    )
    assert membership.status_code == 201, membership.text
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    ownership = f"/v1/computer-ownerships/{owner['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    resources_path = f"/v1/computers/{computer_id}/resources"

    async def archive(http):
        before = await http.get(resources_path)
        assert before.status_code == 200 and len(before.json()) == 2, before.text
        for resource in before.json():
            result = await http.delete(f"/v1/computer-resources/{resource['id']}")
            assert result.status_code == 204, result.text
        return before.json()

    async def issue(http):
        result = await http.post(credentials)
        assert result.status_code == 201, result.text
        return result.json()["token"]

    pool = {"company_id": company["id"]}
    assert (await api.patch(ownership, json=pool)).status_code == 200
    await start_replacement_session(
        client, bound_device, await issue(api), clock, publish_capacity=True
    )
    clock.now += timedelta(minutes=1)
    resources = await archive(api)
    assert (await other_api.patch(ownership, json={"user_id": other_user["id"]})).status_code == 200
    for resource in resources:
        restored = await other_api.post(
            resources_path,
            json={
                "resource_type_id": resource["resource_type_id"],
                "total": resource["total"],
            },
        )
        assert restored.status_code == 201, restored.text
    await start_replacement_session(
        client, bound_device, await issue(other_api), clock, publish_capacity=True
    )
    clock.now += timedelta(minutes=1)
    await archive(other_api)
    assert (await other_api.patch(ownership, json=pool)).status_code == 200
    key = await issue(other_api)
    if key_confirmed_before_upgrade:
        await start_replacement_session(client, bound_device, key, clock)

    await reapply_connection_migration(settings)
    rebuilt = (await other_api.get(PATH)).json()["items"]
    assert [row["id"] for row in rebuilt] == [computer_id]
    assert rebuilt[0]["transfer_pending"] is not key_confirmed_before_upgrade
    if not key_confirmed_before_upgrade:
        await start_replacement_session(client, bound_device, key, clock)
    assert (await api.get(PATH)).json()["items"] == []
    assert [row["id"] for row in (await other_api.get(PATH)).json()["items"]] == [computer_id]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("scenario", ["repeated_pool", "deleted_active", "deleted_before_pending"])
async def test_migration_preserves_the_latest_signed_pool_account(
    client, api, other_api, other_user, company, bound_device, clock, settings, scenario
):
    """Identify the last signed actor through repeated visits and account deletion."""
    profile = {
        "email": "carol@example.com",
        "password": "long test password",
        "first_name": "Carol",
        "last_name": "Example",
        "nickname": "carol",
    }
    registered = await client.post("/v1/users", json=profile)
    assert registered.status_code == 201, registered.text
    login = await client.post(
        "/v1/auth/login", json={key: profile[key] for key in ("email", "password")}
    )
    assert login.status_code == 200, login.text
    async with AsyncClient(
        base_url=client.base_url,
        timeout=10,
        trust_env=False,
        headers={"Authorization": "Bearer " + login.json()["access_token"]},
    ) as third_api:
        second = await other_api.post("/v1/companies", json={"name": "Second pool"})
        final = await api.post("/v1/companies", json={"name": "Final pool"})
        assert second.status_code == final.status_code == 201
        third_user_id = registered.json()["id"]
        third_pool_id = company["id"] if scenario == "repeated_pool" else final.json()["id"]
        third_memberships = []
        for manager, pool_id, user_id in [
            (api, company["id"], other_user["id"]),
            (api, third_pool_id, third_user_id),
            (other_api, second.json()["id"], third_user_id),
        ]:
            granted = await manager.post(
                "/v1/memberships",
                json={
                    "company_id": pool_id,
                    "user_id": user_id,
                    "role": "admin",
                },
            )
            assert granted.status_code == 201, granted.text
            if user_id == third_user_id:
                third_memberships.append(granted.json()["id"])
        computer_id = bound_device.binding["computer_id"]
        owner = next(
            row
            for row in (await api.get("/v1/computer-ownerships")).json()
            if row["computer_id"] == computer_id
        )
        ownership = f"/v1/computer-ownerships/{owner['id']}"
        credentials = f"/v1/computers/{computer_id}/credentials"
        for actor, pool_id in [
            (api, company["id"]),
            (other_api, second.json()["id"]),
            (third_api, third_pool_id),
        ]:
            moved = await actor.patch(ownership, json={"company_id": pool_id})
            assert moved.status_code == 200, moved.text
            issued = await actor.post(credentials)
            assert issued.status_code == 201, issued.text
            await start_replacement_session(
                client,
                bound_device,
                issued.json()["token"],
                clock,
                publish_capacity=True,
            )
            clock.now += timedelta(seconds=10)

        assert [row["id"] for row in (await third_api.get(PATH)).json()["items"]] == [computer_id]
        if scenario != "repeated_pool":
            for membership_id in third_memberships:
                left = await third_api.delete(f"/v1/memberships/{membership_id}")
                assert left.status_code == 204, left.text
            deleted = await third_api.delete(f"/v1/users/{third_user_id}")
            assert deleted.status_code == 204, deleted.text
        if scenario == "deleted_active":
            await reapply_connection_migration(settings)
            assert (await api.get(PATH)).json()["items"] == []
            assert (await other_api.get(PATH)).json()["items"] == []
            return

        destination = final.json()["id"] if scenario == "repeated_pool" else company["id"]
        pending = await api.patch(ownership, json={"company_id": destination})
        assert pending.status_code == 200, pending.text
        issued = await api.post(credentials)
        assert issued.status_code == 201, issued.text
        await reapply_connection_migration(settings)
        assert (await api.get(PATH)).json()["items"] == []
        assert (await other_api.get(PATH)).json()["items"] == []
        if scenario == "repeated_pool":
            rows = (await third_api.get(PATH)).json()["items"]
            assert [row["id"] for row in rows] == [computer_id]
            assert rows[0]["transfer_pending"] and not rows[0]["online"]
        await start_replacement_session(client, bound_device, issued.json()["token"], clock)
        if scenario == "repeated_pool":
            assert (await third_api.get(PATH)).json()["items"] == []
        assert [row["id"] for row in (await api.get(PATH)).json()["items"]] == [computer_id]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("resource_change", ["unchanged", "archived_one", "replaced"])
async def test_migration_recognizes_a_signed_session_before_capacity_was_published(
    api,
    other_api,
    user,
    other_user,
    company,
    client,
    bound_device,
    clock,
    settings,
    resource_change,
):
    """Use verified presence when a device entered the pool while paused."""
    other_pool = await other_api.post("/v1/companies", json={"name": "Paused pool"})
    assert other_pool.status_code == 201, other_pool.text
    other_pool_id = other_pool.json()["id"]
    for manager, pool_id, guest in [
        (api, company["id"], other_user),
        (other_api, other_pool_id, user),
    ]:
        member = await manager.post(
            "/v1/memberships",
            json={
                "company_id": pool_id,
                "user_id": guest["id"],
                "role": "admin",
            },
        )
        assert member.status_code == 201, member.text
    computer_id = bound_device.binding["computer_id"]
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    ownership = f"/v1/computer-ownerships/{owner['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    for manager, pool_id, publish_capacity in [
        (api, company["id"], True),
        (other_api, other_pool_id, False),
    ]:
        moved = await manager.patch(ownership, json={"company_id": pool_id})
        assert moved.status_code == 200, moved.text
        key = await manager.post(credentials)
        assert key.status_code == 201, key.text
        if not publish_capacity and resource_change != "unchanged":
            resources_path = f"/v1/computers/{computer_id}/resources"
            resources = (await manager.get(resources_path)).json()
            assert len(resources) == 2
            first, second = resources
            archived = await manager.delete(f"/v1/computer-resources/{first['id']}")
            assert archived.status_code == 204, archived.text
            if resource_change == "replaced":
                restored = await manager.post(
                    resources_path,
                    json={
                        "resource_type_id": first["resource_type_id"],
                        "total": first["total"],
                    },
                )
                assert restored.status_code == 201, restored.text
                archived = await manager.delete(f"/v1/computer-resources/{second['id']}")
                assert archived.status_code == 204, archived.text
        clock.now += timedelta(seconds=10)
        await start_replacement_session(
            client,
            bound_device,
            key.json()["token"],
            clock,
            publish_capacity=publish_capacity,
        )
        clock.now += timedelta(seconds=10)

    pending = await api.patch(ownership, json={"company_id": company["id"]})
    assert pending.status_code == 200, pending.text
    key = await api.post(credentials)
    assert key.status_code == 201, key.text
    for upgraded in (False, True):
        if upgraded:
            await reapply_connection_migration(settings)
        assert (await api.get(PATH)).json()["items"] == []
        rows = (await other_api.get(PATH)).json()["items"]
        assert [row["id"] for row in rows] == [computer_id]
        assert rows[0]["transfer_pending"] and not rows[0]["online"]
    await start_replacement_session(client, bound_device, key.json()["token"], clock)
    assert (await other_api.get(PATH)).json()["items"] == []
    assert [row["id"] for row in (await api.get(PATH)).json()["items"]] == [computer_id]


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("previously_shared", [False, True])
async def test_migrated_archive_still_blocks_a_browser_move_until_the_new_key_connects(
    api,
    other_api,
    other_user,
    company,
    bound_device,
    settings,
    client,
    clock,
    previously_shared,
):
    """Keep an archived native transfer pending until its replacement key is confirmed."""
    computer_id = bound_device.binding["computer_id"]
    target = await api.post("/v1/companies", json={"name": "Next organization"})
    assert target.status_code == 201, target.text
    target_id = target.json()["id"]
    for company_id in [company["id"], target_id]:
        access = await api.post(
            "/v1/memberships",
            json={
                "company_id": company_id,
                "user_id": other_user["id"],
                "role": "admin",
            },
        )
        assert access.status_code == 201, access.text
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    ownership = f"/v1/computer-ownerships/{owner['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    assert (await api.patch(ownership, json={"company_id": company["id"]})).status_code == 200
    issued = await api.post(credentials)
    assert issued.status_code == 201, issued.text
    if previously_shared:
        await start_replacement_session(
            client, bound_device, issued.json()["token"], clock, publish_capacity=True
        )
    resources = await api.get(f"/v1/computers/{computer_id}/resources")
    assert resources.status_code == 200 and resources.json(), resources.text
    for resource in resources.json():
        archived = await api.delete(f"/v1/computer-resources/{resource['id']}")
        assert archived.status_code == 204, archived.text
    moved = await other_api.patch(ownership, json={"company_id": target_id})
    assert moved.status_code == 200, moved.text
    replacement = await other_api.post(credentials)
    assert replacement.status_code == 201, replacement.text
    await reapply_connection_migration(settings)
    pending = (await api.get(PATH)).json()["items"][0]
    assert pending["transfer_pending"] and not pending["can_move"]
    move_path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": None, "expected_company_id": target_id}
    blocked = await api.patch(move_path, json=payload)
    assert blocked.status_code == 409, blocked.text
    token = replacement.json()["token"]
    if previously_shared:
        await start_replacement_session(client, bound_device, token, clock)
    else:
        path = "/agent/v1/connection/confirm"
        content = json.dumps({"computer_id": computer_id}).encode()
        signed = await client.post(
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
                },
                clock.now,
            ),
        )
        assert signed.status_code == 204, signed.text
    verified = (await api.get(PATH)).json()["items"][0]
    assert not verified["transfer_pending"]
    assert verified["online"] == previously_shared
    assert (await api.patch(move_path, json=payload)).status_code == 200


@pytest.mark.parametrize("same_account", [False, True])
async def test_saved_key_can_be_checked_by_legacy_transfer_account_without_exposing_inventory(
    api,
    other_api,
    user,
    other_user,
    company,
    bound_device,
    client,
    clock,
    same_account,
    access_token,
):
    """Verify and recover the signed key with narrow resource management access."""
    computer_id = bound_device.binding["computer_id"]
    original = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    ownership = f"/v1/computer-ownerships/{original['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    recovery = f"{PATH}/{computer_id}/recovery"
    login, manager, account = (
        (api, other_api, user) if same_account else (other_api, api, other_user)
    )
    if not same_account:
        joined = await api.post(
            "/v1/memberships",
            json={
                "company_id": company["id"],
                "user_id": other_user["id"],
                "role": "admin",
            },
        )
        assert joined.status_code == 201, joined.text
        assert (await api.patch(ownership, json={"company_id": company["id"]})).status_code == 200
        source_key = await api.post(credentials)
        assert source_key.status_code == 201, source_key.text
        await start_replacement_session(client, bound_device, source_key.json()["token"], clock)

    target = await manager.post("/v1/companies", json={"name": "Manage-only target"})
    assert target.status_code == 201, target.text
    target_id = target.json()["id"]
    membership_data = {
        "company_id": target_id,
        "user_id": account["id"],
        "role": "user",
        "permissions": ["resources.manage"],
    }
    membership = await manager.post("/v1/memberships", json=membership_data)
    assert membership.status_code == 201, membership.text
    move = await login.patch(ownership, json={"company_id": target_id})
    assert move.status_code == 200, move.text
    repeated = await manager.patch(ownership, json={"company_id": target_id})
    assert repeated.status_code == 200, repeated.text
    issued = await login.post(credentials)
    assert issued.status_code == 201, issued.text
    key = issued.json()["token"]
    assert all(
        row["computer_id"] != computer_id
        for row in (await login.get("/v1/computer-ownerships")).json()
    )
    inventory = (await login.get(PATH)).json()["items"]
    if same_account:
        assert inventory[0]["transfer_pending"] and not inventory[0]["can_move"]
        assert (await manager.get(recovery)).json() is False
    else:
        assert inventory == []
    assert (await login.get(recovery)).json() is True
    revision = (await login.get(recovery, params={"snapshot": "true"})).json()["revision"]
    assert revision > move.json()["revision"]

    removed = await manager.delete(f"/v1/memberships/{membership.json()['id']}")
    assert removed.status_code == 204, removed.text
    assert (await login.get(recovery)).json() is False
    assert (await login.get(recovery, params={"snapshot": "true"})).json() == {"revision": None}
    assert (await manager.post("/v1/memberships", json=membership_data)).status_code == 201

    session_path = "/agent/v1/session?include_pool=true"
    checked = await client.get(
        session_path,
        headers=proof_headers(
            bound_device.identity.key,
            bound_device.identity.certificate,
            "GET",
            session_path,
            b"",
            {"Authorization": "Bearer " + key},
            clock.now,
        ),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["session_id"] is None and checked.json()["transfer_pending"]
    assert "pool_owner" not in checked.json()
    intent = {
        "expected_user_id": account["id"],
        "expected_company_id": target_id,
        "expected_revision": revision,
    }
    wrong_account = await manager.post(recovery, json=intent)
    assert wrong_account.status_code == 403, wrong_account.text
    if same_account:
        from_token = await client.post(
            recovery, json=intent, headers={"Authorization": "Bearer " + access_token["token"]}
        )
        assert from_token.status_code == 403, from_token.text
        assert from_token.json()["detail"] == "The account must sign in to recover a transfer"
    stale_intent = await login.post(recovery, json={**intent, "expected_company_id": None})
    assert stale_intent.status_code == 409, stale_intent.text
    replacement = await login.post(recovery, json=intent)
    assert replacement.status_code == 201, replacement.text
    recovered_key = replacement.json()["token"]
    assert recovered_key != key
    confirmed_owner = replacement.json()["ownership"]
    assert (
        confirmed_owner["computer_id"] == computer_id and confirmed_owner["company_id"] == target_id
    )
    assert confirmed_owner["revision"] > revision
    duplicate = await login.post(recovery, json=intent)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "computer_pool_changed"

    confirm_path = "/agent/v1/connection/confirm"

    async def confirm(token, target_computer=computer_id, *, signed=True):
        body = json.dumps({"computer_id": target_computer}).encode()
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        if signed:
            headers = proof_headers(
                bound_device.identity.key,
                bound_device.identity.certificate,
                "POST",
                confirm_path,
                body,
                headers,
                clock.now,
            )
        return await client.post(confirm_path, content=body, headers=headers)

    assert (await confirm(recovered_key, signed=False)).status_code == 401
    assert (await confirm(recovered_key, str(uuid4()))).status_code == 404
    assert (await confirm(key)).status_code == 401
    personal_pool = {"company_id": None, "expected_company_id": target_id}
    blocked = await login.patch(f"{PATH}/{computer_id}/pool", json=personal_pool)
    assert blocked.status_code == (409 if same_account else 404), blocked.text
    if same_account:
        assert blocked.json()["detail"] == "Computer has an unfinished native transfer"
    assert (await confirm(recovered_key)).status_code == 204
    assert (await confirm(recovered_key)).status_code == 204
    checked = await client.get(
        session_path,
        headers=proof_headers(
            bound_device.identity.key,
            bound_device.identity.certificate,
            "GET",
            session_path,
            b"",
            {"Authorization": "Bearer " + recovered_key},
            clock.now,
        ),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["session_id"] is None
    assert not checked.json()["transfer_pending"] and not checked.json()["active"]
    assert checked.json()["pool_owner"] == {"user_id": None, "company_id": target_id}
    current = (await login.get(PATH)).json()["items"][0]
    assert (
        current["id"] == computer_id
        and current["can_move"]
        and not current["transfer_pending"]
        and not current["online"]
    )
    assert (await manager.get(recovery)).json() is False
    assert (await login.post(recovery, json=intent)).status_code == 404
    moved = await login.patch(f"{PATH}/{computer_id}/pool", json=personal_pool)
    assert moved.status_code == 200, moved.text


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
async def test_web_move_waits_for_same_account_native_recovery(
    api, client, company, bound_device, settings, clock
):
    """Serialize a browser move with recovery of an interrupted transfer by the same account."""
    computer_id = bound_device.binding["computer_id"]
    owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    legacy_path = f"/v1/computer-ownerships/{owner['id']}"
    credentials = f"/v1/computers/{computer_id}/credentials"
    assert (await api.patch(legacy_path, json={"company_id": company["id"]})).status_code == 200
    # The server issued a key, but the old app may still be recovering a lost reply.
    assert (await api.post(credentials)).status_code == 201
    path = f"{PATH}/{computer_id}/pool"
    payload = {"company_id": None, "expected_company_id": company["id"]}
    assert (await api.patch(path, json=payload)).status_code == 409
    await reapply_connection_migration(settings)
    row = (await api.get(PATH)).json()["items"][0]
    assert row["transfer_pending"] and not row["can_move"]
    assert (await api.patch(path, json=payload)).status_code == 409
    retried = await api.post(credentials)
    assert retried.status_code == 201, retried.text
    await start_replacement_session(client, bound_device, retried.json()["token"], clock)
    assert (await api.patch(path, json=payload)).status_code == 200


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize(
    ("detach_route", "action_after_detach"),
    [
        ("unbind", "reattach_only"),
        ("delete_owner", "reattach_only"),
        ("delete_owner", "issue_key"),
        ("delete_owner", "change_owner"),
    ],
)
async def test_detach_removes_connection_even_if_an_owner_is_added_again(
    api,
    user,
    connected_worker,
    settings,
    detach_route,
    action_after_detach,
    client,
    access_token,
    company,
    clock,
):
    """Keep a detached device out of the account inventory after owner recreation and upgrade."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    assert (await api.get(PATH)).json()["total"] == 1
    clock.now += timedelta(seconds=1)
    if detach_route == "unbind":
        detached = await api.post(
            f"/v1/computers/{computer_id}/unbind",
            json={"cleanup_confirmed": True},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert detached.status_code == 200, detached.text
    else:
        owner = next(
            row
            for row in (await api.get("/v1/computer-ownerships")).json()
            if row["computer_id"] == computer_id
        )
        detached = await api.delete(f"/v1/computer-ownerships/{owner['id']}")
        assert detached.status_code == 204, detached.text
    reattached = await api.post(
        "/v1/computer-ownerships",
        json={"computer_id": computer_id, "user_id": user["id"]},
    )
    assert reattached.status_code == 201, reattached.text
    token_headers = {"Authorization": "Bearer " + access_token["token"]}
    if action_after_detach == "issue_key":
        reissued = await client.post(
            f"/v1/computers/{computer_id}/credentials",
            headers=token_headers,
        )
        assert reissued.status_code == 201, reissued.text
    if action_after_detach == "change_owner":
        changed = await client.patch(
            f"/v1/computer-ownerships/{reattached.json()['id']}",
            json={"company_id": company["id"]},
            headers=token_headers,
        )
        assert changed.status_code == 200, changed.text
    assert (await api.get(PATH)).json()["items"] == []
    await reapply_connection_migration(settings)
    assert (await api.get(PATH)).json()["items"] == []
    move = await api.patch(
        f"{PATH}/{computer_id}/pool",
        json={"company_id": None, "expected_company_id": None},
    )
    assert move.status_code == 404


@pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True)
@pytest.mark.parametrize("confirmed", [False, True])
async def test_migration_preserves_a_post_detach_handoff_when_the_clock_moves_back(
    api, user, company, bound_device, connected_worker, settings, client, clock, confirmed
):
    """Preserve post-detach signed sessions and recoverable handoffs across a clock correction."""
    session, _ = connected_worker
    computer_id = session["computer_id"]
    old_owner = next(
        row
        for row in (await api.get("/v1/computer-ownerships")).json()
        if row["computer_id"] == computer_id
    )
    clock.now += timedelta(seconds=15)
    detached = await api.delete(f"/v1/computer-ownerships/{old_owner['id']}")
    assert detached.status_code == 204, detached.text
    reattached = await api.post(
        "/v1/computer-ownerships", json={"computer_id": computer_id, "user_id": user["id"]}
    )
    assert reattached.status_code == 201, reattached.text
    handed_off = await api.patch(
        f"/v1/computer-ownerships/{reattached.json()['id']}", json={"company_id": company["id"]}
    )
    assert handed_off.status_code == 200, handed_off.text
    issued = await api.post(f"/v1/computers/{computer_id}/credentials")
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    if confirmed:
        clock.now -= timedelta(seconds=5)
        current = await start_replacement_session(client, bound_device, token, clock)
        assert current["session_id"] != session["session_id"]

    before = (await api.get(PATH)).json()
    assert before["total"] == before["online"] == int(confirmed)
    await reapply_connection_migration(settings)
    after = (await api.get(PATH)).json()
    assert after["total"] == after["online"] == int(confirmed)
    if not confirmed:
        await start_replacement_session(client, bound_device, token, clock)
    connected = (await api.get(PATH)).json()
    assert connected["total"] == connected["online"] == 1
    assert connected["items"][0]["id"] == computer_id
    assert connected["items"][0]["pool"]["company_id"] == company["id"]


async def test_move_preserves_signed_session_and_transfers_capacity(
    api, other_api, connected_worker, company, user, clock, app
):
    """Transfer available resources while preserving the signed agent session."""
    session, agent_post = connected_worker
    computer_id = session["computer_id"]
    path = f"{PATH}/{computer_id}/pool"
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
