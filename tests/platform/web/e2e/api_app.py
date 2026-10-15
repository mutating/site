"""Test-only app: real API, historical DB fixtures and an explicit registration fault.

Never imported by the production entrypoint. The test server binds loopback only.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
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


@app.post("/__test__/history")
async def historical_capacity(data: HistoryFixture):
    """Insert known past events; the browser still reads the production reporting endpoint."""
    async with app.state.db.write() as session:
        owner = await session.scalar(
            select(ComputerOwnership).where(ComputerOwnership.computer_id == str(data.computer_id))
        )
        for start, end, cpu, ram, busy_cpu, busy_ram in [
            (1, 3, 2500, 4, 500, 1.5),
            (3, 5, 6000, 12, 1500, 4),
            (5, 8, 500, 2, 250, 0.75),
        ]:
            session.add(
                CapacityInterval(
                    computer_id=str(data.computer_id),
                    user_id=owner.user_id,
                    company_id=owner.company_id,
                    closed=True,
                    starts_at=datetime(2026, 1, start, tzinfo=UTC),
                    ends_at=datetime(2026, 1, end, tzinfo=UTC),
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
    if request.method == "POST" and request.url.path == "/v1/auth/login":
        data = await request.json()
        identity = data.get("login", "")
        if identity.startswith("register_retry_") and identity not in failed_logins:
            failed_logins.add(identity)
            return JSONResponse({"detail": "Temporary test outage"}, status_code=503)
    return await call_next(request)
