"""Real HTTP membership grants, private member profiles and invitation lifecycle."""

import asyncio
import io
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from PIL import Image

from throng_api import invitations
from throng_api.models import CompanyInvitation, Membership, User

pytestmark = pytest.mark.anyio


async def invite(api, company, **extra):
    """Create a share link through the real endpoint and require a successful grant."""
    response = await api.post("/v1/invitations", json={"company_id": company["id"], **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def change(api, member, **extra):
    """Set membership rights through the creator's API client."""
    response = await api.patch(f"/v1/memberships/{member['id']}", json=extra)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("identifier", ["BOB", " BOB@EXAMPLE.COM "])
async def test_add_member_by_identity_and_private_profile_lookup(
    api, other_api, company, other_user, identifier
):
    """Resolve exact email/nickname only for managers and omit private profile attributes."""
    path = f"/v1/companies/{company['id']}/users/lookup"
    assert (await other_api.get(path, params={"identifier": identifier})).status_code == 404
    found = await api.get(path, params={"identifier": identifier})
    assert found.status_code == 200
    assert found.json() == {
        key: other_user[key]
        for key in ["id", "first_name", "last_name", "nickname", "avatar_version"]
    }
    created = await api.post(
        "/v1/memberships", json={"company_id": company["id"], "identifier": identifier}
    )
    assert created.status_code == 201
    member = created.json()
    assert member["user"] == found.json() and member["permissions"] is None
    assert "execution.run" in member["effective_permissions"]
    assert (await other_api.get(path, params={"identifier": "alice"})).status_code == 403
    assert (await api.get(path, params={"identifier": "missing"})).status_code == 404
    assert (await api.get(f"/v1/users/{other_user['id']}")).status_code == 404
    duplicate = await api.post(
        "/v1/memberships", json={"company_id": company["id"], "identifier": identifier}
    )
    assert duplicate.status_code == 409


async def test_member_profiles_pagination_avatar_and_access_boundaries(
    api, other_api, company, membership, client, other_user
):
    """List real members with counted pagination and serve avatars only within the organization."""
    response = await other_api.get(
        "/v1/memberships", params={"company_id": company["id"], "limit": 1}
    )
    assert response.status_code == 200 and response.headers["x-total-count"] == "2"
    first = response.json()[0]
    second = (
        await api.get(
            "/v1/memberships",
            params={"company_id": company["id"], "limit": 1, "offset": 1},
        )
    ).json()[0]
    assert first["id"] != second["id"] and "email" not in first["user"]
    assert (
        await api.get("/v1/memberships", params={"company_id": company["id"], "offset": 2})
    ).json() == []
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    assert (await other_api.put("/v1/users/me/avatar", content=image.getvalue())).status_code == 200
    avatar = await api.get(f"/v1/memberships/{membership['id']}/avatar")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/webp"
    creator = next(row for row in [first, second] if row["role"] == "creator")
    assert (await api.get(f"/v1/memberships/{creator['id']}/avatar")).status_code == 404
    assert (await api.get(f"/v1/memberships/{uuid4()}/avatar")).status_code == 404
    unrelated = (await api.post("/v1/companies", json={"name": "Private"})).json()
    assert (
        await other_api.get("/v1/memberships", params={"company_id": unrelated["id"]})
    ).status_code == 404


async def test_member_search_filters_before_pagination_and_keeps_the_company_boundary(
    api, other_api, app, company, membership, other_user
):
    """Match a large roster inside the authorized company and count filtered and unfiltered rows."""
    path = "/v1/memberships"
    scope = {"company_id": company["id"]}
    profile = await other_api.patch(
        f"/v1/users/{other_user['id']}",
        json={
            "first_name": "СЕ\u0308МЁН",
            "last_name": "Straße_100%",
            "nickname": "Member_0000",
        },
    )
    assert profile.status_code == 200
    async with app.state.db.write() as session:
        people = [
            User(
                id=str(uuid4()),
                first_name=f"Участник {index:04d}",
                last_name="Команды",
                nickname=f"member_{index:04d}",
                email=f"member_{index:04d}@example.com",
                password_hash="unused",
            )
            for index in range(1, 1999)
        ]
        session.add_all(people)
        await session.flush()
        session.add_all(
            Membership(
                id=str(UUID(int=index + 1)),
                company_id=company["id"],
                user_id=user.id,
                role="user",
            )
            for index, user in enumerate(people)
        )

    preview = await api.get(path, params={**scope, "limit": 5})
    assert len(preview.json()) == 5 and preview.headers["x-total-count"] == "2000"
    assert all(row["id"] != membership["id"] for row in preview.json())
    last_page = await api.get(path, params={**scope, "limit": 20, "offset": 1980})
    assert len(last_page.json()) == 20 and last_page.headers["x-total-count"] == "2000"
    assert membership["id"] in {row["id"] for row in last_page.json()}
    for query in ("  сёмён  STRASSE_100% ", "@MEMBER_0000", "100%", "member_0000"):
        found = await api.get(path, params={**scope, "q": query, "limit": 1})
        assert found.status_code == 200 and found.headers["x-total-count"] == "1"
        assert found.headers["x-total-unfiltered-count"] == "2000"
        assert [row["id"] for row in found.json()] == [membership["id"]]
        assert "email" not in found.json()[0]["user"]
    assert (await api.get(path, params={**scope, "q": "@"})).json() == []
    assert (await api.get(path, params={**scope, "q": "member%"})).json() == []
    assert (await api.get(path, params={**scope, "q": "_9999"})).json() == []
    partial = await api.get(path, params={**scope, "q": "участник", "offset": 1997, "limit": 20})
    assert partial.headers["x-total-count"] == "1998" and len(partial.json()) == 1
    outside = await api.get(path, params={**scope, "q": "STRASSE", "offset": 1})
    assert outside.headers["x-total-count"] == "1" and outside.json() == []
    identity = await api.get(path, params={**scope, "q": "STRASSE", "user_id": other_user["id"]})
    assert identity.headers["x-total-unfiltered-count"] == "1"
    assert [row["id"] for row in identity.json()] == [membership["id"]]
    assert (await api.get(path, params={**scope, "q": "a\0b"})).status_code == 422

    renamed = await other_api.patch(
        f"/v1/users/{other_user['id']}",
        json={
            "first_name": "@СЕ\u0308МЁН",
            "last_name": "Петров",
            "nickname": None,
        },
    )
    assert renamed.status_code == 200
    assert (await api.get(path, params={**scope, "q": "STRASSE"})).json() == []
    assert (await api.get(path, params={**scope, "q": "@MEMBER_0000"})).json() == []
    assert (await api.get(path, params={**scope, "q": "сёмён петров"})).headers[
        "x-total-count"
    ] == "1"
    assert (await api.get(path, params={**scope, "q": "@сёмён петров"})).headers[
        "x-total-count"
    ] == "1"
    unrelated = (await api.post("/v1/companies", json={"name": "Unrelated team"})).json()
    assert (await api.get(path, params={"company_id": unrelated["id"], "q": "Петров"})).json() == []
    assert (
        await other_api.get(path, params={"company_id": unrelated["id"], "q": "Петров"})
    ).status_code == 404
    unnamed = await other_api.patch(
        f"/v1/users/{other_user['id']}", json={"first_name": "", "last_name": ""}
    )
    assert unnamed.status_code == 200
    found = await api.get(path, params={**scope, "q": other_user["id"][:8]})
    assert [row["id"] for row in found.json()] == [membership["id"]]
    await change(api, membership, permissions=[])
    assert (await other_api.get(path, params={**scope, "q": "Петров"})).status_code == 403


async def test_custom_rights_filter_collections_and_block_details_but_leave_personal_access(
    api, other_api, company, membership, company_agent
):
    """A current membership can retain its identity while losing all organization data rights."""
    await change(api, membership, permissions=[])
    overview = (await other_api.get(f"/v1/companies/{company['id']}")).json()
    assert overview["my_role"] == "user" and overview["my_permissions"] == []
    assert (await other_api.get("/v1/users/me")).status_code == 200
    for path in [
        f"/v1/memberships/{membership['id']}",
        f"/v1/computers/{company_agent['computer']['id']}",
        f"/v1/companies/{company['id']}/resources/summary",
        f"/v1/memberships/{membership['id']}/avatar",
    ]:
        assert (await other_api.get(path)).status_code == 403, path
    for path in [
        "computers",
        "computer-ownerships",
        "isolates",
        "artifacts",
        "access-tokens",
        "token-ownerships",
    ]:
        rows = (await other_api.get("/v1/" + path)).json()
        assert all(row.get("company_id") != company["id"] for row in rows), path
    assert (
        await other_api.patch(f"/v1/companies/{company['id']}", json={"name": "No"})
    ).status_code == 403
    assert (await other_api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204


async def test_delegate_specific_management_rights_and_reset_role_defaults(
    api, other_api, company, membership, company_agent
):
    """Individual grants allow their corresponding actions without permitting unrelated management."""
    granted = await change(
        api,
        membership,
        permissions=["resources.read", "resources.manage", "company.manage"],
    )
    assert granted["effective_permissions"] == [
        "company.manage",
        "resources.manage",
        "resources.read",
    ]
    assert (
        await other_api.patch(
            f"/v1/computers/{company_agent['computer']['id']}",
            json={"name": "Delegated"},
        )
    ).status_code == 200
    assert (
        await other_api.patch(f"/v1/companies/{company['id']}", json={"name": "Renamed"})
    ).status_code == 200
    assert (
        await other_api.post(
            "/v1/memberships", json={"company_id": company["id"], "identifier": "alice"}
        )
    ).status_code == 403
    defaults = await change(api, membership, permissions=None)
    assert (
        defaults["permissions"] is None
        and "resources.manage" not in defaults["effective_permissions"]
    )
    promoted = await change(api, membership, role="admin")
    assert len(promoted["effective_permissions"]) == 9
    assert (
        await other_api.post(
            "/v1/invitations", json={"company_id": company["id"], "role": "creator"}
        )
    ).status_code == 403


async def test_grant_ceiling_creator_guards_and_permission_validation(
    api, other_api, company, membership
):
    """Delegated managers cannot raise themselves or others beyond their live grants."""
    await change(api, membership, permissions=["members.manage", "members.read"])
    assert (
        await other_api.post("/v1/invitations", json={"company_id": company["id"]})
    ).status_code == 403
    allowed = await invite(other_api, company, permissions=["members.read"])
    assert allowed["effective_permissions"] == ["members.read"]
    assert (
        await other_api.patch(
            f"/v1/memberships/{membership['id']}",
            json={"permissions": ["tokens.manage"]},
        )
    ).status_code == 403
    creator = next(
        row
        for row in (await api.get("/v1/memberships", params={"company_id": company["id"]})).json()
        if row["role"] == "creator"
    )
    assert (
        await api.patch(f"/v1/memberships/{creator['id']}", json={"permissions": []})
    ).status_code == 422
    assert (
        await api.patch(f"/v1/memberships/{creator['id']}", json={"role": "user"})
    ).status_code == 409
    for payload in [{}, {"role": None}, {"permissions": ["unknown"]}]:
        assert (
            await api.patch(f"/v1/memberships/{membership['id']}", json=payload)
        ).status_code == 422
    for payload in [{"identifier": "missing"}, {"user_id": str(uuid4())}]:
        assert (
            await api.post("/v1/memberships", json={"company_id": company["id"], **payload})
        ).status_code == 404
    assert (
        await api.post("/v1/memberships", json={"company_id": company["id"]})
    ).status_code == 422


async def test_company_tokens_are_revoked_on_permission_loss(
    api, other_api, client, company, membership
):
    """Company credentials cannot keep old privileges or resurrect after a permission reset."""
    await change(api, membership, role="admin")
    owner = (
        await other_api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    ).json()
    token = (
        await other_api.post(
            "/v1/access-tokens",
            json={
                "ownership_id": owner["id"],
                "name": "Member automation",
                "scopes": ["read", "execute"],
            },
        )
    ).json()
    headers = {"Authorization": "Bearer " + token["token"]}
    assert (await client.get(f"/v1/companies/{company['id']}", headers=headers)).status_code == 200
    await change(api, membership, permissions=["members.read"])
    assert (await client.get(f"/v1/companies/{company['id']}", headers=headers)).status_code == 401
    await change(api, membership, permissions=None)
    assert (await client.get(f"/v1/companies/{company['id']}", headers=headers)).status_code == 401


async def test_execution_permissions_apply_to_artifacts_and_isolate_submission(
    api, other_api, company, membership, company_agent
):
    """Reading execution data and submitting work are independently enforced against live grants."""
    uploaded = await api.post(
        "/v1/artifacts",
        params={"company_id": company["id"], "filename": "state.tar"},
        content=b"state",
    )
    assert uploaded.status_code == 201
    artifact = uploaded.json()
    payload = {
        "company_id": company["id"],
        "state_artifact_id": artifact["id"],
        "image": "python:3.12",
        "os": "linux",
        "architecture": "amd64",
        "runtime": "oci",
        "cpu_millis": 1000,
        "memory_bytes": 1024,
        "disk_bytes": 4096,
    }
    await change(api, membership, permissions=["execution.read"])
    assert (await other_api.get(f"/v1/artifacts/{artifact['id']}")).status_code == 200
    assert (
        await other_api.post("/v1/artifacts", params={"company_id": company["id"]}, content=b"no")
    ).status_code == 403
    assert (
        await other_api.post(
            "/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=payload
        )
    ).status_code == 403
    await change(api, membership, permissions=["execution.run"])
    assert (await other_api.get(f"/v1/artifacts/{artifact['id']}")).status_code == 403
    isolate = await other_api.post(
        "/v1/isolates", headers={"Idempotency-Key": str(uuid4())}, json=payload
    )
    assert isolate.status_code == 202
    await change(api, membership, permissions=[])
    assert (await other_api.delete(f"/v1/isolates/{isolate.json()['id']}")).status_code == 403


async def test_addressed_invitations_accept_by_inbox_and_hide_tokens(
    api, other_api, company, other_user, app
):
    """Addressed recipients can join; other viewers cannot discover links or private identities."""
    row = await invite(api, company, identifier="BOB", role="admin")
    assert row["invitee_user_id"] == other_user["id"]
    inbox = (await other_api.get("/v1/invitations")).json()
    assert len(inbox) == 1 and "token" not in inbox[0] and "token_hash" not in inbox[0]
    assert (await api.get("/v1/invitations")).json() == []
    assert (await api.get(f"/v1/invitations/link/{row['token']}")).status_code == 403
    assert (await api.post(f"/v1/invitations/link/{row['token']}/accept")).status_code == 403
    assert (await other_api.delete(f"/v1/invitations/{row['id']}")).status_code == 404
    assert (
        await other_api.get("/v1/invitations", params={"company_id": company["id"]})
    ).status_code == 404
    assert (
        await api.post(
            "/v1/invitations",
            json={"company_id": company["id"], "identifier": "bob@example.com"},
        )
    ).status_code == 409
    joined = await other_api.post(f"/v1/invitations/{row['id']}/accept")
    assert joined.status_code == 200 and joined.json()["role"] == "admin"
    assert (await other_api.get("/v1/invitations")).json() == []
    assert (await other_api.post(f"/v1/invitations/{row['id']}/accept")).status_code == 410
    assert (await other_api.post(f"/v1/invitations/link/{row['token']}/accept")).status_code == 410
    async with app.state.db.read() as session:
        saved = await session.get(CompanyInvitation, row["id"])
        assert saved.token_hash != row["token"] and len(saved.token_hash) == 64


async def test_invitation_badge_and_rows_can_be_fetched_independently(
    api, other_api, company, membership
):
    """Count-only and compact invitation reads share authorization, scope and the existing order."""
    opened = [await invite(api, company) for _ in range(3)]
    revoked = await api.delete(f"/v1/invitations/{opened[1]['id']}")
    assert revoked.status_code == 204
    path = "/v1/invitations"
    scope = {"company_id": company["id"]}
    counted = await api.get(path, params={**scope, "limit": 0})
    assert counted.status_code == 200 and counted.json() == []
    assert counted.headers["x-total-count"] == "3"
    ordinary = await api.get(path, params={**scope, "limit": 2, "offset": 1})
    compact = await api.get(
        path, params={**scope, "limit": 2, "offset": 1, "include_counts": False}
    )
    assert ordinary.status_code == compact.status_code == 200
    assert ordinary.headers["x-total-count"] == "3"
    assert "x-total-count" not in compact.headers
    revision = "x-computer-pool-check-revision"
    for result in [counted, ordinary, compact]:
        assert result.headers["x-organization-membership-id"] == company["my_membership_id"]
        assert int(result.headers[revision]) > 0
    assert all(row["member_count"] == 2 for row in ordinary.json())
    for complete, reduced in zip(ordinary.json(), compact.json(), strict=True):
        assert reduced["link_check_revision"] >= complete["link_check_revision"]
        assert reduced == dict(
            complete,
            member_count=0,
            computer_count=0,
            link_check_revision=reduced["link_check_revision"],
        )
    denied = await other_api.get(path, params={**scope, "limit": 0})
    assert denied.status_code == 403
    assert denied.headers["x-organization-membership-id"] == membership["id"]
    assert int(denied.headers[revision]) >= int(counted.headers[revision])
    assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    for requested in [{**scope, "limit": 0}, {**scope, "include_counts": False}]:
        missing = await other_api.get(path, params=requested)
        assert missing.status_code == 404
        assert int(missing.headers[revision]) > int(denied.headers[revision])
        assert "x-organization-membership-id" not in missing.headers
        assert "x-organization-id" not in missing.headers


async def test_team_reads_stay_in_the_authorized_snapshot(
    api, other_api, client, company, membership, other_user, monkeypatch
):
    """An in-flight team read cannot see data created after its access was removed."""
    await change(api, membership, role="admin")
    company_id = company["id"]
    creator_id = company["my_membership_id"]
    avatar_path = f"/v1/memberships/{creator_id}/avatar"
    lookup_path = f"/v1/companies/{company_id}/users/lookup"
    email = "after-removal@example.com"
    scope = {"company_id": company_id}

    def png(color):
        image = io.BytesIO()
        Image.new("RGB", (2, 2), color).save(image, format="PNG")
        return image.getvalue()

    upload_path = "/v1/users/me/avatar"
    assert (await api.put(upload_path, content=png("red"))).status_code == 200
    previous = (await other_api.get(avatar_path)).content
    checks = asyncio.Queue()
    release = asyncio.Event()
    authorize = invitations.company_access

    async def paused(session, principal, *args, **kwargs):
        member = await authorize(session, principal, *args, **kwargs)
        if principal.user_id == other_user["id"]:
            checks.put_nowait(None)
            await release.wait()
        return member

    monkeypatch.setattr(invitations, "company_access", paused)
    reads = asyncio.gather(
        other_api.get(avatar_path),
        other_api.get("/v1/invitations", params={**scope, "limit": 0}),
        other_api.get("/v1/invitations", params={**scope, "include_counts": False}),
        other_api.get(lookup_path, params={"identifier": email}),
    )
    try:
        for _ in range(4):
            await asyncio.wait_for(checks.get(), timeout=5)
        assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
        assert (await api.put(upload_path, content=png("blue"))).status_code == 200
        await invite(api, company)
        created = await client.post(
            "/v1/users", json={"email": email, "password": "long invitation password"}
        )
        assert created.status_code == 201
    finally:
        release.set()
        avatar, badge, rows, lookup = await reads

    assert avatar.status_code == 200 and avatar.content == previous
    assert badge.status_code == 200 and badge.headers["x-total-count"] == "0"
    assert rows.status_code == 200 and rows.json() == []
    assert lookup.status_code == 404
    current = await api.get(lookup_path, params={"identifier": email})
    assert current.status_code == 200 and current.json()["id"] == created.json()["id"]
    assert (await other_api.get(avatar_path)).status_code == 404
    assert (await other_api.get("/v1/invitations", params=scope)).status_code == 404
    denied = await other_api.get(lookup_path, params={"identifier": email})
    assert denied.status_code == 404


async def test_future_email_invitation_survives_registration(api, client, company):
    """An invitation created before account registration is found by the normalized email."""
    row = await invite(api, company, identifier="FUTURE@example.com", permissions=["members.read"])
    response = await client.post(
        "/v1/users",
        json={"email": "future@example.com", "password": "long invitation password"},
    )
    assert response.status_code == 201
    login = (
        await client.post(
            "/v1/auth/login",
            json={
                "login": "future@example.com",
                "password": "long invitation password",
            },
        )
    ).json()
    headers = {"Authorization": "Bearer " + login["access_token"]}
    assert (await client.get("/v1/invitations", headers=headers)).json()[0]["id"] == row["id"]
    joined = await client.post(f"/v1/invitations/{row['id']}/accept", headers=headers)
    assert joined.status_code == 200 and joined.json()["effective_permissions"] == ["members.read"]


async def test_share_links_are_single_use_and_rotation_invalidates_previous_secret(
    api, other_api, company
):
    """The raw link is required for open invitations and concurrent acceptance is serialized."""
    row = await invite(api, company)
    assert (await other_api.get("/v1/invitations")).json() == []
    assert (await other_api.post(f"/v1/invitations/{row['id']}/accept")).status_code == 404
    rotated = await api.post(f"/v1/invitations/{row['id']}/link")
    assert rotated.status_code == 200
    replacement = rotated.json()
    manager_list = await api.get("/v1/invitations", params={"company_id": company["id"]})
    assert manager_list.status_code == 200
    listed = manager_list.json()[0]
    assert listed["link_version"] == replacement["link_version"] != row["link_version"]
    assert (
        listed["link_check_revision"]
        >= replacement["link_check_revision"]
        > row["link_check_revision"]
    )
    assert "token_hash" not in listed and "token" not in listed
    assert (await other_api.get(f"/v1/invitations/link/{row['token']}")).status_code == 404
    token = replacement["token"]
    opened = await other_api.get(f"/v1/invitations/link/{token}")
    assert opened.status_code == 200
    assert opened.json()["link_version"] is None
    assert opened.json()["link_check_revision"] is None
    results = await asyncio.gather(
        *[other_api.post(f"/v1/invitations/link/{token}/accept") for _ in range(2)]
    )
    assert sorted(result.status_code for result in results) == [200, 410]


@pytest.mark.parametrize("end", ["decline", "revoke", "expire"])
async def test_invitation_end_states_cannot_be_accepted(api, other_api, company, clock, end):
    """Expired, declined and revoked invitations lose both inbox and link access."""
    row = await invite(api, company, identifier="bob")
    if end == "expire":
        clock.now += timedelta(days=8)
        # Existing login credentials expire sooner than invitations; re-login to exercise the invitation boundary.
        login = await other_api.post(
            "/v1/auth/login",
            json={"login": "bob", "password": "bob's sufficiently long password"},
        )
        other_api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        login = await api.post(
            "/v1/auth/login",
            json={"login": "alice", "password": "  correct horse battery staple  "},
        )
        api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    elif end == "decline":
        assert (await other_api.post(f"/v1/invitations/{row['id']}/decline")).status_code == 204
    else:
        assert (await api.delete(f"/v1/invitations/{row['id']}")).status_code == 204
    assert (await other_api.get("/v1/invitations")).json() == []
    assert (await other_api.get(f"/v1/invitations/link/{row['token']}")).status_code == 410
    assert (await other_api.post(f"/v1/invitations/{row['id']}/accept")).status_code == 410
    assert (await api.post(f"/v1/invitations/{row['id']}/link")).status_code == 410
    state = (await api.get("/v1/invitations", params={"company_id": company["id"]})).json()[0][
        "status"
    ]
    assert state == {"expire": "expired", "decline": "declined", "revoke": "revoked"}[end]


async def test_invitation_revoked_when_issuer_loses_grant_rights(
    api, other_api, company, membership
):
    """Pending links cannot restore privileged access after their issuer's demotion."""
    await change(api, membership, role="admin")
    row = await invite(other_api, company)
    await change(api, membership, role="user")
    assert (await api.get(f"/v1/invitations/link/{row['token']}")).status_code == 410
    await change(api, membership, role="admin")
    assert (await api.post(f"/v1/invitations/link/{row['token']}/accept")).status_code == 410


async def test_invitation_validation_unknown_ids_existing_members_and_organization_deletion(
    api, other_api, company, other_user
):
    """Validate grants before persistence and revoke pending links when their empty company is deleted."""
    for payload, status in [
        ({"identifier": "missing_nickname"}, 404),
        ({"expires_in_seconds": 0}, 422),
        ({"role": "creator", "permissions": []}, 422),
        ({"permissions": ["unknown"]}, 422),
        ({"identifier": "alice"}, 409),
    ]:
        assert (
            await api.post("/v1/invitations", json={"company_id": company["id"], **payload})
        ).status_code == status
    for suffix in ["accept", "decline"]:
        assert (await other_api.post(f"/v1/invitations/{uuid4()}/{suffix}")).status_code == 404
    assert (await api.delete(f"/v1/invitations/{uuid4()}")).status_code == 404
    assert (await other_api.get("/v1/invitations/link/unknown")).status_code == 404
    assert (await other_api.post("/v1/invitations/link/unknown/accept")).status_code == 404
    row = await invite(api, company, identifier=other_user["email"])
    assert (await api.delete(f"/v1/companies/{company['id']}")).status_code == 204
    assert (await other_api.get(f"/v1/invitations/link/{row['token']}")).status_code == 410


async def test_creator_only_deletion_and_privileged_invitation_roles(
    api, other_api, company, membership
):
    """An administrator can invite participants but cannot grant privileged roles or delete the company."""
    await change(api, membership, role="admin")
    path = f"/v1/companies/{company['id']}"
    assert (await other_api.delete(path)).status_code == 403
    for role in ("admin", "creator"):
        assert (
            await other_api.post(
                "/v1/invitations", json={"company_id": company["id"], "role": role}
            )
        ).status_code == 403
        assert (await invite(api, company, role=role))["role"] == role
    assert (await invite(other_api, company, role="user"))["role"] == "user"
    assert (await api.delete(path)).status_code == 409
    assert (await other_api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    assert (await api.delete(path)).status_code == 204


async def test_pending_invitation_cannot_overwrite_an_existing_membership(
    api, other_api, company, other_user
):
    """Accepting a previously issued invite never upgrades a membership created by another path."""
    row = await invite(api, company, identifier="bob", role="admin")
    member = await api.post(
        "/v1/memberships",
        json={"company_id": company["id"], "user_id": other_user["id"]},
    )
    assert member.status_code == 201
    assert (await other_api.post(f"/v1/invitations/{row['id']}/accept")).status_code == 409
    assert (await other_api.get(f"/v1/memberships/{member.json()['id']}")).json()["role"] == "user"


async def test_scoped_company_token_cannot_bypass_custom_resource_permissions(
    api, other_api, client, company, membership, company_agent
):
    """Newly issued company credentials inherit their issuer's current per-action restrictions."""
    await change(api, membership, role="admin", permissions=["tokens.manage", "tokens.read"])
    owner = (
        await other_api.post("/v1/token-ownerships", json={"company_id": company["id"]})
    ).json()
    token = (
        await other_api.post(
            "/v1/access-tokens",
            json={
                "ownership_id": owner["id"],
                "name": "Restricted",
                "scopes": ["read", "manage"],
            },
        )
    ).json()
    headers = {"Authorization": "Bearer " + token["token"]}
    assert (await client.get("/v1/computers", headers=headers)).json() == []
    assert (
        await client.get(f"/v1/computers/{company_agent['computer']['id']}", headers=headers)
    ).status_code == 403
    path = f"/v1/memberships/{membership['id']}"
    leaving = await client.delete(path, headers=headers)
    assert leaving.status_code == 403
    assert leaving.json()["detail"] == "A personal token is required"
    assert (await api.get(path)).status_code == 200
    await change(api, membership, permissions=["tokens.manage", "tokens.read", "resources.read"])
    owned = await client.get("/v1/computers", headers=headers, params={"company_id": company["id"]})
    assert owned.status_code == 200, owned.text
    assert [row["id"] for row in owned.json()] == [company_agent["computer"]["id"]]
