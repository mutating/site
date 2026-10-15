"""Real HTTP membership grants, private member profiles and invitation lifecycle."""

import asyncio
import io
from datetime import timedelta
from uuid import uuid4

import pytest
from PIL import Image

from throng_api.models import CompanyInvitation

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
            "/v1/memberships", params={"company_id": company["id"], "limit": 1, "offset": 1}
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
        api, membership, permissions=["resources.read", "resources.manage", "company.manage"]
    )
    assert granted["effective_permissions"] == [
        "company.manage",
        "resources.manage",
        "resources.read",
    ]
    assert (
        await other_api.patch(
            f"/v1/computers/{company_agent['computer']['id']}", json={"name": "Delegated"}
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
            f"/v1/memberships/{membership['id']}", json={"permissions": ["tokens.manage"]}
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
            json={"login": "future@example.com", "password": "long invitation password"},
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
    assert (await other_api.get(f"/v1/invitations/link/{row['token']}")).status_code == 404
    token = rotated.json()["token"]
    assert (await other_api.get(f"/v1/invitations/link/{token}")).status_code == 200
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


async def test_pending_invitation_cannot_overwrite_an_existing_membership(
    api, other_api, company, other_user
):
    """Accepting a previously issued invite never upgrades a membership created by another path."""
    row = await invite(api, company, identifier="bob", role="admin")
    member = await api.post(
        "/v1/memberships", json={"company_id": company["id"], "user_id": other_user["id"]}
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
            json={"ownership_id": owner["id"], "name": "Restricted", "scopes": ["read"]},
        )
    ).json()
    headers = {"Authorization": "Bearer " + token["token"]}
    assert (await client.get("/v1/computers", headers=headers)).json() == []
    assert (
        await client.get(f"/v1/computers/{company_agent['computer']['id']}", headers=headers)
    ).status_code == 403
