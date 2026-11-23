"""Dashboard reports preserve availability, reservations and historical ownership."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select, update

from throng_api import resource_history
from throng_api.models import (
    CPU_RESOURCE_ID,
    AvailabilityBaseline,
    CapacityInterval,
    Computer,
    ComputerResource,
    ComputerResourceChange,
    Coordination,
    ResourceType,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def dashboard_clock(app, clock):
    """Use a deterministic clock after the migration's real availability baseline."""
    async with app.state.db.write() as session:
        baseline = await session.get(AvailabilityBaseline, 1)
        baseline.available_from = clock.now - timedelta(seconds=1)
    return clock


async def test_history_tracks_heartbeats_capacity_changes_and_expiry_without_controller(
    api, user, client, computer, app, dashboard_clock
):
    """Keep one interval per unchanged capacity and terminate at lease expiry even without a tick."""
    clock = dashboard_clock
    credential = (await api.post(f"/v1/computers/{computer['id']}/credentials")).json()["token"]
    headers = {"Authorization": "Bearer " + credential}
    started = clock.now
    handshake = await client.post(
        "/agent/v1/session",
        headers={**headers, "Idempotency-Key": "history-start"},
        json={"clean_start": True},
    )
    headers["X-Agent-Session"] = handshake.json()["session_id"]
    clock.now += timedelta(seconds=10)
    assert (await client.post("/agent/v1/heartbeat", headers=headers)).status_code == 200
    async with app.state.db.read() as session:
        assert await session.scalar(select(func.count()).select_from(CapacityInterval)) == 1
    clock.now += timedelta(seconds=10)
    changed = clock.now
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"cpu_millis": 4000})
    ).status_code == 200
    clock.now += timedelta(seconds=100)
    path = f"/v1/users/{user['id']}/resources/availability-history"
    report = (
        await api.get(
            path,
            params={"from": (started - timedelta(days=7)).isoformat(), "to": clock.now.isoformat()},
        )
    ).json()
    positive = [point for point in report["points"] if point["cpu_millis"]]
    assert [point["cpu_millis"] for point in positive] == [2000, 4000]
    assert positive[1]["at"] == changed.isoformat().replace("+00:00", "Z")
    assert report["points"][-1]["cpu_millis"] == 0
    assert report["points"][-1]["cpu_occupied_millis"] == 0
    summary = (
        await api.get(f"/v1/users/{user['id']}/resources/summary?only_available=true")
    ).json()
    assert summary["computer_count"] == 0 and summary["resources"] == []


async def test_available_computers_filter_owner_count_and_pagination(
    api, user, company_agent, agent_api, company, other_user
):
    """Apply owner and availability filters before count/pagination and reject foreign owners."""
    for owner in [{"user_id": user["id"]}, {"company_id": company["id"]}]:
        response = await api.get(
            "/v1/computers", params={**owner, "only_available": True, "limit": 1}
        )
        assert response.status_code == 200 and response.headers["x-total-count"] == "1"
        assert response.headers.get("x-organization-membership-id") == (
            company["my_membership_id"] if "company_id" in owner else None
        )
        assert len(response.json()) == 1
        selected = response.json()[0]["id"]
        for identity, count in [(selected, 1), (other_user["id"], 0)]:
            filtered = await api.get(
                "/v1/computers",
                params={**owner, "only_available": True, "computer_id": identity},
            )
            assert filtered.status_code == 200
            assert filtered.headers["x-total-count"] == str(count)
            assert [computer["id"] for computer in filtered.json()] == ([selected] if count else [])
        response = await api.get(
            "/v1/computers", params={**owner, "only_available": True, "offset": 1}
        )
        assert response.json() == [] and response.headers["x-total-count"] == "1"
    assert (await api.get("/v1/computers", params={"user_id": other_user["id"]})).status_code == 404
    assert (
        await api.get("/v1/computers", params={"user_id": user["id"], "company_id": company["id"]})
    ).status_code == 422


