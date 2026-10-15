"""Fault-injection resources for states that cannot be produced by a compliant HTTP peer."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from throng_api import controller
from throng_api.cli import migration_config
from throng_api.models import Artifact


@pytest.fixture
async def dashboard_downgrade_collision(client, monkeypatch):
    """Force a generated legacy nickname collision while preserving real migration SQL."""
    import uuid

    collision, unique = uuid.uuid4(), uuid.uuid4()
    existing = await client.post(
        "/v1/users",
        json={
            "email": "named@example.com",
            "password": "long enough password",
            "nickname": "user_" + collision.hex,
        },
    )
    anonymous = await client.post(
        "/v1/users",
        json={"email": "unnamed@example.com", "password": "long enough password"},
    )
    assert existing.status_code == anonymous.status_code == 201
    sequence = iter([collision, unique])
    original = uuid.uuid4
    monkeypatch.setattr(uuid, "uuid4", lambda: next(sequence, original()))
    return {
        "existing": existing.json(),
        "anonymous": anonymous.json(),
        "nickname": "user_" + unique.hex,
    }


@pytest.fixture
def alembic_config(settings):
    """Expose the real migration configuration for upgrade and downgrade checks."""
    return migration_config(settings)


@pytest.fixture
async def uncertain_commit(app, run_task, monkeypatch, request):
    """Inject an error after a committed artifact transaction, modelling an ambiguous COMMIT response."""
    error_type = getattr(request, "param", RuntimeError)
    write = app.state.db.write
    state = {"enabled": True}

    @asynccontextmanager
    async def interrupted_write():
        """Commit normally before injecting an acknowledgement failure for artifact writes."""
        async with write() as session:
            yield session
            uploaded = any(isinstance(row, Artifact) for row in session.identity_map.values())
        if uploaded and state["enabled"]:
            raise error_type("commit acknowledgement lost")

    monkeypatch.setattr(app.state.db, "write", interrupted_write)
    return state


@pytest.fixture
async def broken_controller(app, client, monkeypatch):
    """Temporarily fail real controller ticks and expose a switch to permit recovery."""
    original_tick = controller.tick
    state = {"enabled": True, "attempted": asyncio.Event()}

    async def failing_tick(session, now, settings):
        """Represent a transient scheduler failure while retaining the production retry loop."""
        if state["enabled"]:
            state["attempted"].set()
            raise RuntimeError("transient database failure")
        await original_tick(session, now, settings)

    monkeypatch.setattr(controller, "tick", failing_tick)
    await asyncio.wait_for(state["attempted"].wait(), timeout=5)
    app.state.last_controller_tick = None
    return state


@pytest.fixture
async def missing_artifact_file(app, artifact, client):
    """Remove stored bytes to represent unavailable or damaged external artifact storage."""
    async with app.state.db.read() as session:
        row = await session.get(Artifact, artifact["id"])
        path = app.state.settings.storage_path / row.storage_key
    path.unlink()
    return artifact


@pytest.fixture
async def paused_controller(app, run_task, monkeypatch):
    """Pause scheduling outside database transactions after a run has been delivered."""
    original = controller.run_once
    entered, resume = asyncio.Event(), asyncio.Event()

    async def gated_tick(application):
        """Wait before opening the production scheduler transaction."""
        entered.set()
        await resume.wait()
        await original(application)

    monkeypatch.setattr(controller, "run_once", gated_tick)
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        yield resume
    finally:
        resume.set()


@pytest.fixture
async def paused_provisioning_controller(app, create_task, monkeypatch):
    """Pause scheduling after create delivery while the isolate is still provisioning."""
    original = controller.run_once
    entered, resume = asyncio.Event(), asyncio.Event()

    async def gated_tick(application):
        """Pause before any scheduler transaction can update provisioning state."""
        entered.set()
        await resume.wait()
        await original(application)

    monkeypatch.setattr(controller, "run_once", gated_tick)
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        yield resume
    finally:
        resume.set()


@pytest.fixture
async def missing_coordination_row(app, client):
    """Remove the migration sentinel to simulate a damaged or incorrectly initialized database."""
    from sqlalchemy import delete

    from throng_api.models import Coordination

    async with app.state.db.engine.begin() as connection:
        await connection.execute(delete(Coordination))
    return app.state.db


@pytest.fixture
async def paused_login(user, monkeypatch):
    """Pause a real password verification before login rechecks the current database credentials."""
    from throng_api import accounts

    verify = accounts.verify_password
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed_verification(encoded, password):
        """Keep the actual Argon2 verification and delay only its return to the login handler."""
        result = await verify(encoded, password)
        entered.set()
        await resume.wait()
        return result

    monkeypatch.setattr(accounts, "verify_password", delayed_verification)
    try:
        yield {"entered": entered, "resume": resume}
    finally:
        resume.set()


@pytest.fixture
async def legacy_occupied_database(
    alembic_config, app, http_server, run_task, computer, isolate, api, company
):
    """Build personal, corporate and detached inventory, then stop and downgrade the API while preserving active work."""
    import anyio
    from alembic import command as alembic

    extra_computers = []
    for company_id in [company["id"], None]:
        response = await api.post(
            "/v1/computers",
            json={
                "company_id": company_id,
                "os": "linux",
                "architecture": "amd64",
                "runtime": "oci",
                "cpu_millis": 3000,
                "memory_bytes": 4096,
                "disk_bytes": 8192,
            },
        )
        assert response.status_code == 201, response.text
        extra_computers.append(response.json())
    response = await api.delete(f"/v1/computer-ownerships/{extra_computers[-1]['ownership']['id']}")
    assert response.status_code == 204, response.text
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0001")
    return {
        "computer": computer,
        "isolate": isolate,
        "task": run_task,
        "extra_computers": extra_computers,
    }


@pytest.fixture
async def migration_database(settings):
    """Provide a separate database connection that survives migration tests without starting a controller."""
    from throng_api.database import Database

    database = Database(settings.database_url)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def rejected_resource_history(app, computer_resources, monkeypatch):
    """Fail a history INSERT after a capacity update has been flushed to test transactional rollback."""
    from sqlalchemy import event

    def reject_history(connection, cursor, statement, parameters, context, executemany):
        """Represent a database failure while persisting the immutable resource event."""
        if statement.startswith("INSERT INTO computer_resource_changes"):
            raise RuntimeError("resource history write failed")

    event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", reject_history)
    try:
        yield
    finally:
        event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", reject_history)


@pytest.fixture
async def paused_company_read(company, monkeypatch):
    """Pause one company read after permission checking so a concurrent HTTP deletion can commit."""
    from throng_api import accounts

    original = accounts.company_access
    state = {"entered": asyncio.Event(), "resume": asyncio.Event(), "armed": True}

    async def pause_after_access(*args, **kwargs):
        """Keep the real permission query and suspend only its first successful result."""
        await original(*args, **kwargs)
        if state["armed"]:
            state["armed"] = False
            state["entered"].set()
            await state["resume"].wait()

    monkeypatch.setattr(accounts, "company_access", pause_after_access)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def paused_token_owner_lookup(access_token, monkeypatch):
    """Pause authentication after reading a delegated token but before looking up its ownership."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from throng_api.models import AccessToken

    original = AsyncSession.scalar
    state = {"entered": asyncio.Event(), "resume": asyncio.Event()}

    async def pause_after_token(session, *args, **kwargs):
        """Return real query results after allowing the target token's owner to be deleted concurrently."""
        row = await original(session, *args, **kwargs)
        if isinstance(row, AccessToken) and row.id == access_token["id"]:
            state["entered"].set()
            await state["resume"].wait()
        return row

    monkeypatch.setattr(AsyncSession, "scalar", pause_after_token)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def paused_token_issuer_lookup(access_token, monkeypatch):
    """Pause a personal token's authentication after ownership lookup, before checking that its issuer still exists."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from throng_api.models import TokenOwnership

    original = AsyncSession.get
    state = {"entered": asyncio.Event(), "resume": asyncio.Event()}

    async def pause_after_owner(session, entity, identity, **kwargs):
        """Preserve the loaded ownership while another HTTP request deletes its token issuer."""
        row = await original(session, entity, identity, **kwargs)
        if entity is TokenOwnership and identity == access_token["ownership_id"]:
            state["entered"].set()
            await state["resume"].wait()
        return row

    monkeypatch.setattr(AsyncSession, "get", pause_after_owner)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def paused_corporate_issuer_lookup(company_token, monkeypatch):
    """Pause a corporate token read before checking its issuer's current administrative membership."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from throng_api.models import AccessToken

    original = AsyncSession.scalar
    state = {"entered": asyncio.Event(), "resume": asyncio.Event()}

    async def pause_after_token(session, *args, **kwargs):
        """Keep the originally read token while a role change and automatic revocation commit elsewhere."""
        row = await original(session, *args, **kwargs)
        if isinstance(row, AccessToken) and row.id == company_token["id"]:
            state["entered"].set()
            await state["resume"].wait()
        return row

    monkeypatch.setattr(AsyncSession, "scalar", pause_after_token)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def finished_execution(api, agent_api, run_task, command, isolate):
    """Complete a command and confirm isolate destruction entirely through HTTP before internal retry checks."""
    response = await agent_api.post(
        f"/agent/v1/tasks/{run_task['id']}/complete",
        json={"returncode": 0, "stdout": "saved result"},
    )
    assert response.status_code == 200, response.text
    response = await api.delete(f"/v1/isolates/{isolate['id']}")
    assert response.status_code == 202, response.text
    response = await agent_api.post("/agent/v1/tasks/claim", json={"wait_seconds": 2})
    assert response.status_code == 200, response.text
    destroy = response.json()["tasks"][0]
    assert destroy["kind"] == "destroy"
    response = await agent_api.post(f"/agent/v1/tasks/{destroy['id']}/complete", json={})
    assert response.status_code == 200, response.text
    return {"command": command, "isolate": isolate, "run_task": run_task, "destroy": destroy}


