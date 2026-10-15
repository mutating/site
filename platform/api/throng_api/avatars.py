"""Private, size-limited profile images normalized before storage."""

import hashlib
import io
import warnings

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from throng_api.models import User
from throng_api.schemas import UserOut
from throng_api.security import access_token_scheme, authenticate, personal

router = APIRouter(
    prefix="/v1/users/me/avatar", tags=["accounts"], dependencies=[Depends(access_token_scheme)]
)
MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000


def normalize_avatar(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if (
                    source.format not in {"PNG", "JPEG", "WEBP"}
                    or getattr(source, "n_frames", 1) != 1
                ):
                    raise ValueError("Unsupported image")
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("Image dimensions are too large")
                source.load()
                picture = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGBA"), (512, 512))
                output = io.BytesIO()
                # Encoding a new image also removes EXIF and other uploaded metadata.
                picture.save(output, format="WEBP", quality=85, exif=b"")
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise HTTPException(
            422, "Use a static PNG, JPEG or WebP image up to 16 megapixels"
        ) from error


@router.put("", response_model=UserOut)
async def upload_avatar(request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, "manage")
        personal(principal)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise HTTPException(413, "Avatar must not exceed 2 MiB")
    data = await anyio.to_thread.run_sync(normalize_avatar, bytes(body))
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user = await session.get(User, personal(principal))
        user.avatar_data = data
        user.avatar_version = hashlib.sha256(data).hexdigest()
        return user


@router.get("", response_class=Response, responses={200: {"content": {"image/webp": {}}}})
async def get_avatar(request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        data = await session.scalar(select(User.avatar_data).where(User.id == personal(principal)))
        if data is None:
            raise HTTPException(404, "Avatar not found")
        return Response(
            data,
            media_type="image/webp",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )


@router.delete("", status_code=204)
async def delete_avatar(request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user = await session.get(User, personal(principal))
        user.avatar_data = None
        user.avatar_version = None
