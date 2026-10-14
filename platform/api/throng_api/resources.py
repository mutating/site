"""Resource catalog administration, computer inventory and owner-scoped reports."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, select

from throng_api import outputs as out
from throng_api.availability import capacity_history
from throng_api.computers import computer_for_user
from throng_api.models import Computer, ComputerOwnership, ComputerResource, ResourceType
from throng_api.resource_history import history, report_owner, summary
from throng_api.resource_ledger import add_resource, change_resource, computer_owner
from throng_api.schemas import (
    ComputerResourceCreate,
    ComputerResourceUpdate,
    PaginationOffset,
    ResourceTypeCreate,
    ResourceTypeUpdate,
)
from throng_api.security import (
    access_token_scheme,
    authenticate,
    owned_filter,
    system_administrator,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/api/v1", tags=["resources"], dependencies=[Depends(access_token_scheme)]
)


async def resource_type(session, type_id):
    row = await session.get(ResourceType, str(type_id))
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "Resource not found")
    return row


async def resource_for_user(session, principal, resource_id, admin=False, include_deleted=False):
    row = await session.get(ComputerResource, str(resource_id))
    if row is None or (row.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Resource not found")
    await computer_for_user(session, principal, row.computer_id, admin=admin)
    return row


def resource_output(row, kind):
    return {
        **public(row),
        "free": row.total - row.occupied,
        "resource_type": public(kind),
    }


@router.post("/resource-types", status_code=201, response_model=out.ResourceType)
async def create_resource_type(data: ResourceTypeCreate, request: Request):
    """Создать тип: личный токен системного администратора со scope manage."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await system_administrator(session, principal)
        row = ResourceType(**data.model_dump(), created_at=request.app.state.clock())
        session.add(row)
        await session.flush()
        return public(row)


