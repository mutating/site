"""Test-only app: real API, historical DB fixtures and an explicit registration fault.

Never imported by the production entrypoint. The test server binds loopback only.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from throng_api.app import create_app
from throng_api.models import AvailabilityBaseline, CapacityInterval, ComputerOwnership

app = create_app()
failed_logins = set()
production_lifespan = app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(app):
    async with production_lifespan(app):
        async with app.state.db.write() as session:
            baseline = await session.get(AvailabilityBaseline, 1)
            baseline.available_from = datetime(2026, 1, 1, tzinfo=UTC)
        yield


app.router.lifespan_context = fixture_lifespan


class HistoryFixture(BaseModel):
    computer_id: UUID
    pattern: Literal["full", "sparse"] = "full"


@app.post("/__test__/history")
async def historical_capacity(data: HistoryFixture):
    """Insert known past events; the browser still reads the production reporting endpoint."""
    async with app.state.db.write() as session:
        owner = await session.scalar(
            select(ComputerOwnership).where(ComputerOwnership.computer_id == str(data.computer_id))
        )
        week_start = datetime(2026, 1, 1, tzinfo=UTC)
        # Minutes into the requested week. Include a larger peak before the long idle gap.
        if data.pattern == "sparse":
            intervals = [
                (1440, 1490, 24_000, 64, 8000, 8),
                (7990, 8002, 5000, 8, 0, 1),
                (8002, 8017, 8000, 8, 3500, 4),
                (8022, 8034, 4000, 6, 1500, 2),
            ]
        else:
            intervals = [
                (0, 2 * 1440, 2500, 4, 500, 1.5),
                (2 * 1440, 4 * 1440, 6000, 12, 1500, 4),
                (4 * 1440, 7 * 1440, 500, 2, 250, 0.75),
            ]
        for start, end, cpu, ram, busy_cpu, busy_ram in intervals:
            session.add(
                CapacityInterval(
                    computer_id=str(data.computer_id),
                    user_id=owner.user_id,
                    company_id=owner.company_id,
                    closed=True,
                    starts_at=week_start + timedelta(minutes=start),
                    ends_at=week_start + timedelta(minutes=end),
                    cpu_millis=cpu,
                    memory_bytes=int(ram * 1024**3),
                    cpu_occupied_millis=busy_cpu,
                    memory_occupied_bytes=int(busy_ram * 1024**3),
                )
            )
    return {"seeded": True}


@app.middleware("http")
async def fail_first_automatic_login(request, call_next):
    """Simulate a transient API outage after the real registration has committed."""
    if request.method == "POST" and request.url.path == "/v1/auth/session":
        data = await request.json()
        identity = data.get("login", "")
        if identity.startswith("register_retry_") and identity not in failed_logins:
            failed_logins.add(identity)
            return JSONResponse({"detail": "Temporary test outage"}, status_code=503)
    return await call_next(request)
