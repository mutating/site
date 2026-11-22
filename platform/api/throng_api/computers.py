from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, delete, func, literal, or_, select
from sqlalchemy.orm import joinedload, raiseload

from throng_api import outputs as out
from throng_api.accounts import saved, validate_owner
from throng_api.availability import (
    active_computer_session,
    active_computer_session_filter,
    available_computer_filter,
    sync_capacity,
)
from throng_api.database import (
    COMPUTER_POOL_CHECK_REVISION_HEADER,
    company_query_id,
    current_revision,
)
from throng_api.execution import invalidate_computer, remember, replay
from throng_api.models import (
    CPU_RESOURCE_ID,
    MEMORY_RESOURCE_ID,
    AgentCredential,
    Company,
    Computer,
    ComputerOwnership,
    Membership,
)
from throng_api.permissions import effective_permissions
from throng_api.resource_history import report_owner
from throng_api.resource_ledger import (
    add_resource,
    change_resource,
    resource_time,
    transfer_resources,
)
from throng_api.schemas import (
    ComputerCreate,
    ComputerOwnerCreate,
    ComputerPoolUpdate,
    ComputerTransferRecovery,
    ComputerUpdate,
    OwnerInput,
    PaginationOffset,
)
from throng_api.security import (
    ORGANIZATION_MEMBERSHIP_HEADER,
    access_token_scheme,
    authenticate,
    authenticated_snapshot,
    company_access,
    digest,
    organization_membership_headers,
    owned,
    owned_filter,
    owner_access,
    owner_values,
    personal,
    secret,
)
from throng_api.serialization import public
from throng_api.transport import ProtocolError

router = APIRouter(prefix="/v1", tags=["computers"], dependencies=[Depends(access_token_scheme)])
public_router = APIRouter(prefix="/v1", tags=["computers"])
COMPUTER_READ_ALLOWED_HEADER = "X-Computer-Read-Allowed"
COMPUTER_POOL_ASSIGNED_HEADER = "X-Computer-Pool-Assigned"


@public_router.get("/connected-computers/browser", response_class=RedirectResponse, status_code=303)
async def connected_computers_browser(request: Request):
    platform = request.app.state.settings.platform_origin
    origin = str(platform).rstrip("/") if platform else ""
    return RedirectResponse(
        f"{origin}/compute/computers", status_code=303, headers={"Cache-Control": "no-store"}
    )


def computer_view(computer, now):
    return {**public(computer), "session_active": active_computer_session(computer, now)}


def connected_to_account(user_id):
    return (
        select(Computer, ComputerOwnership)
        .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
        .where(
            Computer.connected_user_id == user_id,
            or_(ComputerOwnership.user_id.is_(None), ComputerOwnership.user_id == user_id),
        )
    )


def legacy_transfer_pending(computer, credential):
    return bool(
        computer.certificate_sha256 and (credential is None or computer.legacy_transfer_pending)
    )


@router.get("/connected-computers", response_model=out.ConnectedComputers)
async def list_connected_computers(
    request: Request,
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
    computer_id: UUID | None = None,
):
    async with authenticated_snapshot(request) as (session, principal):
        user_id = personal(principal)
        now = request.app.state.clock()
        inventory = connected_to_account(user_id)
        if computer_id is not None:
            inventory = inventory.where(Computer.id == str(computer_id))
        total, online = (
            await session.execute(
                inventory.with_only_columns(
                    func.count(),
                    func.count().filter(active_computer_session_filter(now)),
                    maintain_column_froms=True,
                )
            )
        ).one()
        rows = await session.execute(
            inventory.add_columns(
                Company.name,
                Membership.id,
                Membership.role,
                Membership.permissions,
                AgentCredential,
            )
            .outerjoin(
                Membership,
                and_(
                    Membership.company_id == ComputerOwnership.company_id,
                    Membership.user_id == user_id,
                ),
            )
            .outerjoin(Company, Company.id == Membership.company_id)
            .outerjoin(AgentCredential, AgentCredential.computer_id == Computer.id)
            .options(raiseload(Computer.resources))
            .order_by(Computer.created_at, Computer.id)
            .limit(limit)
            .offset(offset)
        )
        items = []
        for computer, owner, company_name, membership_id, role, permissions, credential in rows:
            pool = None
            can_move = False
            if owner.user_id == user_id:
                pool = {"company_id": None, "name": None}
                can_move = True
            elif company_name is not None:
                pool = {"company_id": owner.company_id, "name": company_name}
                can_move = "resources.manage" in effective_permissions(role, permissions)
            pending = legacy_transfer_pending(computer, credential)
            items.append(
                {
                    "id": computer.id,
                    "name": computer.name,
                    "os": computer.os,
                    "architecture": computer.architecture,
                    "runtime": computer.runtime,
                    "online": active_computer_session(computer, now),
                    "pool": pool,
                    "pool_membership_id": membership_id,
                    "pool_revision": owner.revision,
                    "can_move": can_move and "manage" in principal.scopes and not pending,
                    "transfer_pending": pending,
                }
            )
        response.headers[COMPUTER_POOL_CHECK_REVISION_HEADER] = str(await current_revision(session))
        return {"items": items, "total": total, "online": online}