@router.get("/resource-types", response_model=list[out.ResourceType])
async def list_resource_types(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    """Каталог доступен только системному администратору; ресурс компьютера содержит описание своего типа."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await system_administrator(session, principal)
        rows = await session.scalars(
            select(ResourceType)
            .where(ResourceType.deleted_at.is_(None))
            .order_by(ResourceType.code)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


@router.get("/resource-types/{type_id}", response_model=out.ResourceType)
async def get_resource_type(type_id: UUID, request: Request):
    """Получить активный тип: только системный администратор со scope read."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await system_administrator(session, principal)
        return public(await resource_type(session, type_id))


@router.patch("/resource-types/{type_id}", response_model=out.ResourceType)
async def update_resource_type(type_id: UUID, data: ResourceTypeUpdate, request: Request):
    """Изменить название и описание; код и единица измерения неизменяемы."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await system_administrator(session, principal)
        row = await resource_type(session, type_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None:
                raise HTTPException(422, f"{key} cannot be null")
            setattr(row, key, value)
        return public(row)


@router.delete("/resource-types/{type_id}", status_code=204)
async def delete_resource_type(type_id: UUID, request: Request):
    """Архивировать неиспользуемый пользовательский тип; встроенные типы защищены от удаления."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await system_administrator(session, principal)
        row = await resource_type(session, type_id)
        if row.builtin:
            raise HTTPException(409, "Built-in resource types cannot be deleted")
        if await session.scalar(
            select(ComputerResource.id)
            .where(
                ComputerResource.resource_type_id == row.id, ComputerResource.deleted_at.is_(None)
            )
            .limit(1)
        ):
            raise HTTPException(409, "Resource type is in use")
        row.deleted_at = request.app.state.clock()


@router.post(
    "/computers/{computer_id}/resources", status_code=201, response_model=out.ComputerResource
)
async def create_computer_resource(
    computer_id: UUID, data: ComputerResourceCreate, request: Request
):
    """Добавить ресурс или восстановить архивный с тем же UUID; нужны права управления компьютером."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        kind = await resource_type(session, data.resource_type_id)
        row = await add_resource(
            session, computer, kind.id, data.total, request.app.state.clock(), principal.actor_id
        )
        return resource_output(row, kind)


@router.get("/computers/{computer_id}/resources", response_model=list[out.ComputerResource])
async def list_computer_resources(
    computer_id: UUID,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
):
    """Текущие ресурсы компьютера с единицами, полным объёмом, резервом и свободным остатком."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await computer_for_user(session, principal, computer_id)
        rows = await session.execute(
            select(ComputerResource, ResourceType)
            .join(ResourceType, ResourceType.id == ComputerResource.resource_type_id)
            .join(Computer, Computer.id == ComputerResource.computer_id)
            .outerjoin(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
            .where(
                ComputerResource.computer_id == str(computer_id),
                ComputerResource.deleted_at.is_(None),
                # Authorize the inventory in the same statement that reads it:
                # a transfer can commit after the initial computer check.
                or_(
                    owned_filter(ComputerOwnership, principal),
                    and_(
                        ComputerOwnership.id.is_(None),
                        Computer.registered_by_user_id == principal.user_id,
                    ),
                ),
            )
            .order_by(ComputerResource.resource_type_id)
            .limit(limit)
            .offset(offset)
        )
        result = [resource_output(row, kind) for row, kind in rows]
        if not result:
            # Distinguish a valid empty page from access lost during a transfer.
            await computer_for_user(session, principal, computer_id)
        return result


@router.get("/computer-resources/{resource_id}", response_model=out.ComputerResource)
async def get_computer_resource(resource_id: UUID, request: Request):
    """Прочитать ресурс собственного компьютера или компьютера своей компании."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        row = await resource_for_user(session, principal, resource_id)
        return resource_output(row, await session.get(ResourceType, row.resource_type_id))


@router.patch("/computer-resources/{resource_id}", response_model=out.ComputerResource)
async def update_computer_resource(
    resource_id: UUID, data: ComputerResourceUpdate, request: Request
):
    """Изменить total; expected_version защищает от перезаписи параллельных изменений (409). Резерв занятой мощности не редактируется."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await resource_for_user(session, principal, resource_id, admin=True)
        if data.expected_version is not None and data.expected_version != row.version:
            raise HTTPException(409, "Resource version changed")
        await change_resource(
            session,
            row,
            request.app.state.clock(),
            "capacity_changed",
            total=data.total,
            actor_id=principal.actor_id,
        )
        return resource_output(row, await session.get(ResourceType, row.resource_type_id))


@router.delete("/computer-resources/{resource_id}", status_code=204)
async def delete_computer_resource(resource_id: UUID, request: Request):
    """Архивировать незанятый ресурс и записать нулевой вклад в историю; занятый ресурс даёт 409."""
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await resource_for_user(session, principal, resource_id, admin=True)
        await change_resource(
            session,
            row,
            request.app.state.clock(),
            "deleted",
            deleted=True,
            actor_id=principal.actor_id,
        )


@router.get("/computer-resources/{resource_id}/history", response_model=out.ResourceHistory)
async def computer_resource_history(
    resource_id: UUID,
    request: Request,
    from_time: datetime = Query(alias="from"),
    to_time: datetime = Query(alias="to"),
    limit: int = Query(500, ge=1, le=2000),
    after_revision: int = Query(0, ge=0),
    snapshot_revision: int | None = Query(None, ge=0),
):
    """История ресурса за [from,to), включая архивный, в границах текущего владельца компьютера. Чужое прошлое скрыто."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        row = await resource_for_user(session, principal, resource_id, include_deleted=True)
        owner = await computer_owner(session, row.computer_id)
        if owner == (None, None):
            raise HTTPException(404, "Resource not found")
        # A transfer may commit after resource_for_user. Authorize the exact,
        # immutable owner scope used below, never an earlier ownership lookup.
        await report_owner(session, principal, user_id=owner[0], company_id=owner[1])
        return await history(
            session,
            owner,
            from_time,
            to_time,
            resource_id=row.id,
            limit=limit,
            after_revision=after_revision,
            snapshot_revision=snapshot_revision,
        )


@router.get("/users/{user_id}/resources/summary", response_model=out.ResourceSummary)
async def user_resource_summary(user_id: UUID, request: Request, only_available: bool = False):
    """Суммы личных ресурсов пользователя; only_available учитывает только включённые компьютеры с живым lease."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, user_id=user_id)
        return await summary(session, owner, request.app.state.clock(), only_available)


@router.get("/companies/{company_id}/resources/summary", response_model=out.ResourceSummary)
async def company_resource_summary(
    company_id: UUID, request: Request, only_available: bool = False
):
    """Суммы ресурсов компании без личных компьютеров её сотрудников; доступны членам и токену компании."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, company_id=company_id)
        return await summary(session, owner, request.app.state.clock(), only_available)


@router.get("/users/{user_id}/resources/history", response_model=out.ResourceHistory)
async def user_resource_history(
    user_id: UUID,
    request: Request,
    from_time: datetime = Query(alias="from"),
    to_time: datetime = Query(alias="to"),
    resource_type_id: UUID | None = None,
    limit: int = Query(500, ge=1, le=2000),
    after_revision: int = Query(0, ge=0),
    snapshot_revision: int | None = Query(None, ge=0),
):
    """Личный пул за [from,to): начальное состояние и точки целых транзакций. Для следующей страницы повторите границы/фильтр, snapshot_revision и next_after_revision как after_revision."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, user_id=user_id)
        return await history(
            session,
            owner,
            from_time,
            to_time,
            resource_type_id=resource_type_id,
            limit=limit,
            after_revision=after_revision,
            snapshot_revision=snapshot_revision,
        )


@router.get("/companies/{company_id}/resources/history", response_model=out.ResourceHistory)
async def company_resource_history(
    company_id: UUID,
    request: Request,
    from_time: datetime = Query(alias="from"),
    to_time: datetime = Query(alias="to"),
    resource_type_id: UUID | None = None,
    limit: int = Query(500, ge=1, le=2000),
    after_revision: int = Query(0, ge=0),
    snapshot_revision: int | None = Query(None, ge=0),
):
    """История пула компании с владельцами на момент изменения. Изменения ровно в from входят в initial, ровно в to исключаются; snapshot_revision фиксирует страницы отчёта."""
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, company_id=company_id)
        return await history(
            session,
            owner,
            from_time,
            to_time,
            resource_type_id=resource_type_id,
            limit=limit,
            after_revision=after_revision,
            snapshot_revision=snapshot_revision,
        )


@router.get("/users/{user_id}/resources/availability-history", response_model=out.CapacityHistory)
async def user_capacity_history(
    user_id: UUID,
    request: Request,
    from_time: datetime = Query(alias="from"),
    to_time: datetime = Query(alias="to"),
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, user_id=user_id)
        return await capacity_history(session, owner, from_time, to_time, request.app.state.clock())


@router.get(
    "/companies/{company_id}/resources/availability-history", response_model=out.CapacityHistory
)
async def company_capacity_history(
    company_id: UUID,
    request: Request,
    from_time: datetime = Query(alias="from"),
    to_time: datetime = Query(alias="to"),
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        owner = await report_owner(session, principal, company_id=company_id)
        return await capacity_history(session, owner, from_time, to_time, request.app.state.clock())
