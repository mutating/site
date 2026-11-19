"""Account additions used by the independent dashboard, over real HTTP."""

import asyncio
import io

import pytest
from PIL import Image

from throng_api import accounts, avatars

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


async def test_company_detail_checks_membership_in_one_snapshot(
    api, other_api, other_user, company, membership, monkeypatch
):
    """A concurrent membership removal cannot invalidate an authorized detail response halfway."""
    authorized = asyncio.Event()
    release = asyncio.Event()
    company_access = accounts.company_access

    async def paused(session, principal, company_id, *args, **kwargs):
        authorized_member = await company_access(session, principal, company_id, *args, **kwargs)
        if principal.user_id == other_user["id"]:
            authorized.set()
            await release.wait()
        return authorized_member

    monkeypatch.setattr(accounts, "company_access", paused)
    path = f"/v1/companies/{company['id']}"
    reading = asyncio.create_task(other_api.get(path))
    try:
        await asyncio.wait_for(authorized.wait(), timeout=5)
        assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
    finally:
        release.set()
        response = await reading
    assert response.status_code == 200, response.text
    assert response.json()["my_membership_id"] == membership["id"]
    assert (await other_api.get(path)).status_code == 404


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


async def test_organization_images_are_independent_and_private(api, other_api, company, other_user):
    """Manage both organization images without exposing blobs or granting member write access."""
    path = f"/v1/companies/{company['id']}"
    avatar, background = f"{path}/images/avatar", f"{path}/images/background"
    payload = io.BytesIO()
    Image.new("RGB", (2000, 1000), "pink").save(payload, format="PNG")
    pixels = payload.getvalue()
    assert (await other_api.put(avatar, content=pixels)).status_code == 404
    assert (await other_api.get(avatar)).status_code == 404
    create = await api.put(avatar, content=pixels)
    assert create.status_code == 200
    avatar_version = create.json()["avatar_version"]
    assert avatar_version and create.json()["background_version"] is None
    invalid = await api.put(background, content=b"not an image")
    assert invalid.status_code == 422
    uploaded = await api.put(background, content=pixels)
    assert uploaded.status_code == 200
    background_version = uploaded.json()["background_version"]
    assert background_version and uploaded.json()["avatar_version"] == avatar_version
    assert (
        await api.post(
            "/v1/memberships",
            json={
                "company_id": company["id"],
                "user_id": other_user["id"],
                "role": "user",
            },
        )
    ).status_code == 201
    assert (await other_api.put(background, content=pixels)).status_code == 403
    assert (await other_api.delete(avatar)).status_code == 403
    for url, size in [(avatar, (512, 512)), (background, (1600, 800))]:
        response = await other_api.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, no-store"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == size
    record = (await api.get(path)).json()
    assert record["avatar_version"] == avatar_version
    assert record["background_version"] == background_version
    assert "avatar_data" not in record and "background_data" not in record
    removed = await api.delete(background)
    assert removed.status_code == 200
    assert removed.json() == {"avatar_version": avatar_version, "background_version": None}
    assert (await other_api.get(background)).status_code == 404


async def test_organization_image_read_cannot_see_a_replacement_after_access_is_removed(
    api, other_api, company, membership, monkeypatch
):
    """An authorized read can finish with its original image after membership is removed."""
    path = f"/v1/companies/{company['id']}/images/avatar"

    def png(color):
        payload = io.BytesIO()
        Image.new("RGB", (12, 12), color).save(payload, format="PNG")
        return payload.getvalue()

    assert (await api.put(path, content=png("pink"))).status_code == 200
    first_image = (await other_api.get(path)).content
    authorized = asyncio.Event()
    release = asyncio.Event()
    authorize = avatars.authorize_company_image

    async def paused(session, request, company_id, write=False):
        await authorize(session, request, company_id, write=write)
        if not write:
            authorized.set()
            await release.wait()

    monkeypatch.setattr(avatars, "authorize_company_image", paused)
    reading = asyncio.create_task(other_api.get(path))
    try:
        await asyncio.wait_for(authorized.wait(), timeout=5)
        assert (await api.delete(f"/v1/memberships/{membership['id']}")).status_code == 204
        assert (await api.put(path, content=png("blue"))).status_code == 200
    finally:
        release.set()
        response = await reading
    assert response.status_code == 200
    assert response.content == first_image
    assert (await api.get(path)).content != first_image
    assert (await other_api.get(path)).status_code == 404
