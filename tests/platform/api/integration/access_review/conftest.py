"""HTTP resources and SQL scheduling gates for the API access review regressions."""

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.util.concurrency import await_only

from throng_api import accounts
from throng_api.models import AgentCredential, User


@pytest.fixture
async def shared_review_computer(api, computer, company, membership):
    """Transfer an idle computer to a company that both HTTP users can initially access."""
    response = await api.patch(
        f"/api/v1/computer-ownerships/{computer['ownership']['id']}",
        json={"company_id": company["id"]},
    )
    assert response.status_code == 200, response.text
    return computer


@pytest.fixture
async def paused_computer_list(app, shared_review_computer):
    """Pause after the list's real computer SELECT, before any separate relationship loading."""
    entered, resume = asyncio.Event(), asyncio.Event()

    def pause_after_computer_query(connection, cursor, statement, parameters, context, executemany):
        """Keep the query's actual result while another HTTP request transfers the computer."""
        if (
            not entered.is_set()
            and statement.startswith("SELECT")
            and "JOIN computer_ownerships" in statement
            and "ORDER BY computers.id" in statement
        ):
            entered.set()
            await_only(resume.wait())

    event.listen(
        app.state.db.engine.sync_engine, "after_cursor_execute", pause_after_computer_query
    )
    try:
        yield entered, resume
    finally:
        resume.set()
        event.remove(
            app.state.db.engine.sync_engine, "after_cursor_execute", pause_after_computer_query
        )


@pytest.fixture
async def paused_agent_computer_lookup(agent_api, monkeypatch, request):
    """Pause after reading a real agent credential so its idle computer can be deleted."""
    original = AsyncSession.scalar
    entered, resume = asyncio.Event(), asyncio.Event()

    reads = 0

    async def pause_after_credential(session, *args, **kwargs):
        """Pause proof preflight or handler revalidation after the real credential read."""
        nonlocal reads
        row = await original(session, *args, **kwargs)
        if isinstance(row, AgentCredential):
            reads += 1
            if reads == request.param:
                entered.set()
                await resume.wait()
        return row

    monkeypatch.setattr(AsyncSession, "scalar", pause_after_credential)
    try:
        yield entered, resume
    finally:
        resume.set()


@pytest.fixture
async def paused_profile_lookup(user, monkeypatch):
    """Pause the profile query after authentication's actual issuer lookup has succeeded."""
    original = AsyncSession.get
    entered, resume = asyncio.Event(), asyncio.Event()
    reads = {}

    async def pause_before_profile(session, entity, identity, **kwargs):
        """Allow the second user lookup to observe a concurrent, committed HTTP deletion."""
        if entity is User and identity == user["id"]:
            reads[session] = reads.get(session, 0) + 1
            if reads[session] == 2 and not entered.is_set():
                entered.set()
                await resume.wait()
        return await original(session, entity, identity, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", pause_before_profile)
    try:
        yield entered, resume
    finally:
        resume.set()


@pytest.fixture
async def paginated_review_computers(api):
    """Register three computers with distinct built-in capacities for collection pagination."""
    computers = []
    for index in range(3):
        response = await api.post(
            "/api/v1/computers",
            json={
                "name": f"pagination-{index}",
                "os": "linux",
                "architecture": "amd64",
                "runtime": "oci",
                "cpu_millis": 2000 + index,
                "memory_bytes": 2048 + index,
                "disk_bytes": 8192,
            },
        )
        assert response.status_code == 201, response.text
        computers.append(response.json())
    return sorted(computers, key=lambda computer: computer["id"])


@pytest.fixture
async def password_hash_observer(api, other_user, access_token, monkeypatch):
    """Observe real Argon2 work after HTTP setup and optionally pause its return for revocation."""
    original = accounts.hash_password
    state = {
        "calls": 0,
        "pause": False,
        "entered": asyncio.Event(),
        "resume": asyncio.Event(),
    }

    async def observe_real_hash(password):
        """Execute the production hash and allow another HTTP write before returning it."""
        state["calls"] += 1
        result = await original(password)
        if state["pause"]:
            state["entered"].set()
            await state["resume"].wait()
        return result

    monkeypatch.setattr(accounts, "hash_password", observe_real_hash)
    try:
        yield state
    finally:
        state["resume"].set()