@router.get("/connected-computers/{computer_id}/position", response_model=int | None)
async def connected_computer_position(computer_id: UUID, request: Request):
    """Return the zero-based list position of the account's computer, or null if absent."""
    async with authenticated_snapshot(request) as (session, principal):
        inventory = connected_to_account(personal(principal))
        identity = str(computer_id)
        created_at = await session.scalar(
            inventory.with_only_columns(Computer.created_at).where(Computer.id == identity)
        )
        if created_at is None:
            return None
        return await session.scalar(
            inventory.with_only_columns(func.count(), maintain_column_froms=True).where(
                or_(
                    Computer.created_at < created_at,
                    and_(Computer.created_at == created_at, Computer.id < identity),
                )
            )
        )


@router.get(
    "/connected-computers/{computer_id}/recovery",
    response_model=bool | out.ComputerRecoverySnapshot,
)
async def can_recover_connected_computer(
    computer_id: UUID, request: Request, snapshot: bool = False
):
    async with authenticated_snapshot(request, "manage") as (session, principal):
        user_id = personal(principal)
        revision = await session.scalar(
            select(ComputerOwnership.revision)
            .select_from(Computer)
            .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
            .where(
                Computer.id == str(computer_id),
                Computer.certificate_sha256.is_not(None),
                or_(
                    Computer.connected_user_id == user_id,
                    Computer.legacy_transfer_user_id == user_id,
                ),
                owned_filter(ComputerOwnership, principal),
            )
        )
        return {"revision": revision} if snapshot else revision is not None