async def test_inventory_reports_only_the_requests_verified_membership(
    api, other_api, other_user, company, company_computer
):
    """Return the current membership on denied resource reads and omit it after membership loss."""
    created = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "user",
            "permissions": ["members.read"],
        },
    )
    assert created.status_code == 201, created.text
    membership_id = created.json()["id"]
    company_path = f"/v1/companies/{company['id']}"
    metadata = await other_api.get(company_path)
    assert metadata.status_code == 200
    assert metadata.json()["my_membership_id"] == membership_id
    assert int(metadata.headers["x-computer-pool-check-revision"]) >= 0
    team = await other_api.get("/v1/memberships", params={"company_id": company["id"]})
    assert team.status_code == 200
    assert team.headers["x-organization-membership-id"] == membership_id
    assert int(team.headers["x-computer-pool-check-revision"]) >= int(
        metadata.headers["x-computer-pool-check-revision"]
    )
    summary = await api.get(f"{company_path}/resources/summary")
    assert summary.status_code == 200
    assert summary.headers["x-organization-membership-id"] == company["my_membership_id"]
    assert int(summary.headers["x-computer-pool-check-revision"]) >= 0
    requests = [
        ("/v1/computers", {"company_id": company["id"]}),
        (f"/v1/computers/{company_computer['id']}", {}),
        (f"{company_path}/resources/pool-history", {}),
        (f"{company_path}/resources/pool-overview", {}),
        (f"{company_path}/resources/pool-snapshot", {}),
        (f"{company_path}/resources/summary", {}),
    ]
    for route, params in requests:
        allowed = await api.get(route, params=params)
        assert allowed.status_code == 200
        assert allowed.headers["x-organization-membership-id"] == company["my_membership_id"]
        assert allowed.headers["x-organization-id"] == company["id"]
        denied = await other_api.get(route, params=params)
        assert denied.status_code == 403
        assert denied.headers["x-organization-membership-id"] == membership_id
        assert denied.headers["x-organization-id"] == company["id"]
        revision = "x-computer-pool-check-revision"
        if route.startswith(company_path):
            assert int(denied.headers[revision]) >= int(metadata.headers[revision])
        elif not params:
            assert int(allowed.headers[revision]) >= 0
            assert int(denied.headers[revision]) >= int(allowed.headers[revision])
            assert allowed.headers["x-computer-read-allowed"] == "true"
            assert denied.headers["x-computer-read-allowed"] == "false"

    changed = await api.patch(
        f"/v1/memberships/{membership_id}", json={"permissions": ["resources.manage"]}
    )
    assert changed.status_code == 200, changed.text
    denied_team = await other_api.get("/v1/memberships", params={"company_id": company["id"]})
    assert denied_team.status_code == 403
    assert denied_team.headers["x-organization-membership-id"] == membership_id
    assert int(denied_team.headers[revision]) > int(team.headers[revision])
    assert (await api.delete(f"/v1/memberships/{membership_id}")).status_code == 204
    missing = await other_api.get(company_path)
    assert missing.status_code == 404
    revision = "x-computer-pool-check-revision"
    assert int(missing.headers[revision]) > int(metadata.headers[revision])
    assert "x-organization-membership-id" not in missing.headers
    assert "x-organization-id" not in missing.headers
    unavailable_team = await other_api.get("/v1/memberships", params={"company_id": company["id"]})
    assert unavailable_team.status_code == 404
    assert int(unavailable_team.headers[revision]) >= int(missing.headers[revision])
    assert "x-organization-membership-id" not in unavailable_team.headers
    assert "x-organization-id" not in unavailable_team.headers
    for route, params in requests:
        unavailable = await other_api.get(route, params=params)
        assert unavailable.status_code == 404
        assert "x-organization-membership-id" not in unavailable.headers
        assert "x-organization-id" not in unavailable.headers
        if route.startswith(company_path):
            assert int(unavailable.headers[revision]) >= int(missing.headers[revision])
        else:
            assert revision not in unavailable.headers