@pytest.fixture
async def missing_run_assignment(app, run_task):
    """Simulate a lost execution assignment while preserving its dispatched command for reconciliation."""
    from throng_api.models import AgentTask

    async with app.state.db.write() as session:
        await session.delete(await session.get(AgentTask, run_task["id"]))


@pytest.fixture
async def terminal_run_assignment(app, run_task, clock):
    """Persist a terminal task awaiting command-result reconciliation, a state a recovery path must tolerate."""
    from throng_api.models import AgentTask

    async with app.state.db.write() as session:
        row = await session.get(AgentTask, run_task["id"])
        row.status = "failed"
        row.finished_at = clock.now
        row.error = "original worker failure"


@pytest.fixture
async def unknown_task_kind(app, run_task):
    """Represent an incompatible stored task kind while preserving its known ID and delivered assignment."""
    from throng_api.models import AgentTask

    async with app.state.db.write() as session:
        row = await session.get(AgentTask, run_task["id"])
        row.kind = "unknown_operation"


@pytest.fixture
async def memory_database():
    """Keep an actual in-memory SQLite schema alive for connection, transaction and constraint tests."""
    from throng_api.database import Database
    from throng_api.models import Base, Coordination

    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(Coordination.__table__.insert().values(id=1, revision=0))
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def postgres_configuration():
    """Configure the real asyncpg dialect without requiring an external database or opening a connection."""
    from throng_api.database import Database

    database = Database("postgresql+asyncpg://test@127.0.0.1/throng_configuration_test")
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def memory_migration_probe(tmp_path):
    """Inspect the migrated in-memory schema before Alembic closes its last connection and the database disappears."""
    from sqlalchemy import event, inspect, text
    from sqlalchemy.engine import Engine

    from throng_api.config import Settings

    state = {
        "settings": Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            storage_path=tmp_path / "artifacts",
            _env_file=None,
        )
    }

    def inspect_completed_revision(connection, cursor, statement, parameters, context, executemany):
        """Observe successful migration version writes while retaining real SQL execution and schema reflection."""
        if statement.startswith(("INSERT INTO alembic_version", "UPDATE alembic_version")):
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision == "0006":
                state["revision"] = revision
                state["types"] = connection.execute(
                    text("SELECT code, unit FROM resource_types ORDER BY code")
                ).all()
                state["columns"] = {
                    row["name"] for row in inspect(connection).get_columns("computers")
                }
                state["tables"] = set(inspect(connection).get_table_names())

    event.listen(Engine, "after_cursor_execute", inspect_completed_revision)
    try:
        yield state
    finally:
        event.remove(Engine, "after_cursor_execute", inspect_completed_revision)


