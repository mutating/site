"""Independent dashboard service: encrypted cookies, API transport and static UI."""

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "tmc_account_session"
# Keep the API transport and session cookies compatible with existing browsers.
PREFIX = "/account"
UUID = r"[0-9a-fA-F-]{36}"
INVITATION_TOKEN = r"inv_[A-Za-z0-9_-]{43}"
ROUTES = {
    "GET": [
        r"users/me",
        r"users/me/avatar",
        r"companies",
        r"memberships",
        rf"companies/{UUID}",
        rf"companies/{UUID}/users/lookup",
        rf"memberships/{UUID}/avatar",
        r"invitations",
        rf"invitations/link/{INVITATION_TOKEN}",
        r"computers",
        rf"(?:users|companies)/{UUID}/resources/(?:summary|availability-history)",
    ],
    "POST": [
        r"companies",
        r"memberships",
        r"invitations",
        rf"invitations/{UUID}/(?:accept|decline|link)",
        rf"invitations/link/{INVITATION_TOKEN}/accept",
    ],
    "PATCH": [rf"users/{UUID}", rf"companies/{UUID}", rf"memberships/{UUID}"],
    "DELETE": [
        rf"users/{UUID}",
        r"users/me/avatar",
        rf"companies/{UUID}",
        rf"memberships/{UUID}",
        rf"invitations/{UUID}",
    ],
    "PUT": [r"users/me/avatar"],
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", env_file=".env", extra="ignore")
    api_url: str = "http://127.0.0.1:8000"
    public_origin: str = "http://127.0.0.1:5173"
    secure_cookie: bool = False
    session_key: str | None = None


def create_app(settings=None):
    settings = settings or Settings()
    if settings.secure_cookie and not settings.session_key:
        raise RuntimeError("Set DASHBOARD_SESSION_KEY for production")
    # Development-only key survives restarts and is excluded from Git.
    if not settings.session_key:
        directory = ROOT / ".runtime"
        directory.mkdir(exist_ok=True, mode=0o700)
        key_file = directory / "session.key"
        try:
            with key_file.open("xb") as output:
                key_file.chmod(0o600)
                output.write(Fernet.generate_key())
        except FileExistsError:
            pass
        settings.session_key = key_file.read_text()
    cipher = Fernet(settings.session_key.encode())

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=settings.api_url, timeout=20, trust_env=False
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def headers_and_csrf(request, call_next):
        if request.url.path.startswith(PREFIX + "/api/") and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            if (
                request.headers.get("origin") != settings.public_origin
                or request.headers.get("x-requested-with") != "ThrongDashboard"
            ):
                return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
        response = await call_next(request)
        if response.status_code == 401:
            clear_cookie(response)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    def read_session(request):
        try:
            session = json.loads(cipher.decrypt(request.cookies.get(COOKIE, "").encode()))
            if session["expires"] <= datetime.now(UTC).timestamp():
                raise ValueError("Expired session")
            return session
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise HTTPException(401, "Authentication required") from None

    def clear_cookie(response):
        response.delete_cookie(
            COOKIE, path=PREFIX + "/", secure=settings.secure_cookie, httponly=True, samesite="lax"
        )

    async def upstream(request, method, path, token=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            return await request.app.state.client.request(
                method, "/v1/" + path, headers=headers, **kwargs
            )
        except httpx.HTTPError:
            raise HTTPException(502, "API is temporarily unavailable") from None

    def forward(response):
        result = Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )
        if "x-total-count" in response.headers:
            result.headers["X-Total-Count"] = response.headers["x-total-count"]
        if response.status_code == 401:
            clear_cookie(result)
        return result

    @app.get(PREFIX + "/api/session")
    async def session(request: Request):
        session = read_session(request)
        return forward(await upstream(request, "GET", "users/me", session["token"]))

    async def establish_session(request, identity, password):
        response = await upstream(
            request, "POST", "auth/login", json={"login": identity, "password": password}
        )
        if response.status_code != 200:
            return forward(response)
        login = response.json()
        expires = datetime.fromisoformat(login["token"]["expires_at"].replace("Z", "+00:00"))
        profile = await upstream(request, "GET", "users/me", login["access_token"])
        result = forward(profile)
        if profile.status_code == 200:
            value = cipher.encrypt(
                json.dumps(
                    {
                        "token": login["access_token"],
                        "token_id": login["token"]["id"],
                        "expires": expires.timestamp(),
                    }
                ).encode()
            ).decode()
            result.set_cookie(
                COOKIE,
                value,
                path=PREFIX + "/",
                secure=settings.secure_cookie,
                httponly=True,
                samesite="lax",
                expires=expires,
            )
        return result

    async def body(request, limit=2 * 1024 * 1024):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise HTTPException(413, "Request is too large")
        return bytes(data)

    async def json_body(request):
        try:
            data = json.loads(await body(request, 16 * 1024))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return data
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, "Invalid JSON request") from None

    @app.post(PREFIX + "/api/session")
    async def login(request: Request):
        data = await json_body(request)
        return await establish_session(request, data.get("login", ""), data.get("password", ""))

    @app.post(PREFIX + "/api/register")
    async def register(request: Request):
        data = await json_body(request)
        response = await upstream(request, "POST", "users", json=data)
        if response.status_code != 201:
            return forward(response)
        try:
            result = await establish_session(request, data["email"], data["password"])
            if result.status_code == 200:
                return result
        except HTTPException:
            pass
        # Account creation has committed even if the next API call is unavailable.
        # Preserve that success so the browser retries login, never registration.
        result = JSONResponse({"account_created": True, "requires_login": True}, status_code=201)
        clear_cookie(result)
        return result

    @app.delete(PREFIX + "/api/session")
    async def logout(request: Request):
        try:
            session = read_session(request)
        except HTTPException:
            result = Response(status_code=204)
            clear_cookie(result)
            return result
        response = await upstream(
            request, "DELETE", "access-tokens/" + session["token_id"], session["token"]
        )
        if response.status_code not in {204, 401, 404}:
            return forward(response)
        result = Response(status_code=204)
        clear_cookie(result)
        return result

    @app.api_route(PREFIX + "/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def proxy(path: str, request: Request):
        if not any(re.fullmatch(pattern, path) for pattern in ROUTES[request.method]):
            raise HTTPException(404, "Resource not found")
        session = read_session(request)
        data = await body(request, 2 * 1024 * 1024 if request.method == "PUT" else 16 * 1024)
        response = await upstream(
            request,
            request.method,
            path,
            session["token"],
            params=request.query_params.multi_items(),
            content=data,
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
        result = forward(response)
        if (
            request.method == "DELETE"
            and re.fullmatch(rf"users/{UUID}", path)
            and response.status_code == 204
        ):
            clear_cookie(result)
        return result

    @app.get(PREFIX)
    @app.get(PREFIX + "/{path:path}")
    async def redirect(request: Request):
        path = request.path_params.get("path", "")
        target = "/" + quote(path.lstrip("/"), safe="/")
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=308)

    dist = ROOT / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if "." in path or path.split("/", 1)[0] in {"api", "agent", "health"}:
            raise HTTPException(404)
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "Build the dashboard with npm run build")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
