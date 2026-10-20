"""Browser sessions use the API's revocable access tokens in host-only cookies."""

import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.datastructures import MutableHeaders

from throng_api import accounts
from throng_api.models import AccessToken
from throng_api.schemas import Login, UserOut
from throng_api.security import SESSION_COOKIE, access_token_scheme, digest

router = APIRouter(prefix="/v1/auth/session", tags=["accounts"])


def clear_cookie(response, settings):
    response.delete_cookie(
        SESSION_COOKIE,
        path="/v1/",
        secure=settings.browser_secure_cookie,
        httponly=True,
        samesite="lax",
    )


class BrowserSessionMiddleware:
    """Check cookie writes without consuming body or disconnect events."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        cookie_auth = SESSION_COOKIE in request.cookies and "authorization" not in request.headers
        session_endpoint = request.url.path == "/v1/auth/session"
        if (
            request.url.path.startswith("/v1/")
            and (cookie_auth or session_endpoint)
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and (
                request.headers.get("origin") not in self.settings.browser_origins
                or request.headers.get("x-requested-with") != "ThrongDashboard"
            )
        ):
            response = JSONResponse({"detail": "Invalid request origin"}, status_code=403)
            await response(scope, receive, send)
            return

        async def send_response(message):
            if message["type"] == "http.response.start" and (cookie_auth or session_endpoint):
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                if message["status"] == 401 or (
                    message["status"] == 204
                    and request.method == "DELETE"
                    and re.fullmatch(r"/v1/users/[0-9a-fA-F-]{36}", request.url.path)
                ):
                    expired = Response()
                    clear_cookie(expired, self.settings)
                    headers.append("Set-Cookie", expired.headers["set-cookie"])
            await send(message)

        await self.app(scope, receive, send_response)


@router.post("", response_model=UserOut)
async def login(data: Login, request: Request, response: Response):
    issued, profile = await accounts.issue_login(data, request)
    response.set_cookie(
        SESSION_COOKIE,
        issued["access_token"],
        path="/v1/",
        secure=request.app.state.settings.browser_secure_cookie,
        httponly=True,
        samesite="lax",
        expires=issued["token"]["expires_at"],
    )
    return profile


@router.get("", response_model=UserOut, dependencies=[Depends(access_token_scheme)])
async def session(request: Request):
    return await accounts.me(request)


@router.delete("", status_code=204)
async def logout(request: Request):
    raw = request.cookies.get(SESSION_COOKIE, "")
    async with request.app.state.db.write() as session:
        token = await session.scalar(
            select(AccessToken).where(AccessToken.token_hash == digest(raw))
        )
        if token:
            await session.delete(token)
    result = Response(status_code=204)
    clear_cookie(result, request.app.state.settings)
    return result