@pytest.fixture
def resource_query_observer(app):
    """Observe actual catalog SQL and materialized history rows without replacing database operations."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from throng_api.models import ComputerResourceChange

    state = {"catalog_queries": [], "history_rows": []}

    def record_query(connection, cursor, statement, parameters, context, executemany):
        """Count reads of resource metadata, including joins, on this application's engine."""
        if statement.lstrip().upper().startswith("SELECT") and "resource_types" in statement:
            state["catalog_queries"].append(statement)

    def record_history(session, instance):
        """Count real ORM materialization instead of relying on timing-sensitive benchmarks."""
        if isinstance(instance, ComputerResourceChange):
            state["history_rows"].append(instance.id)

    event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", record_query)
    event.listen(Session, "loaded_as_persistent", record_history)
    try:
        yield state
    finally:
        event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", record_query)
        event.remove(Session, "loaded_as_persistent", record_history)


@pytest.fixture
async def large_resource_inventory(api, admin_api, computer):
    """Create ten custom resource types and capacities through HTTP alongside CPU and memory."""
    for number in range(10):
        response = await admin_api.post(
            "/v1/resource-types",
            json={"name": f"Device {number}", "code": f"device{number}", "unit": "device"},
        )
        assert response.status_code == 201, response.text
        response = await api.post(
            f"/v1/computers/{computer['id']}/resources",
            json={"resource_type_id": response.json()["id"], "total": number + 1},
        )
        assert response.status_code == 201, response.text
    return computer


