"""Account, company, membership and token contracts exercised over real HTTP."""

from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_user_crud_and_case_normalization(client):
    """Register, log in, read, update and delete a user without exposing secrets."""
    response = await client.post(
        "/api/v1/users",
        json={
            "email": "Mixed@EXAMPLE.com",
            "password": "a sufficiently long password",
            "first_name": " Mixed ",
            "last_name": " Example ",
            "nickname": "MiXeD",
        },
    )
    assert response.status_code == 201
    user = response.json()
    assert user["email"] == "mixed@example.com" and user["nickname"] == "mixed"
    assert user["first_name"] == "Mixed"
    assert "password" not in user and "password_hash" not in user
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "MIXED@example.com", "password": "a sufficiently long password"},
    )
    assert response.status_code == 200
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    for path in ["/api/v1/users/me", f"/api/v1/users/{user['id']}"]:
        response = await client.get(path)
        assert response.status_code == 200 and response.json() == user
    assert (await client.get("/api/v1/users")).json() == [user]
    response = await client.patch(
        f"/api/v1/users/{user['id']}",
        json={
            "email": "NEW@example.com",
            "nickname": "NEW",
            "first_name": "New",
            "last_name": "Name",
        },
    )
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com" and response.json()["nickname"] == "new"
    assert response.json()["last_name"] == "Name"
    assert (await client.delete(f"/api/v1/users/{user['id']}")).status_code == 204
    assert (await client.get("/api/v1/users/me")).status_code == 401


@pytest.mark.parametrize("field,value", [("email", "ALICE@example.com"), ("nickname", "ALICE")])
async def test_registration_uniqueness_is_case_insensitive(client, user, field, value):
    """Case variants cannot create duplicate email addresses or nicknames."""
    payload = {
        "email": "another@example.com",
        "password": user["password"],
        "first_name": "A",
        "last_name": "B",
        "nickname": "another",
    }
    payload[field] = value
    response = await client.post("/api/v1/users", json=payload)
    assert response.status_code == 409
    assert (await client.get("/api/v1/users/me", headers=user["headers"])).status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("password", "short"),
        ("email", "invalid"),
        ("nickname", "has space"),
        ("first_name", "a" * 101),
        ("unexpected", True),
    ],
)
async def test_registration_rejects_invalid_input(client, field, value):
    """Invalid registration fields and unknown properties produce validation errors."""
    payload = {
        "email": "valid@example.com",
        "password": "a sufficiently long password",
        "first_name": "A",
        "last_name": "B",
        "nickname": "valid",
    }
    payload[field] = value
    assert (await client.post("/api/v1/users", json=payload)).status_code == 422


@pytest.mark.parametrize("settings", [{"allow_registration": False}], indirect=True)
async def test_registration_can_be_disabled(client):
    """Deployment configuration prevents public account creation when disabled."""
    response = await client.post(
        "/api/v1/users",
        json={
            "email": "new@example.com",
            "password": "a sufficiently long password",
            "first_name": "A",
            "last_name": "B",
            "nickname": "new",
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "email,password",
    [
        ("alice@example.com", "correct horse battery staple"),
        ("absent@example.com", "wrong password"),
    ],
)
async def test_login_does_not_trim_passwords_or_disclose_missing_users(
    client, user, email, password
):
    """Wrong passwords, including removed whitespace, and unknown users return the same error."""
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401 and response.json()["detail"] == "Invalid email or password"


@pytest.mark.parametrize(
    "authorization", [None, "Basic abc", "Bearer", "Bearer unknown", "bearer invalid"]
)
async def test_invalid_authorization_is_rejected(client, authorization):
    """Protected endpoints reject missing, malformed and unknown bearer credentials."""
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401


async def test_users_cannot_read_modify_or_delete_each_other(api, other_user):
    """Personal CRUD hides other users while lists contain only the caller's profile."""
    path = f"/api/v1/users/{other_user['id']}"
    assert (await api.get(path)).status_code == 404
    assert (await api.patch(path, json={"first_name": "changed"})).status_code == 404
    assert (await api.delete(path)).status_code == 404
    assert all(row["id"] != other_user["id"] for row in (await api.get("/api/v1/users")).json())


async def test_password_change_revokes_all_previously_issued_tokens(
    api, client, user, access_token
):
    """Changing the password revokes login and delegated tokens and enables the new password."""
    response = await api.patch(
        f"/api/v1/users/{user['id']}", json={"password": "a completely different password"}
    )
    assert response.status_code == 200
    assert (await api.get("/api/v1/users/me")).status_code == 401
    assert (
        await client.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer " + access_token["token"]}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    ).status_code == 401
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": "a completely different password"},
    )
    assert response.status_code == 200
    assert (
        await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer " + response.json()["access_token"]},
        )
    ).status_code == 200