async def test_empty_history_validation_and_owner_isolation(
    api, user, company, other_api, dashboard_clock
):
    """Bound query windows, exclude future history and authorize each owner independently."""
    clock = dashboard_clock
    path = f"/v1/users/{user['id']}/resources/availability-history"
    period = {"from": (clock.now - timedelta(days=7)).isoformat(), "to": clock.now.isoformat()}
    report = (await api.get(path, params=period)).json()
    assert all(point["cpu_millis"] == point["memory_bytes"] == 0 for point in report["points"])
    assert (await api.get(path)).json() == report
    for bounds in [
        {"from": period["from"]},
        {"to": period["to"]},
        {"from": clock.now.isoformat(), "to": clock.now.isoformat()},
        {"from": "2026-01-01", "to": clock.now.isoformat()},
        {"from": (clock.now - timedelta(days=32)).isoformat(), "to": clock.now.isoformat()},
        {"from": "0001-01-01T00:00:00+12:00", "to": "0001-01-02T00:00:00Z"},
    ]:
        assert (await api.get(path, params=bounds)).status_code == 422
    assert (await other_api.get(path, params=period)).status_code == 404
    company_path = f"/v1/companies/{company['id']}/resources/availability-history"
    chart = await api.get(company_path, params=period)
    missing = await other_api.get(company_path, params=period)
    assert chart.status_code == 200 and missing.status_code == 404
    revision = "x-computer-pool-check-revision"
    assert int(missing.headers[revision]) >= int(chart.headers[revision])
    assert chart.headers["x-organization-membership-id"] == company["my_membership_id"]
    assert "x-organization-membership-id" not in missing.headers
    future = {
        "from": (clock.now + timedelta(days=1)).isoformat(),
        "to": (clock.now + timedelta(days=2)).isoformat(),
    }
    assert (await api.get(path, params=future)).json()["points"] == []


async def test_history_coalesces_instant_changes_and_keeps_real_reservations(
    api, user, computer, run_task, dashboard_clock
):
    """Zero-duration changes cannot add capacity; a continuing lease retains task reservations."""
    clock = dashboard_clock
    started = clock.now
    clock.now += timedelta(seconds=1)
    for total in [3000, 2000]:
        response = await api.patch(f"/v1/computers/{computer['id']}", json={"cpu_millis": total})
        assert response.status_code == 200, response.text
    clock.now += timedelta(seconds=1)
    report = (
        await api.get(
            f"/v1/users/{user['id']}/resources/availability-history",
            params={"from": started.isoformat(), "to": clock.now.isoformat()},
        )
    ).json()
    assert len(report["points"]) == 2
    for point in report["points"]:
        assert point["cpu_millis"] == 2000 and point["memory_bytes"] == 2048
        assert point["cpu_occupied_millis"] == 1000
        assert point["memory_occupied_bytes"] == 1024


async def test_organization_pool_history_includes_offline_inventory(
    api, other_api, other_user, company, company_agent, dashboard_clock
):
    """Keep offline hardware in the totals but omit it from available CPU and memory."""
    clock = dashboard_clock
    active = company_agent["computer"]
    offline_cpu, offline_memory = 6000, 4 * 1024**3
    clock.now += timedelta(seconds=2)
    added_at = clock.now
    response = await api.post(
        "/v1/computers",
        json={
            "company_id": company["id"],
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": offline_cpu,
            "memory_bytes": offline_memory,
            "disk_bytes": 8192,
        },
    )
    assert response.status_code == 201, response.text
    clock.now += timedelta(seconds=2)
    path = f"/v1/companies/{company['id']}/resources/pool-history"
    report = await api.get(path)
    assert report.status_code == 200, report.text
    assert report.headers["x-organization-membership-id"] == company["my_membership_id"]
    revision = "x-computer-pool-check-revision"
    assert int(report.headers[revision]) > 0
    points = report.json()["points"]
    change = next(
        point for point in points if point["at"] == added_at.isoformat().replace("+00:00", "Z")
    )
    assert change["cpu_total_millis"] == active["cpu_millis"] + offline_cpu
    assert change["memory_total_bytes"] == active["memory_bytes"] + offline_memory
    assert change["cpu_free_millis"] == active["cpu_millis"]
    assert change["memory_free_bytes"] == active["memory_bytes"]
    missing = await other_api.get(path)
    assert missing.status_code == 404
    assert int(missing.headers[revision]) >= int(report.headers[revision])
    assert "x-organization-membership-id" not in missing.headers
    assert "x-organization-id" not in missing.headers
    member = await api.post(
        "/v1/memberships",
        json={
            "company_id": company["id"],
            "user_id": other_user["id"],
            "role": "user",
            "permissions": ["members.read"],
        },
    )
    assert member.status_code == 201, member.text
    denied = await other_api.get(path)
    assert denied.status_code == 403
    membership_id = member.json()["id"]
    assert denied.headers["x-organization-membership-id"] == membership_id
    assert int(denied.headers[revision]) > int(report.headers[revision])
    grant = await api.patch(
        f"/v1/memberships/{membership_id}", json={"permissions": ["resources.read"]}
    )
    assert grant.status_code == 200, grant.text
    allowed = await other_api.get(path)
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["x-organization-membership-id"] == membership_id
    assert int(allowed.headers[revision]) > int(denied.headers[revision])


