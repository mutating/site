from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload

from throng_api import outputs as out
from throng_api.accounts import saved, validate_owner
from throng_api.availability import sync_capacity
from throng_api.execution import invalidate_computer
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentCredential,
    Computer,
    ComputerOwnership,
)
from throng_api.resource_history import report_owner
from throng_api.resource_ledger import add_resource, change_resource, transfer_resources
from throng_api.schemas import (
    ComputerCreate,
    ComputerOwnerCreate,
    ComputerUpdate,
    OwnerInput,
    PaginationOffset,
)
from throng_api.security import (
    access_token_scheme,
    authenticate,
    digest,
    owned,
    owned_filter,
    owner_access,
    owner_values,
    secret,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/api/v1", tags=["computers"], dependencies=[Depends(access_token_scheme)]
)


async def computer_for_user(session, principal, computer_id, admin=False):
    computer = await session.get(Computer, str(computer_id))
    if not computer:
        raise HTTPException(404, "Computer not found")
    owner = await session.scalar(
        select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
    )
    if owner:
        await owner_access(session, principal, owner, admin=admin)
    elif principal.user_id != computer.registered_by_user_id:
        raise HTTPException(404, "Computer not found")
    return computer


async def disconnect_idle(session, computer, now):
    if any(row.occupied for row in computer.resources) or computer.reserved_disk:
        raise HTTPException(
            409, "Destroy assigned isolates before changing computer ownership or credentials"
        )
    computer.online = False
    await sync_capacity(session, computer, now)
    computer.session_id = None
    computer.lease_expires_at = None
    await session.execute(delete(AgentCredential).where(AgentCredential.computer_id == computer.id))