async def test_company_crud_and_creator_membership(api, company):
    """Company CRUD creates a creator membership and removes that membership on deletion."""
    path = f"/api/v1/companies/{company['id']}"
    assert (await api.get(path)).json() == company
    assert (await api.get("/api/v1/companies", params={"limit": 1})).json() == [company]
    assert (await api.get("/api/v1/companies", params={"offset": 1})).json() == []
    members = (await api.get("/api/v1/memberships", params={"company_id": company["id"]})).json()
    assert len(members) == 1 and members[0]["role"] == "creator"
    response = await api.patch(path, json={"name": "Renamed"})
    assert response.status_code == 200 and response.json()["name"] == "Renamed"
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    assert (await api.get(f"/api/v1/memberships/{members[0]['id']}")).status_code == 404


async def test_membership_crud_and_last_creator_protection(api, other_api, company, membership):
    """Manage memberships while preventing removal of the company's final creator."""
    path = f"/api/v1/memberships/{membership['id']}"
    assert (await other_api.get(path)).json() == membership
    duplicate = await api.post(
        "/api/v1/memberships",
        json={key: membership[key] for key in ["company_id", "user_id", "role"]},
    )
    assert duplicate.status_code == 409
    members = (await api.get("/api/v1/memberships", params={"company_id": company["id"]})).json()
    creator = next(row for row in members if row["role"] == "creator")
    creator_path = f"/api/v1/memberships/{creator['id']}"
    assert (await api.delete(creator_path)).status_code == 409
    assert (await api.patch(creator_path, json={"role": "user"})).status_code == 409
    assert (await other_api.patch(path, json={"role": "admin"})).status_code == 403
    assert (await api.patch(path, json={"role": "creator"})).status_code == 200
    assert (await api.delete(creator_path)).status_code == 204
    assert (await api.get(f"/api/v1/companies/{company['id']}")).status_code == 404
    assert (await other_api.get(path)).json()["role"] == "creator"


@pytest.mark.parametrize("membership", ["user", "admin"], indirect=True)
async def test_member_permissions_and_voluntary_departure(
    api, other_api, company, membership, user
):
    """Members may leave, ordinary members cannot administer, and admins cannot change creators."""
    members = (await api.get("/api/v1/memberships", params={"company_id": company["id"]})).json()
    creator = next(row for row in members if row["user_id"] == user["id"])
    assert (
        await other_api.patch(f"/api/v1/memberships/{creator['id']}", json={"role": "user"})
    ).status_code == 403
    response = await other_api.patch(f"/api/v1/companies/{company['id']}", json={"name": "Updated"})
    assert response.status_code == (200 if membership["role"] == "admin" else 403)
    assert (await other_api.delete(f"/api/v1/companies/{company['id']}")).status_code == 403
    assert (await other_api.delete(f"/api/v1/memberships/{membership['id']}")).status_code == 204
    assert (await other_api.get(f"/api/v1/companies/{company['id']}")).status_code == 404


async def test_membership_requires_an_existing_user(api, company):
    """Membership creation refuses unknown user UUIDs without leaving phantom members."""
    response = await api.post(
        "/api/v1/memberships", json={"company_id": company["id"], "user_id": str(uuid4())}
    )
    assert response.status_code == 404
    assert (
        len((await api.get("/api/v1/memberships", params={"company_id": company["id"]})).json())
        == 1
    )


async def test_user_deletion_with_membership_is_atomic(api, company, user):
    """A failed deletion of a company member preserves the user and valid login token."""
    assert (await api.delete(f"/api/v1/users/{user['id']}")).status_code == 409
    assert (await api.get("/api/v1/users/me")).status_code == 200
    assert (await api.get(f"/api/v1/companies/{company['id']}")).status_code == 200


async def test_token_crud_and_one_time_secret(api, client, access_token):
    """Tokens expose their secret once, support renaming, and become unusable after deletion."""
    path = f"/api/v1/access-tokens/{access_token['id']}"
    response = await api.get(path)
    assert response.status_code == 200
    assert "token" not in response.json() and "token_hash" not in response.json()
    rows = (await api.get("/api/v1/access-tokens")).json()
    assert any(row["id"] == access_token["id"] for row in rows)
    assert all("token_hash" not in row and "token" not in row for row in rows)
    response = await api.patch(path, json={"name": "Renamed token"})
    assert response.status_code == 200 and response.json()["name"] == "Renamed token"
    headers = {"Authorization": "Bearer " + access_token["token"]}
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 401


@pytest.mark.parametrize("mode", ["revoke", "expire", "delete_owner"])
async def test_token_revocation_expiration_and_owner_deletion(
    api, client, clock, access_token, token_ownership, mode
):
    """Revocation, expiry and deleting token ownership each immediately invalidate the secret."""
    path = f"/api/v1/access-tokens/{access_token['id']}"
    if mode == "expire":
        clock.now += timedelta(days=1, seconds=1)
    elif mode == "revoke":
        assert (await api.patch(path, json={"revoke": True})).status_code == 200
        assert (await api.patch(path, json={"revoke": False})).json()["revoked_at"] is not None
    else:
        assert (
            await api.delete(f"/api/v1/token-ownerships/{token_ownership['id']}")
        ).status_code == 204
        assert (await api.get(path)).status_code == 404
    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + access_token["token"]}
    )
    assert response.status_code == 401