async def test_org_pool_history_starts_when_both_reports_can_be_trusted(
    api, app, company, company_agent, dashboard_clock
):
    """Return no fabricated data before capacity or inventory tracking begins."""
    now = dashboard_clock.now
    path = f"/v1/companies/{company['id']}/resources/pool-history"
    async with app.state.db.write() as session:
        baseline = await session.get(AvailabilityBaseline, 1)
        baseline.available_from = now

    capacity_pending = await api.get(path)
    assert capacity_pending.status_code == 200, capacity_pending.text
    assert capacity_pending.json()["points"] == []

    async with app.state.db.write() as session:
        await session.execute(
            update(ComputerResourceChange)
            .where(ComputerResourceChange.computer_id == company_agent["computer"]["id"])
            .values(created_at=now + timedelta(seconds=2), reason="migration")
        )
    dashboard_clock.now = now + timedelta(seconds=1)
    inventory_pending = await api.get(path)
    assert inventory_pending.status_code == 200, inventory_pending.text
    assert inventory_pending.json()["points"] == []

    dashboard_clock.now = now + timedelta(seconds=3)
    available = await api.get(path)
    assert available.status_code == 200, available.text
    assert datetime.fromisoformat(available.json()["points"][0]["at"]) == now + timedelta(seconds=2)


async def test_org_history_reads_all_inventory_revisions(
    api, company, company_agent, dashboard_clock
):
    """Apply every visible inventory revision and preserve chronological totals and free capacity."""
    computer = company_agent["computer"]["id"]
    for cpu in [4000, 5000]:
        dashboard_clock.now += timedelta(seconds=1)
        update = await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": cpu})
        assert update.status_code == 200, update.text
    dashboard_clock.now += timedelta(seconds=1)

    response = await api.get(f"/v1/companies/{company['id']}/resources/pool-history")
    assert response.status_code == 200, response.text
    points = response.json()["points"]
    assert [(row["cpu_total_millis"], row["cpu_free_millis"]) for row in points] == [
        (0, 0),
        (2000, 2000),
        (4000, 4000),
        (5000, 5000),
        (5000, 5000),
    ]


async def test_org_history_preserves_inventory_changes_after_2000_revisions(
    api, app, company, company_agent, dashboard_clock
):
    """A busy window keeps late inventory extrema and current totals after downsampling."""
    computer_id = company_agent["computer"]["id"]
    start = dashboard_clock.now
    count, peak_revision = 2105, 2073
    async with app.state.db.write() as session:
        computer = await session.get(Computer, computer_id)
        resource = computer.resource(CPU_RESOURCE_ID)
        coordination = await session.get(Coordination, 1)
        total = resource.total
        changes = []
        for step in range(1, count + 1):
            next_total = 18000 if step == peak_revision else 3000 + (step % 2) * 1000
            changes.append(
                dict(
                    computer_id=computer_id,
                    computer_resource_id=resource.id,
                    resource_type_id=CPU_RESOURCE_ID,
                    revision=coordination.revision + step,
                    version=resource.version + step,
                    before_total=total,
                    after_total=next_total,
                    before_occupied=0,
                    after_occupied=0,
                    before_company_id=company["id"],
                    after_company_id=company["id"],
                    reason="resize",
                    created_at=start + timedelta(seconds=step),
                )
            )
            total = next_total
        await session.execute(insert(ComputerResourceChange), changes)
        coordination.revision += count
        resource.version += count
        resource.total = total
    dashboard_clock.now = start + timedelta(seconds=count + 1)
    result = await api.get(f"/v1/companies/{company['id']}/resources/pool-history")
    assert result.status_code == 200, result.text
    chart = result.json()
    assert chart["sampled"] is True
    peak = max(chart["points"], key=lambda point: point["cpu_total_millis"])
    assert peak["cpu_total_millis"] == 18000
    assert datetime.fromisoformat(peak["at"]) == start + timedelta(seconds=peak_revision)
    assert chart["points"][-1]["cpu_total_millis"] == total


