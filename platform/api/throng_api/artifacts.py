import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from throng_api import outputs as out
from throng_api.models import Artifact, new_id
from throng_api.schemas import PaginationOffset, StorageText
from throng_api.security import access_token_scheme, authenticate, owned, owned_filter, owner_values
from throng_api.serialization import public

router = APIRouter(
    prefix="/api/v1", tags=["artifacts"], dependencies=[Depends(access_token_scheme)]
)

BINARY_CONTENT = {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
BINARY_UPLOAD_DOCS = {
    "requestBody": {
        "description": "Raw file bytes (empty files are allowed).",
        "content": BINARY_CONTENT,
    }
}
BINARY_DOWNLOAD_RESPONSES = {
    200: {
        "description": "Original file bytes.",
        "content": BINARY_CONTENT,
        "headers": {
            "X-Content-SHA256": {
                "schema": {"type": "string"},
                "description": "SHA-256 checksum of the file.",
            }
        },
    }
}


async def receive_file(request):
    settings = request.app.state.settings
    key = new_id()
    path = settings.storage_path / key
    size = 0
    checksum = hashlib.sha256()
    try:
        async with await anyio.open_file(path, "xb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_artifact_bytes:
                    raise HTTPException(413, "Artifact exceeds the configured size limit")
                checksum.update(chunk)
                await stream.write(chunk)
        return path, size, checksum.hexdigest()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def check_storage_quota(session, owner, size, settings):
    condition = (
        Artifact.user_id == owner["user_id"]
        if owner["user_id"]
        else Artifact.company_id == owner["company_id"]
    )
    used = await session.scalar(
        select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(condition)
    )
    if used + size > settings.max_owner_storage_bytes:
        raise HTTPException(409, "Owner artifact storage quota exceeded")


def artifact_file(artifact, settings):
    path = settings.storage_path / artifact.storage_key
    if not path.is_file():
        raise HTTPException(503, "Artifact storage is unavailable")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=artifact.filename,
        headers={"X-Content-SHA256": artifact.sha256},
    )


@router.post(
    "/artifacts", status_code=201, response_model=out.Artifact, openapi_extra=BINARY_UPLOAD_DOCS
)
async def upload_artifact(
    request: Request,
    company_id: UUID | None = None,
    filename: Annotated[StorageText, Query(min_length=1, max_length=200)] = "state.tar",
):
    # Authenticate before accepting bytes; recheck permissions after the upload.
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, "execute")
        await owner_values(session, principal, company_id)
    path, size, checksum = await receive_file(request)
    keep = False
    try:
        async with request.app.state.db.write() as session:
            principal = await authenticate(session, request, "execute")
            owner = await owner_values(session, principal, company_id)
            await check_storage_quota(session, owner, size, request.app.state.settings)
            row = Artifact(
                **owner,
                filename=Path(filename).name,
                storage_key=path.name,
                size_bytes=size,
                sha256=checksum,
            )
            session.add(row)
            await session.flush()
            response = public(row)
            # Once COMMIT starts, an exception need not mean it rolled back.
            # Preserve a possible orphan rather than delete committed content.
            keep = True
        return response
    finally:
        if not keep:
            path.unlink(missing_ok=True)


@router.get("/artifacts", response_model=list[out.Artifact])
async def list_artifacts(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        rows = await session.scalars(
            select(Artifact)
            .where(owned_filter(Artifact, principal))
            .order_by(Artifact.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


@router.get("/artifacts/{artifact_id}", response_model=out.Artifact)
async def get_artifact(artifact_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(
            await owned(session, Artifact, artifact_id, await authenticate(session, request))
        )


@router.get(
    "/artifacts/{artifact_id}/content",
    response_class=FileResponse,
    responses=BINARY_DOWNLOAD_RESPONSES,
)
async def download_artifact(artifact_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        row = await owned(session, Artifact, artifact_id, await authenticate(session, request))
        return artifact_file(row, request.app.state.settings)


@router.delete("/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(artifact_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        row = await owned(session, Artifact, artifact_id, principal)
        key = row.storage_key
        await session.delete(row)
    # FK restrictions prevent removing states/results still used by execution history.
    (request.app.state.settings.storage_path / key).unlink(missing_ok=True)
