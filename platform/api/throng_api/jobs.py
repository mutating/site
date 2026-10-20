from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select

from throng_api import outputs as out
from throng_api.execution import (
    TASK_TERMINAL,
    TERMINAL,
    add_task,
    destroy_isolate,
    remember,
    replay,
    request_cancel,
)
from throng_api.models import (
    AgentTask,
    Artifact,
    CIReservation,
    Command,
    CommandResult,
    Computer,
    Isolate,
)
from throng_api.schemas import CommandCreate, IDs, IsolateCreate, PaginationOffset
from throng_api.security import access_token_scheme, authenticate, owned, owned_filter, owner_values
from throng_api.serialization import public

router = APIRouter(prefix="/v1", tags=["execution"], dependencies=[Depends(access_token_scheme)])


async def writable_isolate(session, isolate_id, principal):
    isolate = await owned(session, Isolate, isolate_id, principal)
    if await session.scalar(select(CIReservation.id).where(CIReservation.isolate_id == isolate.id)):
        raise HTTPException(409, "CI isolates are managed by Actions")
    return isolate


@router.post("/isolates", status_code=202, response_model=out.Isolate)
async def create_isolate(data: IsolateCreate, request: Request, idempotency_key: str = Header()):
    settings = request.app.state.settings
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        payload = data.model_dump(mode="json")
        # Preserve hashes of v1 requests stored before these optional fields existed.
        for name, default in (("isolation_mode", None), ("retry_allowed", True)):
            if payload[name] == default:
                del payload[name]
        previous = await replay(session, principal.key, "create_isolate", idempotency_key, payload)
        if previous:
            return public(await owned(session, Isolate, previous, principal))
        owner = await owner_values(session, principal, data.company_id)
        owner_condition = (
            Isolate.user_id == owner["user_id"]
            if owner["user_id"]
            else Isolate.company_id == owner["company_id"]
        )
        count = await session.scalar(
            select(func.count())
            .select_from(Isolate)
            .where(owner_condition, Isolate.status.in_(["provisioning", "ready", "destroying"]))
        )
        if count >= settings.max_owner_isolates:
            raise HTTPException(409, "Owner isolate quota exceeded")
        artifact = await owned(session, Artifact, data.state_artifact_id, principal)
        if (artifact.user_id, artifact.company_id) != (owner["user_id"], owner["company_id"]):
            raise HTTPException(422, "Initial state must have the same owner as the isolate")
        if artifact.size_bytes > data.disk_bytes:
            raise HTTPException(422, "Requested disk is smaller than the uploaded initial state")
        now = request.app.state.clock()
        values = data.model_dump(exclude={"company_id", "state_artifact_id"})
        isolate = Isolate(
            **values,
            **owner,
            state_artifact_id=str(data.state_artifact_id),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.isolate_ttl_seconds),
            provisioning_deadline=now + timedelta(seconds=settings.provisioning_timeout_seconds),
        )
        session.add(isolate)
        await session.flush()
        remember(session, principal.key, "create_isolate", idempotency_key, payload, isolate.id)
        return public(isolate)