async def test_org_history_reports_current_inventory_and_half_open_lease(
    api, company, company_agent, dashboard_clock
):
    """The final point includes writes and lease expiry exactly at the report time."""
    computer = company_agent["computer"]["id"]
    node = (await api.get(f"/v1/computers/{computer}")).json()
    dashboard_clock.now = datetime.fromisoformat(node["lease_expires_at"])
    assert (
        await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": 5000})
    ).status_code == 200
    history = (await api.get(f"/v1/companies/{company['id']}/resources/pool-history")).json()
    assert history["points"][-2]["cpu_free_millis"] == 2000
    assert history["points"][-1]["cpu_total_millis"] == 5000
    assert history["points"][-1]["cpu_free_millis"] == 0


async def test_personal_snapshot_reports_capacity_at_the_exact_expiry(
    api, user, agent, dashboard_clock
):
    """Keep the latest capacity reading aligned with the overview at the lease boundary."""
    computer = (await api.get(f"/v1/computers/{agent['computer']['id']}")).json()
    dashboard_clock.now = datetime.fromisoformat(computer["lease_expires_at"])
    report = await api.get(f"/v1/users/{user['id']}/resources/pool-snapshot")
    assert report.status_code == 200, report.text
    snapshot = report.json()
    overview, chart = snapshot["overview"], snapshot["history"]
    assert overview["inventory"]["computer_count"] == 1
    assert overview["available"]["computer_count"] == 0
    assert chart["to_time"] == overview["available"]["as_of"]
    assert chart["points"][-1]["cpu_millis"] == 0
    assert chart["points"][-1]["memory_bytes"] == 0
    assert chart["points"][-2]["cpu_millis"] == 2000


async def test_personal_snapshot_does_not_invent_history_before_the_baseline(api, user, app, clock):
    """Allow a current overview before an interval of known history has elapsed."""
    async with app.state.db.write() as session:
        baseline = await session.get(AvailabilityBaseline, 1)
        baseline.available_from = clock.now
    report = await api.get(f"/v1/users/{user['id']}/resources/pool-snapshot")
    assert report.status_code == 200, report.text
    snapshot = report.json()
    assert snapshot["overview"]["inventory"]["computer_count"] == 0
    assert snapshot["history"]["points"] == []


async def test_org_history_uses_one_snapshot_during_a_concurrent_resize(
    api, company, company_agent, dashboard_clock, monkeypatch
):
    """An inventory write between the two reads cannot alter half the report."""
    sampled = asyncio.Event()
    release = asyncio.Event()
    read_intervals = resource_history.capacity_history

    async def paused(*args, **kwargs):
        result = await read_intervals(*args, **kwargs)
        sampled.set()
        await release.wait()
        return result

    monkeypatch.setattr(resource_history, "capacity_history", paused)
    dashboard_clock.now += timedelta(seconds=3)
    path = f"/v1/companies/{company['id']}/resources/pool-history"
    reading = asyncio.create_task(api.get(path))
    try:
        await asyncio.wait_for(sampled.wait(), timeout=5)
        computer = company_agent["computer"]["id"]
        response = await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": 1000})
        assert response.status_code == 200, response.text
    finally:
        release.set()
        report = await reading
    assert report.status_code == 200, report.text
    assert report.json()["points"][-1]["cpu_total_millis"] == 2000
    assert report.json()["points"][-1]["cpu_free_millis"] == 2000


