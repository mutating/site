"""Account additions used by the independent dashboard, over real HTTP."""

import io

import pytest
from PIL import Image

pytestmark = pytest.mark.anyio


async def test_company_deletion_preserves_other_members(api, other_api, company, membership):
    """Deletion cannot remove another member; it succeeds after that member explicitly leaves."""
    path = f"/v1/companies/{company['id']}"
    response = await api.delete(path)
    assert response.status_code == 409
    assert response.json()["detail"] == "Other members must leave before deleting the company"
    assert (await other_api.get(path)).status_code == 200
    assert (await other_api.get(f"/v1/memberships/{membership['id']}")).json() == membership
    assert (await other_api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    assert (await api.delete(path)).status_code == 204


async def test_company_deletion_preserves_tokens_and_empty_ownerships(
    api, client, company, company_token
):
    """Neither tokens nor their owner records are cascaded away when deleting a company."""
    path = f"/v1/companies/{company['id']}"
    response = await api.delete(path)
    assert response.status_code == 409
    assert (
        response.json()["detail"] == "Remove company token ownerships before deleting the company"
    )
    headers = {"Authorization": "Bearer " + company_token["token"]}
    assert (await client.get(path, headers=headers)).status_code == 200
    assert (await api.delete(f"/v1/access-tokens/{company_token['id']}")).status_code == 204
    assert (await api.delete(path)).status_code == 409
    assert (
        await api.delete(f"/v1/token-ownerships/{company_token['ownership_id']}")
    ).status_code == 204
    assert (await api.delete(path)).status_code == 204


async def test_minimal_registration_and_optional_nickname(client):
    """Register without optional fields, use a nickname for login, then remove it."""
    password = "  a sufficiently long password  "
    response = await client.post(
        "/v1/users", json={"email": "minimal@example.com", "password": password}
    )
    assert response.status_code == 201
    user = response.json()
    assert user["first_name"] == user["last_name"] == ""
    assert user["nickname"] is None and user["avatar_version"] is None
    login = await client.post(
        "/v1/auth/login", json={"login": user["email"].upper(), "password": password}
    )
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    response = await client.patch(
        f"/v1/users/{user['id']}",
        headers=headers,
        json={"nickname": "MiNi", "first_name": "", "last_name": ""},
    )
    assert response.json()["nickname"] == "mini"
    assert (
        await client.post("/v1/auth/login", json={"login": "MINI", "password": password})
    ).status_code == 200
    assert (
        await client.post("/v1/auth/login", json={"login": "MINI", "password": password.strip()})
    ).status_code == 401
    response = await client.patch(
        f"/v1/users/{user['id']}", headers=headers, json={"nickname": None}
    )
    assert response.json()["nickname"] is None
    assert (
        await client.post("/v1/auth/login", json={"login": "MINI", "password": password})
    ).status_code == 401
    assert (
        await client.post(
            "/v1/users", json={"email": "another-minimal@example.com", "password": password}
        )
    ).status_code == 201


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_avatar_normalization_privacy_and_deletion(api, other_api, format):
    """Normalize supported images, keep them private and remove stored content on request."""
    image = Image.new("RGB", (40, 20), color="pink")
    payload = io.BytesIO()
    image.save(payload, format=format)
    assert (await api.get("/v1/users/me/avatar")).status_code == 404
    response = await api.put("/v1/users/me/avatar", content=payload.getvalue())
    assert response.status_code == 200 and response.json()["avatar_version"]
    assert "avatar_data" not in response.json()
    response = await api.get("/v1/users/me/avatar")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, no-store"
    with Image.open(io.BytesIO(response.content)) as normalized:
        assert normalized.size == (512, 512)
    assert (await other_api.get("/v1/users/me/avatar")).status_code == 404
    assert (await api.delete("/v1/users/me/avatar")).status_code == 204
    assert (await api.get("/v1/users/me")).json()["avatar_version"] is None
    assert (await api.get("/v1/users/me/avatar")).status_code == 404


async def test_avatar_rejects_invalid_oversized_and_animated_images(api):
    """Reject malformed files, unsupported formats, large dimensions and oversized requests."""
    assert (await api.put("/v1/users/me/avatar", content=b"not an image")).status_code == 422
    assert (
        await api.put("/v1/users/me/avatar", content=b"x" * (2 * 1024 * 1024 + 1))
    ).status_code == 413
    for size, format in [((4001, 4000), "PNG"), ((4, 4), "GIF")]:
        payload = io.BytesIO()
        Image.new("RGB", size).save(payload, format=format)
        assert (await api.put("/v1/users/me/avatar", content=payload.getvalue())).status_code == 422