@pytest.fixture
async def resource_history_series(api, computer, computer_resources, resource_type, clock):
    """Build repeated CPU snapshots and introduce a custom type midway through the HTTP-created series."""
    from datetime import timedelta

    start = clock.now
    for number in range(1, 13):
        clock.now += timedelta(seconds=1)
        response = await api.patch(
            f"/v1/computer-resources/{computer_resources['cpu']['id']}",
            json={"total": 2000 + number},
        )
        assert response.status_code == 200, response.text
        if number == 6:
            response = await api.post(
                f"/v1/computers/{computer['id']}/resources",
                json={"resource_type_id": resource_type["id"], "total": 4},
            )
            assert response.status_code == 201, response.text
    return {"start": start, "end": clock.now}


@pytest.fixture
async def paused_resource_history(monkeypatch):
    """Pause a real resource-history authorization so ownership can change before the owner scope is read."""
    from throng_api import resources

    original = resources.resource_for_user
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed_resource(*args, **kwargs):
        """Retain actual authorization and pause only the history handler after its successful check."""
        row = await original(*args, **kwargs)
        if kwargs.get("include_deleted"):
            entered.set()
            await resume.wait()
        return row

    monkeypatch.setattr(resources, "resource_for_user", delayed_resource)
    try:
        yield entered, resume
    finally:
        resume.set()


@pytest.fixture
async def paused_resource_inventory(monkeypatch):
    """Pause the inventory route once after actual computer authorization, allowing a concurrent ownership transfer."""
    from throng_api import resources

    original = resources.computer_for_user
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed_computer(*args, **kwargs):
        """Delay only the first authorized read and leave all database and permission checks intact."""
        row = await original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            await resume.wait()
        return row

    monkeypatch.setattr(resources, "computer_for_user", delayed_computer)
    try:
        yield entered, resume
    finally:
        resume.set()


