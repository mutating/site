"""Dashboard reports preserve availability, reservations and historical ownership."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from throng_api.models import AvailabilityBaseline, CapacityInterval

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
        assert len(response.json()) == 1
        response = await api.get(
            "/v1/computers", params={**owner, "only_available": True, "offset": 1}
        )
        assert response.json() == [] and response.headers["x-total-count"] == "1"
    assert (await api.get("/v1/computers", params={"user_id": other_user["id"]})).status_code == 404
    assert (
        await api.get("/v1/computers", params={"user_id": user["id"], "company_id": company["id"]})
    ).status_code == 422


async def test_empty_history_validation_and_owner_isolation(
    api, user, company, other_api, dashboard_clock
):
    """Bound query windows, exclude future history and authorize each owner independently."""
    clock = dashboard_clock
    path = f"/v1/users/{user['id']}/resources/availability-history"
    period = {"from": (clock.now - timedelta(days=7)).isoformat(), "to": clock.now.isoformat()}
    report = (await api.get(path, params=period)).json()
    assert all(point["cpu_millis"] == point["memory_bytes"] == 0 for point in report["points"])
    for bounds in [
        {"from": clock.now.isoformat(), "to": clock.now.isoformat()},
        {"from": "2026-01-01", "to": clock.now.isoformat()},
        {"from": (clock.now - timedelta(days=32)).isoformat(), "to": clock.now.isoformat()},
        {"from": "0001-01-01T00:00:00+12:00", "to": "0001-01-02T00:00:00Z"},
    ]:
        assert (await api.get(path, params=bounds)).status_code == 422
    assert (await other_api.get(path, params=period)).status_code == 404
    company_path = f"/v1/companies/{company['id']}/resources/availability-history"
    assert (await api.get(company_path, params=period)).status_code == 200
    assert (await other_api.get(company_path, params=period)).status_code == 404
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