@router.post("/computers", status_code=201, response_model=out.RegisteredComputer)
async def create_computer(data: ComputerCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        if data.runtime == "powercell-ubuntu-arm64-v1":
            raise HTTPException(422, "Power Cell computers require attested enrollment")
        owner = await owner_values(session, principal, data.company_id, admin=True)
        computer = Computer(
            **data.model_dump(exclude={"company_id", "cpu_millis", "memory_bytes"}),
            registered_by_user_id=principal.actor_id,
            resources=[],
        )
        session.add(computer)
        await session.flush()
        ownership = ComputerOwnership(computer_id=computer.id, **owner)
        session.add(ownership)
        await session.flush()
        for type_id, total in (
            (CPU_RESOURCE_ID, data.cpu_millis),
            (MEMORY_RESOURCE_ID, data.memory_bytes),
        ):
            await add_resource(
                session, computer, type_id, total, request.app.state.clock(), principal.actor_id
            )
        return {**public(computer), "ownership": public(ownership)}


@router.get("/computers", response_model=list[out.Computer])
async def list_computers(
    request: Request,
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
    user_id: UUID | None = None,
    company_id: UUID | None = None,
    only_available: bool = False,
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        if user_id is not None and company_id is not None:
            raise HTTPException(422, "Choose one owner")
        conditions = [owned_filter(ComputerOwnership, principal)]
        if user_id is not None or company_id is not None:
            owner = await report_owner(session, principal, user_id=user_id, company_id=company_id)
            conditions.append(
                ComputerOwnership.user_id == owner[0]
                if owner[0]
                else ComputerOwnership.company_id == owner[1]
            )
        if only_available:
            conditions.extend(
                [
                    Computer.enabled.is_(True),
                    Computer.accepting_work.is_(True),
                    Computer.online.is_(True),
                    Computer.session_id.is_not(None),
                    Computer.lease_expires_at > request.app.state.clock(),
                ]
            )
        statement = (
            select(Computer)
            .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
            .where(*conditions)
            .options(joinedload(Computer.resources))
        )
        rows = await session.scalars(statement.order_by(Computer.id).limit(limit).offset(offset))
        response.headers["X-Total-Count"] = str(
            await session.scalar(
                select(func.count())
                .select_from(Computer)
                .join(ComputerOwnership)
                .where(*conditions)
            )
        )
        return [public(row) for row in rows.unique()]


@router.get("/computers/{computer_id}", response_model=out.Computer)
async def get_computer(computer_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(
            await computer_for_user(session, await authenticate(session, request), computer_id)
        )


@router.patch("/computers/{computer_id}", response_model=out.Computer)
async def update_computer(computer_id: UUID, data: ComputerUpdate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None and key != "name":
                raise HTTPException(422, f"{key} cannot be null")
            if key in {"cpu_millis", "memory_bytes"}:
                type_id = CPU_RESOURCE_ID if key == "cpu_millis" else MEMORY_RESOURCE_ID
                row = computer.resource(type_id)
                if row:
                    await change_resource(
                        session,
                        row,
                        request.app.state.clock(),
                        "capacity_changed",
                        total=value,
                        actor_id=principal.actor_id,
                    )
                else:
                    await add_resource(
                        session,
                        computer,
                        type_id,
                        value,
                        request.app.state.clock(),
                        principal.actor_id,
                    )
            else:
                setattr(computer, key, value)
        # The database also enforces that capacity cannot fall below reservations.
        await session.flush()
        await sync_capacity(session, computer, request.app.state.clock())
        return public(computer)


@router.delete("/computers/{computer_id}", status_code=204)
async def delete_computer(computer_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        await disconnect_idle(session, computer, request.app.state.clock())
        for row in computer.resources:
            if row.deleted_at is None:
                await change_resource(
                    session,
                    row,
                    request.app.state.clock(),
                    "computer_deleted",
                    deleted=True,
                    actor_id=principal.actor_id,
                )
        await session.execute(
            delete(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
        )
        await session.delete(computer)


@router.post(
    "/computers/{computer_id}/credentials", status_code=201, response_model=out.AgentCredential
)
async def rotate_credential(computer_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        if not await session.scalar(
            select(ComputerOwnership.id).where(ComputerOwnership.computer_id == computer.id)
        ):
            raise HTTPException(409, "Computer needs an owner")
        await invalidate_computer(
            session, computer, request.app.state.clock(), "credential_rotated"
        )
        computer.session_id = None
        computer.lease_expires_at = None
        await session.execute(
            delete(AgentCredential).where(AgentCredential.computer_id == computer.id)
        )
        raw = secret("agt_")
        session.add(AgentCredential(computer_id=computer.id, token_hash=digest(raw)))
        return {"computer_id": computer.id, "token": raw}


@router.post("/computer-ownerships", status_code=201, response_model=out.ComputerOwnership)
async def create_owner(data: ComputerOwnerCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, data.computer_id, admin=True)
        if await session.scalar(
            select(ComputerOwnership.id).where(ComputerOwnership.computer_id == computer.id)
        ):
            raise HTTPException(409, "Computer already has an owner; update the ownership")
        ownership = await saved(
            session,
            ComputerOwnership(
                computer_id=computer.id, **await validate_owner(session, principal, data)
            ),
        )
        await transfer_resources(
            session,
            computer,
            (None, None),
            (ownership["user_id"], ownership["company_id"]),
            request.app.state.clock(),
            principal.actor_id,
        )
        return ownership


@router.get("/computer-ownerships", response_model=list[out.ComputerOwnership])
async def list_owners(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        rows = await session.scalars(
            select(ComputerOwnership)
            .where(owned_filter(ComputerOwnership, principal))
            .order_by(ComputerOwnership.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


@router.get("/computer-ownerships/{ownership_id}", response_model=out.ComputerOwnership)
async def get_owner(ownership_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(
            await owned(
                session, ComputerOwnership, ownership_id, await authenticate(session, request)
            )
        )


@router.patch("/computer-ownerships/{ownership_id}", response_model=out.ComputerOwnership)
async def update_owner(ownership_id: UUID, data: OwnerInput, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await owned(session, ComputerOwnership, ownership_id, principal, admin=True)
        values = await validate_owner(session, principal, data)
        computer = await session.get(Computer, row.computer_id)
        await disconnect_idle(session, computer, request.app.state.clock())
        before = (row.user_id, row.company_id)
        for key, value in values.items():
            setattr(row, key, value)
        await session.flush()
        await transfer_resources(
            session,
            computer,
            before,
            (row.user_id, row.company_id),
            request.app.state.clock(),
            principal.actor_id,
        )
        return public(row)


@router.delete("/computer-ownerships/{ownership_id}", status_code=204)
async def delete_owner(ownership_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await owned(session, ComputerOwnership, ownership_id, principal, admin=True)
        computer = await session.get(Computer, row.computer_id)
        await disconnect_idle(session, computer, request.app.state.clock())
        await transfer_resources(
            session,
            computer,
            (row.user_id, row.company_id),
            (None, None),
            request.app.state.clock(),
            principal.actor_id,
        )
        await session.delete(row)