@router.post(
    "/connected-computers/{computer_id}/recovery",
    status_code=201,
    response_model=out.RecoveredComputerCredential,
)
async def recover_connected_computer_key(
    computer_id: UUID,
    data: ComputerTransferRecovery,
    request: Request,
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user_id = check_expected_account(principal, data.expected_user_id)
        if not principal.password_login:
            raise HTTPException(403, "The account must sign in to recover a transfer")
        row = (
            await session.execute(
                select(Computer, ComputerOwnership)
                .join(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
                .where(
                    Computer.id == str(computer_id),
                    Computer.certificate_sha256.is_not(None),
                    Computer.legacy_transfer_pending.is_(True),
                    Computer.legacy_transfer_user_id == user_id,
                    owned_filter(ComputerOwnership, principal),
                )
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(404, "Pending computer transfer not found")
        computer, owner = row
        expected_user = data.expected_user_id if data.expected_company_id is None else None
        check_expected_owner(owner, data.expected_company_id, expected_user, data.expected_revision)
        replacement = await replace_computer_credential(
            session, principal, computer, owner, request.app.state.clock()
        )
        return {**replacement, "ownership": public(owner)}


POOL_CHANGE = "move_connected_computer"
CANCELLED_POOL_CHANGE = str(UUID(int=0))


async def computer_read_headers(session, *, allowed):
    return {
        # A permission or membership change has its own write revision even
        # when the computer's ownership has stayed in the same pool.
        COMPUTER_POOL_CHECK_REVISION_HEADER: str(await current_revision(session)),
        COMPUTER_READ_ALLOWED_HEADER: "true" if allowed else "false",
    }


def pool_change_payload(computer_id, data):
    payload = data.model_dump(mode="json")
    if data.expected_revision is None:
        payload.pop("expected_revision")
    return {"computer_id": str(computer_id), **payload}


def check_expected_account(principal, expected_user_id):
    user_id = personal(principal)
    if expected_user_id is not None and str(expected_user_id) != user_id:
        raise HTTPException(403, "The operation belongs to another account")
    return user_id


@router.post("/connected-computers/{computer_id}/pool/reconcile", response_model=bool)
async def reconcile_pool_change(
    computer_id: UUID,
    data: ComputerPoolUpdate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(),
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        check_expected_account(principal, data.expected_user_id)
        payload = pool_change_payload(computer_id, data)
        previous = await replay(session, principal.key, POOL_CHANGE, idempotency_key, payload)
        if previous is None:
            # Serialize with the original PATCH. If it has yet to arrive, it
            # must no longer apply after the browser confirms the old pool.
            remember(
                session, principal.key, POOL_CHANGE, idempotency_key, payload, CANCELLED_POOL_CHANGE
            )
        if previous is None or previous == CANCELLED_POOL_CHANGE:
            response.headers[COMPUTER_POOL_CHECK_REVISION_HEADER] = str(
                await current_revision(session)
            )
            return False
        # Resolve access against the same rules as the computer detail view.
        # An account can still read a computer after it leaves its connected list.
        if "read" in principal.scopes:
            try:
                await computer_for_user(
                    session,
                    replace(principal, permission="resources.read"),
                    computer_id,
                    response=response,
                )
                return True
            except HTTPException as error:
                if error.headers:
                    response.headers.update(error.headers)
                    return True
        response.headers.update(await computer_read_headers(session, allowed=False))
        return True


@router.patch("/connected-computers/{computer_id}/pool", response_model=out.ConnectedPool)
async def move_connected_computer(
    computer_id: UUID,
    data: ComputerPoolUpdate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(None),
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        try:
            return await apply_connected_pool_move(
                session, principal, computer_id, data, request, response, idempotency_key
            )
        except HTTPException as error:
            # The write lock reserves the next revision. A rejected transfer
            # rolls it back, so the check observed the preceding revision.
            revision = await current_revision(session) - 1
            error.headers = {
                **(error.headers or {}),
                COMPUTER_POOL_CHECK_REVISION_HEADER: str(revision),
            }
            raise


async def apply_connected_pool_move(
    session, principal, computer_id, data, request, response, idempotency_key
):
    user_id = check_expected_account(principal, data.expected_user_id)
    payload = pool_change_payload(computer_id, data)
    previous = None
    if idempotency_key is not None:
        previous = await replay(session, principal.key, POOL_CHANGE, idempotency_key, payload)
        if previous == CANCELLED_POOL_CHANGE:
            raise HTTPException(409, "Computer pool change was cancelled")
    row = (
        await session.execute(connected_to_account(user_id).where(Computer.id == str(computer_id)))
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Connected computer not found")
    computer, owner = row
    member = await owner_access(session, principal, owner, admin=True)
    before = (owner.user_id, owner.company_id)
    after = before
    if previous is None:
        check_expected_owner(owner, None, None, data.expected_revision)
        if data.company_id is None:
            member = None
            after = (user_id, None)
        else:
            member = await company_access(session, principal, data.company_id, admin=True)
            after = (None, member.company_id)
    if before != after:
        current = UUID(owner.company_id) if owner.company_id is not None else None
        if current != data.expected_company_id:
            raise HTTPException(409, "Computer pool changed")
        credential = await session.scalar(
            select(AgentCredential).where(AgentCredential.computer_id == computer.id)
        )
        if legacy_transfer_pending(computer, credential):
            raise HTTPException(409, "Computer has an unfinished native transfer")
        if computer.reserved_disk or any(resource.occupied for resource in computer.resources):
            raise HTTPException(409, "Computer has reserved resources")
        owner.user_id, owner.company_id = after
        owner.revision = await current_revision(session)
        await session.flush()
        now = request.app.state.clock()
        # The agent still belongs to this account and holds the same certified
        # device key. Update the owner and capacity ledger in one transaction.
        await transfer_resources(session, computer, before, after, now, principal.actor_id)
        await sync_capacity(session, computer, now)
    if idempotency_key is not None and previous is None:
        remember(session, principal.key, POOL_CHANGE, idempotency_key, payload, computer.id)
    company = await session.get(Company, owner.company_id) if owner.company_id else None
    response.headers.update(organization_membership_headers(principal, member))
    response.headers[COMPUTER_POOL_ASSIGNED_HEADER] = "true"
    readable = "read" in principal.scopes and (
        member is None or "resources.read" in effective_permissions(member.role, member.permissions)
    )
    response.headers.update(await computer_read_headers(session, allowed=readable))
    return {"company_id": owner.company_id, "name": company.name if company else None}


async def computer_for_user(session, principal, computer_id, admin=False, *, response=None):
    computer = await session.get(Computer, str(computer_id))
    if not computer:
        raise HTTPException(404, "Computer not found")
    owner = await session.scalar(
        select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
    )
    member = None
    if owner:
        try:
            member = await owner_access(
                session, principal, owner, admin=admin, include_membership_id=response is not None
            )
        except HTTPException as error:
            if error.headers and ORGANIZATION_MEMBERSHIP_HEADER in error.headers:
                error.headers.update(await computer_read_headers(session, allowed=False))
            raise
    elif principal.user_id != computer.registered_by_user_id:
        raise HTTPException(404, "Computer not found")
    if response is not None:
        response.headers.update(organization_membership_headers(principal, member))
        response.headers.update(await computer_read_headers(session, allowed=True))
        response.headers[COMPUTER_POOL_ASSIGNED_HEADER] = "true" if owner else "false"
    return computer


async def disconnect_idle(session, computer, now):
    if any(row.occupied for row in computer.resources) or computer.reserved_disk:
        raise HTTPException(
            409, "Destroy assigned isolates before changing computer ownership or credentials"
        )
    computer.online = False
    await sync_capacity(session, computer, await resource_time(session, now))
    computer.session_id = None
    computer.lease_expires_at = None
    await session.execute(delete(AgentCredential).where(AgentCredential.computer_id == computer.id))


@router.post("/computers", status_code=201, response_model=out.RegisteredComputer)
async def create_computer(data: ComputerCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        if data.runtime == "powercell-ubuntu-arm64-v1":
            raise HTTPException(422, "Computers require attested enrollment")
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
        now = request.app.state.clock()
        for type_id, total in (
            (CPU_RESOURCE_ID, data.cpu_millis),
            (MEMORY_RESOURCE_ID, data.memory_bytes),
        ):
            await add_resource(session, computer, type_id, total, now, principal.actor_id)
        return {**computer_view(computer, now), "ownership": public(ownership)}


async def computer_page(session, eligible, now, *, limit, offset=0):
    page = (
        eligible.order_by(ComputerOwnership.computer_id)
        .limit(literal(limit, literal_execute=True))
        .offset(offset)
        .subquery()
    )
    statement = (
        select(Computer)
        .join(page, page.c.computer_id == Computer.id)
        .options(joinedload(Computer.resources))
        .order_by(page.c.computer_id)
    )
    rows = await session.scalars(statement)
    return [computer_view(row, now) for row in rows.unique()]


@router.get("/computers", response_model=list[out.Computer])
async def list_computers(
    request: Request,
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
    user_id: UUID | None = None,
    company_id: UUID | None = None,
    only_available: bool = False,
    computer_id: UUID | None = None,
):
    async with authenticated_snapshot(request) as (session, principal):
        if user_id is not None and company_id is not None:
            raise HTTPException(422, "Choose one owner")
        if company_id is not None:
            membership = await company_access(
                session, principal, company_id, include_membership_id=True
            )
            response.headers.update(organization_membership_headers(principal, membership))
            ownership = owned_filter(
                ComputerOwnership, principal, company_id=company_query_id(session, company_id)
            )
        elif user_id is not None:
            owner_user, _ = await report_owner(session, principal, user_id=user_id)
            ownership = ComputerOwnership.user_id == owner_user
        else:
            ownership = owned_filter(ComputerOwnership, principal)
        now = request.app.state.clock()
        eligible = select(ComputerOwnership.computer_id).where(ownership)
        if computer_id is not None:
            eligible = eligible.where(ComputerOwnership.computer_id == str(computer_id))
        if only_available:
            eligible = eligible.join(Computer, Computer.id == ComputerOwnership.computer_id).where(
                available_computer_filter(now)
            )

        rows = await computer_page(session, eligible, now, limit=limit, offset=offset)
        # A foreign key and a unique computer_id make ownership rows sufficient
        # for exact counts unless availability has to inspect the computer.
        count = select(func.count()).select_from(eligible.subquery())
        response.headers["X-Total-Count"] = str(await session.scalar(count))
        response.headers.update(await computer_read_headers(session, allowed=True))
        return rows


@router.get("/computers/{computer_id}", response_model=out.Computer)
async def get_computer(computer_id: UUID, request: Request, response: Response):
    async with authenticated_snapshot(request) as (session, principal):
        computer = await computer_for_user(session, principal, computer_id, response=response)
        return computer_view(computer, request.app.state.clock())


@router.patch("/computers/{computer_id}", response_model=out.Computer)
async def update_computer(computer_id: UUID, data: ComputerUpdate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        now = request.app.state.clock()
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
                        now,
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
                        now,
                        principal.actor_id,
                    )
            else:
                setattr(computer, key, value)
        # The database also enforces that capacity cannot fall below reservations.
        await session.flush()
        await sync_capacity(session, computer, now)
        return computer_view(computer, now)


@router.delete("/computers/{computer_id}", status_code=204)
async def delete_computer(computer_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        now = request.app.state.clock()
        await disconnect_idle(session, computer, now)
        for row in computer.resources:
            if row.deleted_at is None:
                await change_resource(
                    session,
                    row,
                    now,
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
async def rotate_credential(
    computer_id: UUID,
    request: Request,
    expected_company_id: UUID | None = None,
    expected_user_id: UUID | None = None,
    expected_revision: int | None = Query(default=None, ge=0),
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, computer_id, admin=True)
        owner = await session.scalar(
            select(ComputerOwnership).where(ComputerOwnership.computer_id == computer.id)
        )
        if owner is None:
            raise HTTPException(409, "Computer needs an owner")
        check_expected_owner(owner, expected_company_id, expected_user_id, expected_revision)
        return await replace_computer_credential(
            session, principal, computer, owner, request.app.state.clock()
        )


async def replace_computer_credential(session, principal, computer, owner, now):
    await invalidate_computer(session, computer, now, "credential_rotated")
    computer.session_id = None
    computer.lease_expires_at = None
    if computer.certificate_sha256:
        computer.legacy_transfer_pending = True
        owner.revision = await current_revision(session)
    await session.execute(delete(AgentCredential).where(AgentCredential.computer_id == computer.id))
    pending = (
        principal.user_id
        if principal.password_login and computer.legacy_transfer_user_id == principal.user_id
        else None
    )
    raw = secret("agt_")
    session.add(
        AgentCredential(
            computer_id=computer.id,
            token_hash=digest(raw),
            pending_connected_user_id=pending,
        )
    )
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
                computer_id=computer.id,
                revision=await current_revision(session),
                **await validate_owner(session, principal, data),
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
async def update_owner(
    ownership_id: UUID,
    data: OwnerInput,
    request: Request,
    expected_company_id: UUID | None = None,
    expected_user_id: UUID | None = None,
    expected_revision: int | None = Query(default=None, ge=0),
):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await owned(session, ComputerOwnership, ownership_id, principal, admin=True)
        check_expected_owner(row, expected_company_id, expected_user_id, expected_revision)
        values = await validate_owner(session, principal, data)
        computer = await session.get(Computer, row.computer_id)
        now = request.app.state.clock()
        await disconnect_idle(session, computer, now)
        computer.legacy_transfer_pending = bool(computer.certificate_sha256)
        before = (row.user_id, row.company_id)
        for key, value in values.items():
            setattr(row, key, value)
        row.revision = await current_revision(session)
        if before != (row.user_id, row.company_id):
            computer.legacy_transfer_user_id = (
                principal.user_id
                if computer.certificate_sha256 and principal.password_login
                else None
            )
        await session.flush()
        await transfer_resources(
            session,
            computer,
            before,
            (row.user_id, row.company_id),
            now,
            principal.actor_id,
        )
        return public(row)


def check_expected_owner(owner, company_id, user_id, revision):
    if company_id is not None and user_id is not None:
        raise HTTPException(422, "Specify a single expected computer owner")
    if (
        (company_id is not None and owner.company_id != str(company_id))
        or (user_id is not None and owner.user_id != str(user_id))
        or (revision is not None and owner.revision != revision)
    ):
        raise ProtocolError("computer_pool_changed", "Computer pool changed")


@router.delete("/computer-ownerships/{ownership_id}", status_code=204)
async def delete_owner(ownership_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await owned(session, ComputerOwnership, ownership_id, principal, admin=True)
        computer = await session.get(Computer, row.computer_id)
        now = request.app.state.clock()
        await disconnect_idle(session, computer, now)
        computer.connected_user_id = None
        computer.legacy_transfer_user_id = None
        computer.legacy_transfer_pending = False
        await transfer_resources(
            session,
            computer,
            (row.user_id, row.company_id),
            (None, None),
            now,
            principal.actor_id,
        )
        await session.delete(row)
