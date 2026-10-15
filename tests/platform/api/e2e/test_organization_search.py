"""Real HTTP search over the authorized, globally ordered organization directory."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.anyio
PATH = "/v1/organization-directory"


async def space(api, name, computers=0):
    response = await api.post("/v1/companies", json={"name": name})
    assert response.status_code == 201, response.text
    company = response.json()
    for _ in range(computers):
        node = await api.post(
            "/v1/computers",
            json={
                "name": "Node",
                "company_id": company["id"],
                "os": "linux",
                "architecture": "amd64",
                "runtime": "oci",
                "cpu_millis": 1000,
                "memory_bytes": 1024,
                "disk_bytes": 1024,
            },
        )
        assert node.status_code == 201, node.text
    return company


async def invite(owner, recipient, company, **values):
    response = await owner.post(
        "/v1/invitations",
        json={"company_id": company["id"], "identifier": recipient["email"], **values},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def join(owner, recipient, company, **values):
    response = await owner.post(
        "/v1/memberships",
        json={"company_id": company["id"], "user_id": recipient["id"], **values},
    )
    assert response.status_code == 201, response.text
    return response.json()


def names(data):
    return [
        item["company"]["name"]
        if item["kind"] == "company"
        else item["invitation"]["company_name"]
        for item in data["items"]
    ]


async def test_global_order_is_applied_before_pagination_in_every_group(
    api, other_api, user
):
    groups = [[], [], []]
    for count in [2, 0, 10, 1]:
        pending = await space(other_api, f"Lab pending {count}", count)
        await invite(other_api, user, pending)
        groups[0].append(pending)
        groups[1].append(await space(api, f"Lab owned {count}", count))
        member = await space(other_api, f"Lab member {count}", count)
        await join(
            other_api,
            user,
            member,
            role="creator" if count == 10 else "admin" if count == 2 else "user",
        )
        groups[2].append(member)
    expected = [
        row["name"]
        for group in groups
        for row in sorted(
            group, key=lambda row: int(row["name"].split()[-1]), reverse=True
        )
    ]
    # A zero-computer invitation precedes even a ten-computer authored company.
    actual, priorities, counts = [], [], []
    for offset in range(0, 15, 3):
        response = await api.get(
            PATH, params={"q": " lAB ", "offset": offset, "limit": 3}
        )
        assert response.status_code == 200, response.text
        assert response.headers["X-Total-Count"] == "12"
        data = response.json()
        assert data["total"] == data["total_all"] == 12
        actual += names(data)
        priorities += [row["group"] for row in data["items"]]
        counts += [
            (row.get("company") or row.get("invitation"))["computer_count"]
            for row in data["items"]
        ]
    assert actual == expected
    assert priorities == [0] * 4 + [1] * 4 + [2] * 4
    assert counts == [10, 2, 1, 0] * 3


@pytest.mark.parametrize(
    "query,expected",
    [
        (" лАбОрАт ", ["КВАНТОВАЯ Лаборатория"]),
        ("lAb", ["Research LAB"]),
        ("E\u0301", ["Équipe"]),
        ("éQ", ["Équipe"]),
        ("STRASSE", ["Straße"]),
        ("οσ", ["ΟΣ"]),
        ("%", ["100% ready"]),
        ("_", ["under_score"]),
        ("/", ["slash/name"]),
        ("\\", ["back\\slash"]),
        (".*", ["literal .* name"]),
        ("' OR 1=1 --", []),
        ("unknown", []),
    ],
)
async def test_unicode_substrings_and_sql_wildcards_are_literal(api, query, expected):
    for name in [
        "КВАНТОВАЯ Лаборатория",
        "Research LAB",
        "Équipe",
        "100% ready",
        "under_score",
        "slash/name",
        "back\\slash",
        "literal .* name",
        "Straße",
        "ΟΣ",
    ]:
        await space(api, name)
    response = await api.get(PATH, params={"q": query})
    assert response.status_code == 200, response.text
    data = response.json()
    assert names(data) == expected
    assert data["total"] == len(expected)
    assert data["total_all"] == 10


@pytest.mark.parametrize("group", ["owned", "invited", "member"])
async def test_name_ties_use_natural_normalized_names_then_company_id(
    api, other_api, user, group
):
    owner = api if group == "owned" else other_api
    created = [
        await space(owner, name)
        for name in ["Zulu", "Team 10", "Alpha", "alpha", "Team 2", "Team 02"]
    ]
    for company in created:
        if group == "invited":
            await invite(other_api, user, company)
        elif group == "member":
            await join(other_api, user, company)
    first = sorted(created[2:4], key=lambda row: row["id"])
    twos = sorted(created[4:], key=lambda row: row["id"])
    expected = first + twos + [created[1], created[0]]
    actual = []
    for offset in range(6):
        response = await api.get(PATH, params={"offset": offset, "limit": 1})
        actual.extend(
            row["company"]["id"]
            if row["kind"] == "company"
            else row["invitation"]["company_id"]
            for row in response.json()["items"]
        )
    assert actual == [row["id"] for row in expected]


async def test_search_hides_private_organizations_open_links_other_recipients_and_inactive_invites(
    api, other_api, user, other_user, clock
):
    mine = await space(api, "Lab mine")
    pending = await space(other_api, "Lab pending")
    await invite(other_api, user, pending)
    private = await space(other_api, "Lab private")
    assert (
        await other_api.post("/v1/invitations", json={"company_id": private["id"]})
    ).status_code == 201
    await invite(other_api, {"email": "someone_else@example.com"}, private)
    expired = await space(other_api, "Lab expired")
    await invite(other_api, user, expired, expires_in_seconds=3600)
    clock.now += timedelta(seconds=3601)
    declined = await space(other_api, "Lab declined")
    invitation = await invite(other_api, user, declined)
    assert (
        await api.post(f"/v1/invitations/{invitation['id']}/decline")
    ).status_code == 204
    revoked = await space(other_api, "Lab revoked")
    invitation = await invite(other_api, user, revoked)
    assert (
        await other_api.delete(f"/v1/invitations/{invitation['id']}")
    ).status_code == 204
    response = await api.get(PATH, params={"q": "lab"})
    assert names(response.json()) == [pending["name"], mine["name"]]
    assert response.json()["total"] == response.json()["total_all"] == 2
    assert (await api.get(PATH, params={"q": "private"})).json()["total"] == 0
    assert (await api.get(f"/v1/companies/{pending['id']}")).status_code == 404
    for item in response.json()["items"]:
        row = item.get("invitation") or item.get("company")
        assert not {"token", "token_hash", "name_search"} & row.keys()


async def test_membership_wins_over_pending_invitation_and_role_does_not_change_authorship_group(
    api, other_api, user
):
    company = await space(other_api, "Shared", 2)
    await invite(other_api, user, company, role="creator")
    await join(other_api, user, company, role="creator")
    row = (await api.get(PATH)).json()["items"]
    assert len(row) == 1
    assert row[0]["group"] == 2
    assert row[0]["company"]["member_count"] == 2
    assert row[0]["company"]["computer_count"] == 2


async def test_renaming_updates_search_and_pending_preview_without_stale_snapshot_names(
    api, other_api, user
):
    company = await space(other_api, "Old name")
    await invite(other_api, user, company)
    assert (
        await other_api.patch(
            f"/v1/companies/{company['id']}", json={"name": "НОВАЯ Lab 2"}
        )
    ).status_code == 200
    assert (await api.get(PATH, params={"q": "old"})).json()["total"] == 0
    data = (await api.get(PATH, params={"q": "новая"})).json()
    assert names(data) == ["НОВАЯ Lab 2"]
    assert data["items"][0]["invitation"]["company_name"] == "НОВАЯ Lab 2"


async def test_blank_queries_empty_directory_and_offsets_beyond_the_end(api):
    empty = (await api.get(PATH)).json()
    assert empty == {"items": [], "total": 0, "total_all": 0}
    await space(api, "Only space")
    assert names((await api.get(PATH, params={"q": " \t "})).json()) == ["Only space"]
    data = (await api.get(PATH, params={"offset": 2**63 - 1})).json()
    assert data == {"items": [], "total": 1, "total_all": 1}


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": 2**63}, {"q": "x" * 201}],
)
async def test_search_parameters_are_bounded(api, params):
    assert (await api.get(PATH, params=params)).status_code == 422


async def test_directory_requires_personal_read_access(client, api, company_token):
    assert (await client.get(PATH)).status_code == 401
    assert (
        await client.get(
            PATH, headers={"Authorization": "Bearer " + company_token["token"]}
        )
    ).status_code == 403


@pytest.mark.parametrize("access_token", [{"scopes": ["manage"]}], indirect=True)
async def test_manage_without_read_cannot_search_the_directory(client, access_token):
    assert (
        await client.get(
            PATH, headers={"Authorization": "Bearer " + access_token["token"]}
        )
    ).status_code == 403


async def test_counts_and_group_remain_visible_without_resource_or_member_read_permission(
    api, other_api, user
):
    company = await space(other_api, "Limited member", 2)
    await join(other_api, user, company, permissions=[])
    data = (await api.get(PATH, params={"q": "limited"})).json()
    assert data["total"] == 1
    row = data["items"][0]["company"]
    assert row["my_permissions"] == []
    assert (row["computer_count"], row["member_count"]) == (2, 2)
    assert (
        await api.get(f"/v1/companies/{company['id']}/resources/summary")
    ).status_code == 403
