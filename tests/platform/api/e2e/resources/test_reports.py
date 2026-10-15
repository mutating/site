"""Current totals and ownership-preserving historical series over real HTTP."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio


async def test_transfer_skips_archived_capacity_and_restoration_belongs_to_new_owner(
    api, user, company, computer, gpu_resource, resource_type, history_range, clock
):
    """An archived resource stays absent during ownership transfer; restoring it adds capacity only to the new owner's graph."""
    path = f"/v1/computer-resources/{gpu_resource['id']}"
    assert (await api.delete(path)).status_code == 204
    clock.now += timedelta(seconds=1)
    assert (
        await api.patch(
            f"/v1/computer-ownerships/{computer['ownership']['id']}",
            json={"company_id": company["id"]},
        )
    ).status_code == 200
    current = (await api.get(f"/v1/companies/{company['id']}/resources/summary")).json()
    assert {row["code"] for row in current["resources"]} == {"cpu", "memory"}
    assert (await api.get(path + "/history", params=history_range)).json()["points"] == []
    clock.now += timedelta(seconds=1)
    response = await api.post(
        f"/v1/computers/{computer['id']}/resources",
        json={"resource_type_id": resource_type["id"], "total": 8},
    )
    assert response.status_code == 201 and response.json()["id"] == gpu_resource["id"]
    assert response.json()["version"] == 3
    history = (await api.get(path + "/history", params=history_range)).json()
    assert len(history["points"]) == 1 and history["points"][0]["resources"][0]["total"] == 8
    previous = (
        await api.get(
            f"/v1/users/{user['id']}/resources/history",
            params={**history_range, "resource_type_id": resource_type["id"]},
        )
    ).json()
    assert [point["resources"][0]["total"] for point in previous["points"]] == [4, 0]


async def test_current_totals_keep_personal_and_company_pools_separate(
    api, user, computer, gpu_resource, company_computer, company
):
    """Organization totals exclude members' personal computers and expose exact occupied/free counts per resource type."""
    personal = await api.get(f"/v1/users/{user['id']}/resources/summary")
    assert personal.status_code == 200
    amounts = {row["code"]: row for row in personal.json()["resources"]}
    assert {
        code: (row["total"], row["occupied"], row["free"]) for code, row in amounts.items()
    } == {"cpu": (2000, 0, 2000), "memory": (2048, 0, 2048), "gpu": (4, 0, 4)}
    corporate = await api.get(f"/v1/companies/{company['id']}/resources/summary")
    assert corporate.status_code == 200
    assert {row["code"]: row["total"] for row in corporate.json()["resources"]} == {
        "cpu": 3000,
        "memory": 4096,
    }
    response = await api.post(
        "/v1/computers",
        json={
            "os": "linux",
            "architecture": "amd64",
            "runtime": "oci",
            "cpu_millis": 1500,
            "memory_bytes": 1024,
            "disk_bytes": 4096,
        },
    )
    assert response.status_code == 201
    amounts = (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()["resources"]
    assert {row["code"]: row["free"] for row in amounts} == {"cpu": 3500, "memory": 3072, "gpu": 4}


async def test_available_filter_requires_enabled_online_worker_with_live_lease(
    api, user, computer, agent_api, clock, settings
):
    """Inventory retains offline capacity, while available-only totals track worker enablement and lease validity."""
    path = f"/v1/users/{user['id']}/resources/summary"
    assert len((await api.get(path, params={"only_available": True})).json()["resources"]) == 2
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": False})
    ).status_code == 200
    assert (await api.get(path, params={"only_available": True})).json()["resources"] == []
    assert len((await api.get(path)).json()["resources"]) == 2
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"enabled": True})
    ).status_code == 200
    clock.now += timedelta(seconds=settings.agent_lease_seconds)
    assert (await api.get(path, params={"only_available": True})).json()["resources"] == []
    assert len((await api.get(path)).json()["resources"]) == 2