@pytest.fixture
async def controller_shutdown_probe(app, http_server, computer_resources, monkeypatch, request):
    """Hold a controller tick inside a real write transaction and observe Uvicorn's actual shutdown signal."""
    from sqlalchemy import select

    from throng_api.models import Coordination

    original_tick = controller.tick
    original_put = http_server.server.lifespan.receive_queue.put
    state = {
        "entered": asyncio.Event(),
        "resume": asyncio.Event(),
        "shutdown_requested": asyncio.Event(),
        "completed": False,
        "cancelled": False,
        "fail": request.param,
    }

    async def gated_tick(session, now, settings):
        """Pause after real SQL has acquired the coordination lock, then run the actual scheduler tick."""
        state["revision"] = await session.scalar(select(Coordination.revision))
        state["entered"].set()
        try:
            await state["resume"].wait()
            await original_tick(session, now, settings)
            state["completed"] = True
            if state["fail"]:
                raise RuntimeError("controller transaction failed during shutdown")
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def observe_lifespan_message(message):
        """Deliver the real ASGI message and expose the shutdown request without delaying application cleanup."""
        await original_put(message)
        if message["type"] == "lifespan.shutdown":
            state["shutdown_requested"].set()

    monkeypatch.setattr(controller, "tick", gated_tick)
    monkeypatch.setattr(http_server.server.lifespan.receive_queue, "put", observe_lifespan_message)
    await asyncio.wait_for(state["entered"].wait(), timeout=5)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def paused_command_poll(running_command, monkeypatch):
    """Pause the first actual command SELECT after its snapshot is read, allowing completion before the response is built."""
    from sqlalchemy.ext.asyncio import AsyncSession

    command_id = running_command["command"]["id"]
    original_execute = AsyncSession.execute
    state = {"entered": asyncio.Event(), "resume": asyncio.Event(), "armed": True}

    async def delayed_execute(session, statement, *args, **kwargs):
        """Keep real SQL and pause only a command read containing this poll's command ID."""
        result = await original_execute(session, statement, *args, **kwargs)
        sql = str(statement)
        parameters = str(statement.compile().params)
        if state["armed"] and "FROM commands" in sql and command_id in parameters:
            state["armed"] = False
            state["entered"].set()
            await state["resume"].wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", delayed_execute)
    try:
        yield state
    finally:
        state["resume"].set()


@pytest.fixture
async def polling_commands(api, isolate):
    """Queue twelve real commands for batch polling without fabricating execution records."""
    from uuid import uuid4

    commands = []
    for number in range(12):
        response = await api.post(
            f"/v1/isolates/{isolate['id']}/commands",
            headers={"Idempotency-Key": str(uuid4())},
            json={"command": f"echo {number}"},
        )
        assert response.status_code == 202, response.text
        commands.append(response.json())
    return commands


@pytest.fixture
def polling_query_observer(app):
    """Count actual command/result SELECTs issued by HTTP polling while excluding controller transactions."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    statements = []

    def mark_session(orm_state):
        """Mark ORM reads only when the session has not acquired the controller's coordination lock."""
        if not orm_state.is_select:
            orm_state.session.info["has_write"] = True
        if orm_state.is_select and not orm_state.session.info.get("has_write"):
            orm_state.update_execution_options(observe_command_poll=True)

    def observe(connection, cursor, statement, parameters, context, executemany):
        """Record marked real SQL queries that read commands or command results."""
        if context.execution_options.get("observe_command_poll") and (
            "WHERE commands.id" in statement or "WHERE command_results.command_id" in statement
        ):
            statements.append(statement)

    event.listen(Session, "do_orm_execute", mark_session)
    event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(Session, "do_orm_execute", mark_session)
        event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", observe)