@router.get("/isolates", response_model=list[out.Isolate])
async def list_isolates(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        rows = await session.scalars(
            select(Isolate)
            .where(owned_filter(Isolate, principal))
            .order_by(Isolate.created_at, Isolate.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


@router.get("/isolates/{isolate_id}", response_model=out.Isolate)
async def get_isolate(isolate_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(
            await owned(session, Isolate, isolate_id, await authenticate(session, request))
        )


@router.post("/isolates/{isolate_id}/renew", response_model=out.Isolate)
async def renew_isolate(isolate_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        isolate = await writable_isolate(session, isolate_id, principal)
        now = request.app.state.clock()
        if isolate.status not in {"provisioning", "ready"} or isolate.expires_at <= now:
            raise HTTPException(409, "Isolate is no longer renewable")
        isolate.expires_at = now + timedelta(seconds=request.app.state.settings.isolate_ttl_seconds)
        return public(isolate)


@router.delete("/isolates/{isolate_id}", status_code=202, response_model=out.Isolate)
async def delete_isolate(isolate_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        isolate = await writable_isolate(session, isolate_id, principal)
        await destroy_isolate(
            session, isolate, request.app.state.clock(), request.app.state.settings
        )
        return public(isolate)


@router.post("/isolates/{isolate_id}/commands", status_code=202, response_model=out.Command)
async def create_command(
    isolate_id: UUID, data: CommandCreate, request: Request, idempotency_key: str = Header()
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        isolate = await writable_isolate(session, isolate_id, principal)
        operation = f"command:{isolate.id}"
        payload = data.model_dump(mode="json")
        previous = await replay(session, principal.key, operation, idempotency_key, payload)
        if previous:
            return public(await session.get(Command, previous))
        now = request.app.state.clock()
        if isolate.status != "ready" or isolate.expires_at <= now:
            raise HTTPException(409, "Isolate is not ready")
        pending = await session.scalar(
            select(func.count())
            .select_from(Command)
            .where(Command.isolate_id == isolate.id, Command.status.not_in(TERMINAL))
        )
        if pending >= request.app.state.settings.max_pending_commands_per_isolate:
            raise HTTPException(409, "Pending command quota exceeded")
        isolate.next_sequence += 1
        command = Command(
            isolate_id=isolate.id,
            sequence=isolate.next_sequence,
            **data.model_dump(),
            created_at=now,
            queued_deadline=now
            + timedelta(seconds=request.app.state.settings.command_queue_timeout_seconds),
        )
        session.add(command)
        await session.flush()
        remember(session, principal.key, operation, idempotency_key, payload, command.id)
        return public(command)


async def command_for_user(session, principal, command_id, *, mutate=False):
    command = await session.get(Command, str(command_id))
    if not command:
        raise HTTPException(404, "Command not found")
    if mutate:
        await writable_isolate(session, command.isolate_id, principal)
    else:
        await owned(session, Isolate, command.isolate_id, principal)
    return command


def command_status(command):
    return {
        "id": command.id,
        "isolate_id": command.isolate_id,
        "status": command.status,
        "terminal": command.status in TERMINAL,
        "started_at": command.started_at,
        "finished_at": command.finished_at,
        "cancel_reason": command.cancel_reason,
    }


async def poll_commands(session, principal, ids, *, include_results=False):
    # One statement keeps status/results and ownership in the same database
    # snapshot. Status polling must not load command text, environments or logs.
    keys = [str(key) for key in ids]
    statement = (
        select(
            Command.id,
            Command.isolate_id,
            Command.status,
            Command.started_at,
            Command.finished_at,
            Command.cancel_reason,
        )
        .join(Isolate, Isolate.id == Command.isolate_id)
        .where(Command.id.in_(keys), owned_filter(Isolate, principal))
    )
    if include_results:
        statement = statement.add_columns(CommandResult).outerjoin(
            CommandResult, CommandResult.command_id == Command.id
        )
    rows = {row.id: row for row in (await session.execute(statement))}
    if set(keys) - rows.keys():
        raise HTTPException(404, "Command not found")
    return [rows[key] for key in keys]


@router.post("/commands/statuses", response_model=list[out.Status])
async def command_statuses(data: IDs, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        return [command_status(row) for row in await poll_commands(session, principal, data.ids)]


@router.post("/commands/results", response_model=list[out.ResultEnvelope])
async def command_results(data: IDs, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        keys = [str(key) for key in data.ids]
        # A repeated ID can multiply a large inline log up to 200 times. Check
        # lengths without fetching log contents, then recheck after the data
        # query in case an agent completed a command between the two reads.
        text_length = (
            func.coalesce(func.length(CommandResult.stdout), 0)
            + func.coalesce(func.length(CommandResult.stderr), 0)
            + func.coalesce(func.length(CommandResult.error), 0)
            + func.coalesce(func.length(Command.cancel_reason), 0)
        )
        sizes = {
            row.id: 1024 + 6 * row.text_length
            for row in (
                await session.execute(
                    select(Command.id, text_length.label("text_length"))
                    .join(Isolate, Isolate.id == Command.isolate_id)
                    .outerjoin(CommandResult, CommandResult.command_id == Command.id)
                    .where(Command.id.in_(keys), owned_filter(Isolate, principal))
                )
            )
        }
        if set(keys) - sizes.keys():
            raise HTTPException(404, "Command not found")
        budget = request.app.state.settings.max_results_response_bytes
        if sum(sizes[key] for key in keys) > budget:
            raise HTTPException(422, "Result batch exceeds response size limit")
        rows = await poll_commands(session, principal, data.ids, include_results=True)
        if (
            sum(
                1024
                + 6
                * (
                    len(row.cancel_reason or "")
                    + sum(
                        len(getattr(row.CommandResult, field) or "")
                        for field in ("stdout", "stderr", "error")
                    )
                    if row.CommandResult
                    else len(row.cancel_reason or "")
                )
                for row in rows
            )
            > budget
        ):
            raise HTTPException(422, "Result batch exceeds response size limit")
        return [
            {
                "id": row.id,
                "status": row.status,
                "result": public(row.CommandResult) if row.CommandResult else None,
            }
            for row in rows
        ]


@router.post("/commands/cancel", status_code=202, response_model=list[out.Status])
async def cancel_commands(data: IDs, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        commands = [
            await command_for_user(session, principal, key, mutate=True)
            for key in dict.fromkeys(data.ids)
        ]
        for command in commands:
            await request_cancel(
                session, command, request.app.state.clock(), request.app.state.settings
            )
        return [command_status(command) for command in commands]


@router.get("/commands/{command_id}", response_model=out.Command)
async def get_command(command_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(
            await command_for_user(session, await authenticate(session, request), command_id)
        )


@router.post("/commands/{command_id}/cancel", status_code=202, response_model=out.Status)
async def cancel_command(command_id: UUID, request: Request):
    return (await cancel_commands(IDs(ids=[command_id]), request))[0]


@router.get("/commands/{command_id}/result", response_model=out.ResultEnvelope)
async def command_result(command_id: UUID, request: Request):
    return (await command_results(IDs(ids=[command_id]), request))[0]


@router.post("/isolates/{isolate_id}/snapshots", status_code=202, response_model=out.AgentTask)
async def snapshot(isolate_id: UUID, request: Request, idempotency_key: str = Header()):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "execute")
        isolate = await writable_isolate(session, isolate_id, principal)
        operation = f"snapshot:{isolate.id}"
        previous = await replay(session, principal.key, operation, idempotency_key, {})
        if previous:
            return public(await session.get(AgentTask, previous))
        computer = await session.get(Computer, isolate.computer_id) if isolate.computer_id else None
        if computer and not computer.can_start_work:
            raise HTTPException(409, "Agent is not accepting work")
        if isolate.status != "ready" or isolate.expires_at <= request.app.state.clock():
            raise HTTPException(409, "Isolate is not ready")
        busy = await session.scalar(
            select(Command.id)
            .where(Command.isolate_id == isolate.id, Command.status.not_in(TERMINAL))
            .limit(1)
        )
        task_busy = await session.scalar(
            select(AgentTask.id)
            .where(AgentTask.isolate_id == isolate.id, AgentTask.status.not_in(TASK_TERMINAL))
            .limit(1)
        )
        if busy or task_busy:
            raise HTTPException(
                409, "Wait for isolate operations to finish before taking a snapshot"
            )
        task = await add_task(
            session,
            isolate,
            "snapshot",
            request.app.state.clock(),
            request.app.state.settings.snapshot_timeout_seconds,
        )
        remember(session, principal.key, operation, idempotency_key, {}, task.id)
        return public(task)


@router.get("/operations/{operation_id}", response_model=out.AgentTask)
async def operation(operation_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        task = await session.get(AgentTask, str(operation_id))
        if not task:
            raise HTTPException(404, "Operation not found")
        await owned(session, Isolate, task.isolate_id, principal)
        return public(task)