async def test_token_ownership_crud_preserves_owner_xor(
    api, user, company, token_ownership, access_token
):
    """An ownership can move only after attached tokens are deleted, preserving exactly one owner."""
    path = f"/api/v1/token-ownerships/{token_ownership['id']}"
    assert (await api.get(path)).json() == token_ownership
    assert any(
        row["id"] == token_ownership["id"]
        for row in (await api.get("/api/v1/token-ownerships")).json()
    )
    assert (await api.patch(path, json={"company_id": company["id"]})).status_code == 409
    assert (await api.delete(f"/api/v1/access-tokens/{access_token['id']}")).status_code == 204
    response = await api.patch(path, json={"company_id": company["id"]})
    assert response.status_code == 200
    assert response.json()["user_id"] is None and response.json()["company_id"] == company["id"]
    response = await api.patch(path, json={"user_id": user["id"]})
    assert response.status_code == 200 and response.json()["company_id"] is None
    assert (await api.delete(path)).status_code == 204
    assert (await api.get(path)).status_code == 404


@pytest.mark.parametrize("both", [False, True])
async def test_token_ownership_rejects_zero_or_two_owners(api, user, company, both):
    """The ownership HTTP schema rejects both missing and simultaneously supplied owners."""
    payload = {"user_id": user["id"], "company_id": company["id"]} if both else {}
    assert (await api.post("/api/v1/token-ownerships", json=payload)).status_code == 422


@pytest.mark.parametrize("access_token", [{"scopes": ["manage"]}], indirect=True)
async def test_limited_token_cannot_escalate_by_reissue_or_password_change(
    client, user, token_ownership, access_token
):
    """A manage-only token cannot acquire execute/read scopes through reissue or a password reset."""
    headers = {"Authorization": "Bearer " + access_token["token"]}
    response = await client.post(
        "/api/v1/access-tokens",
        headers=headers,
        json={
            "ownership_id": token_ownership["id"],
            "name": "escalation",
            "scopes": ["manage", "execute"],
        },
    )
    assert response.status_code == 403
    assert (
        await client.patch(
            f"/api/v1/users/{user['id']}",
            headers=headers,
            json={"password": "attacker chosen password"},
        )
    ).status_code == 403
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "attacker chosen password"},
        )
    ).status_code == 401


async def test_company_token_cannot_use_personal_or_other_company_resources(
    client, api, company, company_token, user
):
    """A corporate token stays inside its company even when its issuer owns other resources."""
    second = await api.post("/api/v1/companies", json={"name": "Other company"})
    assert second.status_code == 201
    headers = {"Authorization": "Bearer " + company_token["token"]}
    assert (
        await client.get(f"/api/v1/companies/{company['id']}", headers=headers)
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/companies/{second.json()['id']}", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 403
    assert (
        await client.post("/api/v1/token-ownerships", headers=headers, json={"user_id": user["id"]})
    ).status_code == 403
    assert (await client.get("/api/v1/companies", headers=headers)).json() == [company]
    assert (
        await client.post("/api/v1/companies", headers=headers, json={"name": "Forbidden"})
    ).status_code == 403


@pytest.mark.parametrize("membership", ["admin"], indirect=True)
@pytest.mark.parametrize("remove", [False, True])
async def test_company_tokens_stay_revoked_after_membership_restore(
    api, other_api, client, company, membership, other_user, remove
):
    """Demoting or removing a token issuer revokes its corporate tokens permanently."""
    response = await other_api.post("/api/v1/token-ownerships", json={"company_id": company["id"]})
    assert response.status_code == 201
    response = await other_api.post(
        "/api/v1/access-tokens",
        json={
            "ownership_id": response.json()["id"],
            "name": "Bob CI",
            "scopes": ["read", "execute"],
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    company_path = f"/api/v1/companies/{company['id']}"
    assert (await client.get(company_path, headers=headers)).status_code == 200
    path = f"/api/v1/memberships/{membership['id']}"
    if remove:
        assert (await api.delete(path)).status_code == 204
    else:
        assert (await api.patch(path, json={"role": "user"})).status_code == 200
    assert (await client.get(company_path, headers=headers)).status_code == 401
    if remove:
        assert (
            await api.post(
                "/api/v1/memberships",
                json={"company_id": company["id"], "user_id": other_user["id"], "role": "admin"},
            )
        ).status_code == 201
    else:
        assert (await api.patch(path, json={"role": "admin"})).status_code == 200
    assert (await client.get(company_path, headers=headers)).status_code == 401


@pytest.mark.parametrize(
    "collection", ["companies", "memberships", "token-ownerships", "access-tokens"]
)
async def test_unknown_account_resources_return_not_found(api, collection):
    """Unknown resource UUIDs return an HTTP 404 instead of leaking internal errors."""
    assert (await api.get(f"/api/v1/{collection}/{uuid4()}")).status_code == 404
