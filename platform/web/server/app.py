"""Serve the dashboard UI and public configuration; all data goes directly to the API."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", env_file=".env", extra="ignore")
    api_url: AnyHttpUrl = "https://api.mutating.tech/v1"


def create_app(settings=None):
    settings = settings or Settings()
    api_url = str(settings.api_url).rstrip("/")
    parsed = urlsplit(api_url)
    api_origin = f"{parsed.scheme}://{parsed.netloc}"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' blob: data: {api_origin}; font-src 'self'; "
            f"connect-src 'self' {api_origin}; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        return response

    @app.get("/config.js")
    async def configuration():
        return Response(
            "window.__TMC_API_URL__ = " + json.dumps(api_url) + ";\n",
            media_type="application/javascript",
        )

    dist = ROOT / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if "." in path or path.split("/", 1)[0] in {"account", "api", "v1", "agent", "health"}:
            raise HTTPException(404)
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "Build the dashboard with npm run build")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