@pytest.mark.parametrize(
    "scope",
    [
        "personal",
        "personal-snapshot",
        "organization",
        "organization-summary",
        "organization-snapshot",
    ],
)
async def test_pool_summary_reads_both_views_from_one_snapshot(
    api, other_api, user, agent, company, company_agent, dashboard_clock, monkeypatch, scope
):
    """Keep totals, counts and the personal preview together during concurrent changes."""
    sampled = asyncio.Event()
    release = asyncio.Event()
    summarize = resource_history.pool_resource_totals

    async def paused(*args, **kwargs):
        result = await summarize(*args, **kwargs)
        sampled.set()
        await release.wait()
        return result

    organization = scope.startswith("organization")
    scope_path = f"companies/{company['id']}" if organization else f"users/{user['id']}"
    report_kind = {
        "organization-summary": "summary",
        "personal-snapshot": "snapshot",
        "organization-snapshot": "snapshot",
    }.get(scope, "overview")
    path = f"/v1/{scope_path}/resources/pool-{report_kind}"
    denied = await other_api.get(path)
    assert denied.status_code == 404
    assert "x-organization-membership-id" not in denied.headers
    monkeypatch.setattr(resource_history, "pool_resource_totals", paused)
    reading = asyncio.create_task(api.get(path))
    try:
        await asyncio.wait_for(sampled.wait(), timeout=5)
        computer = (company_agent if organization else agent)["computer"]["id"]
        response = await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": 4000})
        assert response.status_code == 200, response.text
        added = await api.post(
            "/v1/computers",
            json={
                **({"company_id": company["id"]} if organization else {}),
                "os": "linux",
                "architecture": "arm64",
                "runtime": "docker",
                "cpu_millis": 1000,
                "memory_bytes": 2048,
                "disk_bytes": 4096,
            },
        )
        assert added.status_code == 201, added.text
    finally:
        release.set()
        response = await reading
    assert response.status_code == 200, response.text
    if organization and report_kind != "summary":
        assert response.headers["x-organization-membership-id"] == company["my_membership_id"]
    snapshot = response.json()
    report = snapshot["overview"] if report_kind == "snapshot" else snapshot
    assert report["inventory"]["as_of"] == report["available"]["as_of"]
    assert report["inventory"]["computer_count"] == report["available"]["computer_count"] == 1
    for view in ["inventory", "available"]:
        assert (
            next(row for row in report[view]["resources"] if row["code"] == "cpu")["free"] == 2000
        )
    if report_kind != "summary":
        assert [(row["id"], row["cpu_millis"]) for row in report["computers"]] == [(computer, 2000)]
    if report_kind == "snapshot":
        assert snapshot["history"]["to_time"] == report["inventory"]["as_of"]
        capacity_field = "cpu_total_millis" if organization else "cpu_millis"
        assert snapshot["history"]["points"][-1][capacity_field] == 2000
    refreshed = (await api.get(path)).json()
    updated = refreshed["overview"] if report_kind == "snapshot" else refreshed
    assert updated["inventory"]["computer_count"] == 2
    assert updated["available"]["computer_count"] == 1
    assert (
        next(row for row in updated["available"]["resources"] if row["code"] == "cpu")["free"]
        == 4000
    )
    if report_kind != "summary":
        assert updated["check_revision"] > report["check_revision"]
        assert {row["id"]: row["cpu_millis"] for row in updated["computers"]} == {
            computer: 4000,
            added.json()["id"]: 1000,
        }
    if report_kind == "snapshot":
        assert refreshed["history"]["points"][-1][capacity_field] == (
            5000 if organization else 4000
        )


async def test_org_summary_keeps_exact_totals_and_per_computer_free_capacity(
    api, app, company, dashboard_clock
):
    """Combined aggregates agree with the individual reports above the signed 64-bit range."""
    computers = []
    for index in range(10):
        created = await api.post(
            "/v1/computers",
            json={
                "company_id": company["id"],
                "os": "linux",
                "architecture": "arm64",
                "runtime": "docker",
                "cpu_millis": 4000,
                "memory_bytes": 2**60 - index,
                "disk_bytes": 4096,
            },
        )
        assert created.status_code == 201, created.text
        computers.append(created.json()["id"])
    async with app.state.db.write() as session:
        for index, computer_id in enumerate(computers[:-1]):
            computer = await session.get(Computer, computer_id)
            computer.online = True
            computer.session_id = computer_id
            computer.lease_expires_at = dashboard_clock.now + timedelta(minutes=30)
            computer.target_cpu = 1000
            computer.resource(CPU_RESOURCE_ID).occupied = 1500 if index == 0 else 250

    base = f"/v1/companies/{company['id']}/resources"
    combined = await api.get(base + "/pool-summary")
    assert combined.status_code == 200, combined.text
    pools = combined.json()
    for name, only_available, count in [("inventory", False, 10), ("available", True, 9)]:
        individual = await api.get(base + "/summary", params={"only_available": only_available})
        assert individual.status_code == 200, individual.text
        assert pools[name] == individual.json()
        assert pools[name]["computer_count"] == count
        memory = next(row for row in pools[name]["resources"] if row["code"] == "memory")
        assert memory["total"] == sum(2**60 - index for index in range(count)) > 2**63
    available_cpu = next(row for row in pools["available"]["resources"] if row["code"] == "cpu")
    assert available_cpu["free"] == 8 * 750