async def test_empty_owner_and_unknown_type_history_return_empty_series(api, user, history_range):
    """Owners without computers receive empty summaries and a valid empty time series instead of fabricated capacity."""
    from uuid import uuid4

    assert (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()["resources"] == []
    response = await api.get(
        f"/v1/users/{user['id']}/resources/history",
        params={**history_range, "resource_type_id": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["initial"] == response.json()["points"] == []
    assert response.json()["next_after_revision"] is None


async def test_history_has_initial_state_exclusive_end_and_transaction_points(
    api, user, computer, computer_resources, clock
):
    """A [from,to) graph starts with the actual baseline, groups CPU/memory changes atomically and excludes changes at to."""
    start = clock.now
    clock.now += timedelta(seconds=10)
    assert (
        await api.patch(
            f"/v1/computers/{computer['id']}", json={"cpu_millis": 3000, "memory_bytes": 4096}
        )
    ).status_code == 200
    clock.now += timedelta(seconds=10)
    assert (
        await api.patch(f"/v1/computers/{computer['id']}", json={"cpu_millis": 5000})
    ).status_code == 200
    path = f"/v1/users/{user['id']}/resources/history"
    params = {"from": start.isoformat(), "to": clock.now.isoformat()}
    response = await api.get(path, params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert {row["code"]: row["total"] for row in data["initial"]} == {"cpu": 2000, "memory": 2048}
    assert len(data["points"]) == 1
    assert {row["code"]: row["total"] for row in data["points"][0]["resources"]} == {
        "cpu": 3000,
        "memory": 4096,
    }
    filtered = (
        await api.get(
            path,
            params={**params, "resource_type_id": computer_resources["cpu"]["resource_type_id"]},
        )
    ).json()
    assert len(filtered["initial"]) == len(filtered["points"][0]["resources"]) == 1
    direct = await api.get(
        f"/v1/computer-resources/{computer_resources['cpu']['id']}/history", params=params
    )
    assert direct.status_code == 200 and direct.json()["points"] == filtered["points"]


async def test_history_preserves_same_time_events_and_frozen_pagination(
    api, user, computer, computer_resources, history_range
):
    """Revision pagination preserves changes sharing a timestamp and excludes writes committed after the first page's snapshot."""
    path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    for value in [3000, 4000, 5000]:
        assert (await api.patch(path, json={"total": value})).status_code == 200
    history_path = f"/v1/users/{user['id']}/resources/history"
    first = (await api.get(history_path, params={**history_range, "limit": 1})).json()
    assert len(first["points"]) == 1 and first["next_after_revision"] is not None
    assert {row["code"]: row["total"] for row in first["points"][0]["resources"]} == {
        "cpu": 2000,
        "memory": 2048,
    }
    assert (await api.patch(path, json={"total": 6000})).status_code == 200
    pages = [first]
    while pages[-1]["next_after_revision"] is not None:
        response = await api.get(
            history_path,
            params={
                **history_range,
                "limit": 1,
                "snapshot_revision": first["snapshot_revision"],
                "after_revision": pages[-1]["next_after_revision"],
            },
        )
        assert response.status_code == 200
        page = response.json()
        assert page["initial"] == pages[-1]["points"][-1]["resources"]
        pages.append(page)
        assert len(pages) <= 4
    assert len(pages) == 4
    points = [page["points"][0] for page in pages]
    assert len({point["at"] for point in points}) == 1
    assert [
        next(row["total"] for row in point["resources"] if row["code"] == "cpu") for point in points
    ] == [2000, 3000, 4000, 5000]
    assert len({point["revision"] for point in points}) == 4
    latest = (await api.get(history_path, params=history_range)).json()
    assert len(latest["points"]) == 5


async def test_noop_and_rejected_mutations_do_not_pollute_history(
    api, user, computer, computer_resources, history_range
):
    """Unchanged capacities, failed version checks and failed multi-field updates leave both the ledger and inventory unchanged."""
    path = f"/v1/users/{user['id']}/resources/history"
    before = (await api.get(path, params=history_range)).json()
    resource_path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    assert (await api.patch(resource_path, json={"total": 2000})).status_code == 200
    assert (
        await api.patch(resource_path, json={"total": 3000, "expected_version": 99})
    ).status_code == 409
    assert (
        await api.patch(
            f"/v1/computers/{computer['id']}", json={"cpu_millis": 3000, "memory_bytes": None}
        )
    ).status_code == 422
    after = (await api.get(path, params=history_range)).json()
    assert before["points"] == after["points"]
    assert (await api.get(resource_path)).json() == computer_resources["cpu"]


async def test_deleted_resources_and_computers_keep_historical_contributions(
    api, user, computer, gpu_resource, resource_type, admin_api, history_range, clock
):
    """Resource deletion and computer deletion add zero contributions while retaining old graph data and archived type labels."""
    clock.now += timedelta(seconds=1)
    resource_path = f"/v1/computer-resources/{gpu_resource['id']}"
    assert (await api.delete(resource_path)).status_code == 204
    assert (await admin_api.delete(f"/v1/resource-types/{resource_type['id']}")).status_code == 204
    response = await api.get(resource_path + "/history", params=history_range)
    assert response.status_code == 200
    assert [point["resources"][0]["total"] for point in response.json()["points"]] == [4, 0]
    assert response.json()["points"][1]["resources"][0]["code"] == "gpu"
    clock.now += timedelta(seconds=1)
    assert (await api.delete(f"/v1/computers/{computer['id']}")).status_code == 204
    assert (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()["resources"] == []
    data = (await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)).json()
    assert len(data["points"]) == 4
    assert all(
        row["total"] == row["occupied"] == row["free"] == 0
        for row in data["points"][-1]["resources"]
    )
    assert any(row["total"] == 2000 for row in data["points"][0]["resources"])


async def test_transfer_changes_owner_totals_without_rewriting_or_disclosing_prior_history(
    api, other_api, user, computer, computer_resources, company, membership, clock, history_range
):
    """A new owner sees capacity only from transfer onward; the previous owner's chart retains its own past without leaking it to company members."""
    cpu_path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    clock.now += timedelta(seconds=1)
    assert (await api.patch(cpu_path, json={"total": 4500})).status_code == 200
    clock.now += timedelta(seconds=1)
    assert (
        await api.patch(
            f"/v1/computer-ownerships/{computer['ownership']['id']}",
            json={"company_id": company["id"]},
        )
    ).status_code == 200
    personal = (
        await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)
    ).json()
    corporate = (
        await other_api.get(
            f"/v1/companies/{company['id']}/resources/history", params=history_range
        )
    ).json()
    assert len(personal["points"]) == 3
    assert [
        next(row["total"] for row in point["resources"] if row["code"] == "cpu")
        for point in personal["points"]
    ] == [2000, 4500, 0]
    assert len(corporate["points"]) == 1
    assert {row["code"]: row["total"] for row in corporate["points"][0]["resources"]} == {
        "cpu": 4500,
        "memory": 2048,
    }
    direct = (await other_api.get(cpu_path + "/history", params=history_range)).json()
    assert len(direct["points"]) == 1 and direct["points"][0]["resources"][0]["total"] == 4500
    assert user["id"] not in str(corporate) and computer["id"] not in str(corporate)
    assert (
        await other_api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)
    ).status_code == 404
    assert (await api.get(f"/v1/users/{user['id']}/resources/summary")).json()["resources"] == []


async def test_detach_and_reattach_ownership_record_gaps_in_inventory(
    api, user, computer, computer_resources, clock, history_range
):
    """An ownerless computer contributes zero; reattachment restores current capacity and records changes made during the gap only on entry."""
    clock.now += timedelta(seconds=1)
    assert (
        await api.delete(f"/v1/computer-ownerships/{computer['ownership']['id']}")
    ).status_code == 204
    resource_path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    assert (await api.get(resource_path + "/history", params=history_range)).status_code == 404
    clock.now += timedelta(seconds=1)
    assert (await api.patch(resource_path, json={"total": 3000})).status_code == 200
    clock.now += timedelta(seconds=1)
    assert (
        await api.post(
            "/v1/computer-ownerships",
            json={"computer_id": computer["id"], "user_id": user["id"]},
        )
    ).status_code == 201
    data = (await api.get(f"/v1/users/{user['id']}/resources/history", params=history_range)).json()
    assert [
        next(row["total"] for row in point["resources"] if row["code"] == "cpu")
        for point in data["points"]
    ] == [2000, 0, 3000]


@pytest.mark.parametrize(
    "parameters",
    [
        {"from": "2026-10-08T12:00:00"},
        {"to": "2026-10-08T12:00:00"},
        {"from": "2026-10-09T12:00:00Z"},
        {"limit": 0},
        {"limit": 2001},
        {"after_revision": -1},
        {"after_revision": 1},
        {"snapshot_revision": 2**60},
        {"after_revision": 10, "snapshot_revision": 1},
    ],
)
async def test_history_rejects_ambiguous_intervals_and_invalid_cursors(
    api, user, history_range, parameters
):
    """History requires ordered timezone-aware bounds and a valid fixed snapshot when continuing pagination."""
    response = await api.get(
        f"/v1/users/{user['id']}/resources/history", params={**history_range, **parameters}
    )
    assert response.status_code == 422


async def test_history_normalizes_offsets_and_never_moves_backwards(
    api, user, computer_resources, clock, history_range
):
    """Offset timestamps identify the same interval, and a backwards application clock cannot reorder committed graph events."""
    path = f"/v1/computer-resources/{computer_resources['cpu']['id']}"
    clock.now += timedelta(seconds=10)
    assert (await api.patch(path, json={"total": 3000})).status_code == 200
    clock.now -= timedelta(seconds=5)
    assert (await api.patch(path, json={"total": 4000})).status_code == 200
    history_path = f"/v1/users/{user['id']}/resources/history"
    utc = (await api.get(history_path, params=history_range)).json()
    offset = (
        await api.get(
            history_path,
            params={"from": "2026-10-08T14:00:00+03:00", "to": "2026-10-08T16:00:00+03:00"},
        )
    ).json()
    assert utc["points"] == offset["points"]
    assert [point["at"] for point in utc["points"]] == sorted(
        point["at"] for point in utc["points"]
    )
    assert utc["points"][-1]["at"] == utc["points"][-2]["at"]


@pytest.mark.parametrize(
    "bounds",
    [
        {"from": "0001-01-01T00:00:00+03:00", "to": "2026-01-01T00:00:00Z"},
        {"from": "2026-01-01T00:00:00Z", "to": "9999-12-31T23:59:59-03:00"},
    ],
)
async def test_history_rejects_timezone_conversion_outside_datetime_range(
    api, history_report_paths, bounds
):
    """Extreme local-time sentinels that overflow when converted to UTC return 422 in every history scope instead of a server error."""
    for path in history_report_paths:
        response = await api.get(path, params=bounds)
        assert response.status_code == 422, (path, response.text)
        assert response.json() == {
            "detail": "from and to must remain within the datetime range in UTC"
        }


async def test_history_accepts_representable_utc_extremes(api, history_report_paths):
    """All-time ranges at the actual UTC datetime boundaries stay valid and include existing inventory history."""
    for path in history_report_paths:
        response = await api.get(
            path, params={"from": "0001-01-01T00:00:00Z", "to": "9999-12-31T23:59:59Z"}
        )
        assert response.status_code == 200, (path, response.text)
        assert response.json()["from_time"] == "0001-01-01T00:00:00Z"
        assert response.json()["to_time"] == "9999-12-31T23:59:59Z"