async def test_both_pool_query_strategies_agree_with_custom_resources_and_lease_expiry(
    api, app, company, company_agent, dashboard_clock
):
    """Exercise SQL and Python aggregation and both range shapes on each database in CI."""
    computer = company_agent["computer"]["id"]
    dashboard_clock.now += timedelta(seconds=1)
    resized = await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": 3000})
    assert resized.status_code == 200, resized.text
    async with app.state.db.write() as session:
        accelerator = ResourceType(name="Accelerator", code="accelerator", unit="device")
        session.add(accelerator)
        await session.flush()
        session.add(
            ComputerResource(
                computer_id=computer, resource_type_id=accelerator.id, total=10, occupied=3
            )
        )

    async with app.state.db.read(snapshot=True) as session:
        for elapsed in (timedelta(seconds=1), timedelta(hours=2)):
            now = dashboard_clock.now + elapsed
            results = []
            for database in ("sqlite", "postgresql"):
                # Use the real connection for SQL execution and vary only the strategy selector.
                strategy = SimpleNamespace(
                    bind=SimpleNamespace(dialect=SimpleNamespace(name=database)),
                    execute=session.execute,
                    scalar=session.scalar,
                    scalars=session.scalars,
                    get=session.get,
                )
                summary = await resource_history.pool_summary(strategy, (None, company["id"]), now)
                history = await resource_history.pool_history(strategy, company["id"], now)
                results.append((summary, history))
            assert results[0] == results[1]
            summary, history = results[0]
            custom = next(
                row for row in summary["inventory"]["resources"] if row["code"] == "accelerator"
            )
            assert custom["total"] == 10 and custom["free"] == 7
            assert history["points"][-1]["cpu_total_millis"] == 3000


async def test_org_history_keeps_a_multi_resource_update_in_one_point(
    api, app, company, company_agent, dashboard_clock, monkeypatch
):
    """Update CPU and memory within the same visible step even while the clock moves."""
    dashboard_clock.now += timedelta(seconds=1)
    start = dashboard_clock.now

    def tick():
        dashboard_clock.now += timedelta(milliseconds=25)
        return dashboard_clock.now

    with monkeypatch.context() as patch:
        patch.setattr(app.state, "clock", tick)
        computer = company_agent["computer"]["id"]
        response = await api.patch(
            f"/v1/computers/{computer}", json={"cpu_millis": 4000, "memory_bytes": 4096}
        )
        assert response.status_code == 200, response.text
    dashboard_clock.now += timedelta(seconds=1)
    path = f"/v1/companies/{company['id']}/resources/pool-history"
    points = (await api.get(path)).json()["points"]
    changes = [point for point in points[:-1] if datetime.fromisoformat(point["at"]) > start]
    assert len(changes) == 1
    point = changes[0]
    assert point["cpu_total_millis"] == point["cpu_free_millis"] == 4000
    assert point["memory_total_bytes"] == point["memory_free_bytes"] == 4096


@pytest.mark.parametrize("action", ["delete", "transfer", "unassign"])
async def test_org_history_keeps_an_ownership_change_in_one_point(
    api, app, user, company, company_agent, dashboard_clock, monkeypatch, action
):
    """Disconnection and inventory changes commit as one step when releasing a computer."""
    dashboard_clock.now += timedelta(seconds=3)

    def tick():
        dashboard_clock.now += timedelta(milliseconds=25)
        return dashboard_clock.now

    worker = company_agent["computer"]
    ownership = f"/v1/computer-ownerships/{worker['ownership']['id']}"
    with monkeypatch.context() as patch:
        patch.setattr(app.state, "clock", tick)
        if action == "delete":
            assert (await api.delete(f"/v1/computers/{worker['id']}")).status_code == 204
        elif action == "transfer":
            assert (await api.patch(ownership, json={"user_id": user["id"]})).status_code == 200
        else:
            assert (await api.delete(ownership)).status_code == 204
    dashboard_clock.now += timedelta(seconds=1)
    path = f"/v1/companies/{company['id']}/resources/pool-history"
    response = await api.get(path)
    assert response.status_code == 200, response.text
    points = response.json()["points"]
    assert {(row["cpu_total_millis"], row["cpu_free_millis"]) for row in points} == {
        (0, 0),
        (2000, 2000),
    }


async def test_org_history_bounds_a_busy_week_and_preserves_extremes(
    api, app, company, company_agent, dashboard_clock
):
    """Many reservation steps retain CPU and memory lows plus the latest point."""
    start = dashboard_clock.now
    count = 1500
    end = start + timedelta(seconds=count)
    computer_id = company_agent["computer"]["id"]
    async with app.state.db.write() as session:
        (await session.get(AvailabilityBaseline, 1)).available_from = start
        computer = await session.get(Computer, computer_id)
        computer.lease_expires_at = end + timedelta(minutes=1)
        await session.execute(
            update(CapacityInterval)
            .where(CapacityInterval.computer_id == computer_id)
            .values(closed=True, ends_at=start)
        )
        await session.execute(
            insert(CapacityInterval),
            [
                dict(
                    computer_id=computer_id,
                    company_id=company["id"],
                    closed=True,
                    starts_at=start + timedelta(seconds=index),
                    ends_at=start + timedelta(seconds=index + 1),
                    created_at=start + timedelta(seconds=index),
                    cpu_millis=2000,
                    memory_bytes=2048,
                    cpu_occupied_millis=1990 if index == 17 else (index % 2) * 1000,
                    memory_occupied_bytes=1900 if index == 51 else (index % 2) * 1024,
                )
                for index in range(count)
            ],
        )
    dashboard_clock.now = end
    response = await api.get(f"/v1/companies/{company['id']}/resources/pool-history")
    assert response.status_code == 200, response.text
    history = response.json()
    points = history["points"]
    assert history["sampled"] is True
    assert len(points) <= 840
    assert min(row["cpu_free_millis"] for row in points if row["cpu_total_millis"]) == 10
    assert min(row["memory_free_bytes"] for row in points if row["memory_total_bytes"]) == 148
    assert points[-1]["at"] == history["to_time"] and points[-1]["cpu_free_millis"] == 2000
    for field, low, index in [("cpu_free_millis", 10, 17), ("memory_free_bytes", 148, 51)]:
        dip = next(i for i, row in enumerate(points) if row[field] == low)
        recovery = next(row for row in points[dip + 1 :] if row[field] != low)
        assert datetime.fromisoformat(recovery["at"]) == start + timedelta(seconds=index + 1)


@pytest.mark.parametrize("operation", ["resize", "unbind"])
async def test_org_history_aligns_capacity_with_inventory_after_clock_changes(
    api, company, company_agent, dashboard_clock, operation
):
    """Changes after the clock moves back cannot appear before their ledger event."""
    computer = company_agent["computer"]["id"]
    start = dashboard_clock.now
    dashboard_clock.now = start + timedelta(seconds=5)
    other = await api.post(
        "/v1/computers",
        json={
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1000,
            "memory_bytes": 2048,
            "disk_bytes": 8192,
        },
    )
    assert other.status_code == 201
    dashboard_clock.now = start + timedelta(seconds=3)
    if operation == "resize":
        update = await api.patch(f"/v1/computers/{computer}", json={"cpu_millis": 4000})
        expected = 4000
    else:
        update = await api.post(
            f"/v1/computers/{computer}/unbind",
            headers={"Idempotency-Key": "unbind after clock correction"},
            json={"cleanup_confirmed": True},
        )
        expected = 0
    assert update.status_code == 200, update.text
    dashboard_clock.now = start + timedelta(seconds=6)
    history = (await api.get(f"/v1/companies/{company['id']}/resources/pool-history")).json()
    points = history["points"]
    assert not any(
        point["at"] == (start + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
        for point in points
    )
    assert points[-2]["at"] == (start + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    assert points[-2]["cpu_total_millis"] == points[-2]["cpu_free_millis"] == expected
