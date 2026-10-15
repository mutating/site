from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, or_, select, update

from throng_api import outputs as out
from throng_api.models import (
    AccessToken,
    Company,
    CompanyInvitation,
    Membership,
    TokenOwnership,
    User,
)
from throng_api.permissions import PERMISSIONS, effective_permissions
from throng_api.schemas import (
    CompanyInput,
    Login,
    MembershipCreate,
    MembershipUpdate,
    OwnerInput,
    PaginationOffset,
    TokenCreate,
    TokenUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from throng_api.security import (
    DUMMY_PASSWORD_HASH,
    SCOPES,
    access_token_scheme,
    authenticate,
    company_access,
    hash_password,
    issue_token,
    membership_role,
    owned,
    owned_filter,
    owner_access,
    personal,
    verify_password,
)
from throng_api.serialization import public
from throng_api.sharing import grant_allowed, member_view, resolve_user

public_router = APIRouter(prefix="/v1", tags=["accounts"])
router = APIRouter(prefix="/v1", tags=["accounts"], dependencies=[Depends(access_token_scheme)])


async def saved(session, row):
    session.add(row)
    await session.flush()
    return public(row)


async def validate_owner(session, principal, data):
    # Explicit owners cannot be used to escape the caller's permissions.
    await owner_access(session, principal, data, admin=True)
    return {
        "user_id": str(data.user_id) if data.user_id else None,
        "company_id": str(data.company_id) if data.company_id else None,
    }


@public_router.post("/users", status_code=201, response_model=UserOut)
async def create_user(data: UserCreate, request: Request):
    if not request.app.state.settings.allow_registration:
        raise HTTPException(403, "Registration is disabled")
    values = data.model_dump(exclude={"password"})
    values["password_hash"] = await hash_password(data.password)
    async with request.app.state.db.write() as session:
        return await saved(session, User(**values))


async def issue_login(data: Login, request: Request):
    async with request.app.state.db.read() as session:
        identifier = data.login.casefold()
        user = await session.scalar(
            select(User).where(or_(User.email == identifier, User.nickname == identifier))
        )
        encoded = user.password_hash if user else DUMMY_PASSWORD_HASH
        user_id = user.id if user else None
    valid = await verify_password(encoded, data.password)
    if not valid or not user_id:
        raise HTTPException(401, "Invalid email or password")
    async with request.app.state.db.write() as session:
        user = await session.get(User, user_id)
        if not user or user.password_hash != encoded:
            raise HTTPException(401, "Credentials changed; retry login")
        owner = TokenOwnership(user_id=user.id)
        session.add(owner)
        await session.flush()
        token, raw = await issue_token(
            session,
            owner,
            user.id,
            "login",
            SCOPES,
            request.app.state.clock()
            + timedelta(seconds=request.app.state.settings.login_token_seconds),
        )
        return (
            {"access_token": raw, "token_type": "bearer", "token": public(token)},
            UserOut.model_validate(user),
        )


@public_router.post("/auth/login", response_model=out.Login)
async def login(data: Login, request: Request):
    issued, _ = await issue_login(data, request)
    return issued


@router.get("/users", response_model=list[UserOut])
async def list_users(request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        user = await session.get(User, personal(principal))
        if user is None:
            raise HTTPException(401, "Invalid token issuer")
        return [user]


@router.get("/users/me", response_model=UserOut)
async def me(request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        user = await session.get(User, personal(principal))
        if user is None:
            raise HTTPException(401, "Invalid token issuer")
        return user


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        if personal(principal) != str(user_id):
            raise HTTPException(404, "User not found")
        user = await session.get(User, str(user_id))
        if user is None:
            raise HTTPException(401, "Invalid token issuer")
        return user


async def authorize_user_update(session, request, user_id, password_change):
    principal = await authenticate(session, request, "manage")
    if personal(principal) != str(user_id):
        raise HTTPException(404, "User not found")
    if password_change and not SCOPES <= principal.scopes:
        raise HTTPException(403, "Changing a password requires a token with all scopes")


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, data: UserUpdate, request: Request):
    password_change = data.password is not None
    password_hash = None
    if password_change:
        async with request.app.state.db.read() as session:
            await authorize_user_update(session, request, user_id, password_change)
        # Reject unauthorized work before Argon2, without holding the write lock
        # while hashing. Recheck credentials before committing the new password.
        password_hash = await hash_password(data.password)
    async with request.app.state.db.write() as session:
        await authorize_user_update(session, request, user_id, password_change)
        user = await session.get(User, str(user_id))
        for key, value in data.model_dump(exclude={"password"}, exclude_none=True).items():
            setattr(user, key, value)
        if "nickname" in data.model_fields_set and data.nickname is None:
            user.nickname = None
        if password_hash:
            user.password_hash = password_hash
            await session.execute(
                update(AccessToken)
                .where(AccessToken.created_by_user_id == user.id)
                .values(revoked_at=request.app.state.clock())
            )
        return await saved(session, user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        if personal(principal) != str(user_id):
            raise HTTPException(404, "User not found")
        if await session.scalar(
            select(Membership.id).where(Membership.user_id == str(user_id)).limit(1)
        ):
            raise HTTPException(409, "Leave company memberships before deleting the user")
        owners = select(TokenOwnership.id).where(TokenOwnership.user_id == str(user_id))
        await session.execute(delete(AccessToken).where(AccessToken.ownership_id.in_(owners)))
        await session.execute(delete(TokenOwnership).where(TokenOwnership.user_id == str(user_id)))
        await session.delete(await session.get(User, str(user_id)))


@router.post("/companies", status_code=201, response_model=out.Company)
async def create_company(data: CompanyInput, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user_id = personal(principal)
        company = Company(name=data.name, created_by_user_id=user_id)
        session.add(company)
        await session.flush()
        member = Membership(user_id=user_id, company_id=company.id, role="creator")
        session.add(member)
        await session.flush()
        return {
            **public(company),
            "my_role": "creator",
            "my_permissions": sorted(PERMISSIONS),
            "my_membership_id": member.id,
        }


@router.get("/companies", response_model=list[out.Company])
async def list_companies(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        condition = (
            Company.id == principal.company_id
            if principal.company_id
            else Company.id.in_(
                select(Membership.company_id).where(Membership.user_id == principal.user_id)
            )
        )
        rows = await session.execute(
            select(Company, Membership)
            .join(Membership, Membership.company_id == Company.id)
            .where(condition)
            .where(Membership.user_id == (principal.user_id or principal.actor_id))
            .order_by(Company.created_at, Company.id)
            .limit(limit)
            .offset(offset)
        )
        return [
            {
                **public(company),
                "my_role": member.role,
                "my_permissions": sorted(effective_permissions(member.role, member.permissions)),
                "my_membership_id": member.id,
            }
            for company, member in rows
        ]


@router.get("/companies/{company_id}", response_model=out.Company)
async def get_company(company_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await company_access(session, principal, str(company_id))
        company = await session.get(Company, str(company_id))
        if not company:
            raise HTTPException(404, "Company not found")
        member = await session.scalar(
            select(Membership).where(
                Membership.company_id == company.id,
                Membership.user_id == (principal.user_id or principal.actor_id),
            )
        )
        return {
            **public(company),
            "my_role": member.role,
            "my_permissions": sorted(effective_permissions(member.role, member.permissions)),
            "my_membership_id": member.id,
        }


@router.patch("/companies/{company_id}", response_model=out.Company)
async def update_company(company_id: UUID, data: CompanyInput, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await company_access(session, principal, str(company_id), admin=True)
        company = await session.get(Company, str(company_id))
        company.name = data.name
        return public(company)


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(company_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        if await membership_role(session, personal(principal), str(company_id)) != "creator":
            raise HTTPException(403, "Company creator required")
        if await session.scalar(
            select(Membership.id)
            .where(
                Membership.company_id == str(company_id), Membership.user_id != principal.user_id
            )
            .limit(1)
        ):
            raise HTTPException(409, "Other members must leave before deleting the company")
        if await session.scalar(
            select(TokenOwnership.id).where(TokenOwnership.company_id == str(company_id)).limit(1)
        ):
            raise HTTPException(409, "Remove company token ownerships before deleting the company")
        # The sole creator membership is intrinsic to an otherwise empty company.
        # Other resources remain protected by their foreign keys; failures roll back
        # this membership deletion together with the attempted company deletion.
        await session.execute(
            delete(Membership).where(
                Membership.company_id == str(company_id), Membership.user_id == principal.user_id
            )
        )
        await session.delete(await session.get(Company, str(company_id)))
        await session.execute(
            update(CompanyInvitation)
            .where(
                CompanyInvitation.company_id == str(company_id),
                CompanyInvitation.status == "pending",
            )
            .values(status="revoked")
        )


async def membership_change_allowed(session, principal, company_id, old_role, new_role):
    await company_access(session, principal, company_id, permission="members.manage")
    role = await membership_role(session, personal(principal), company_id)
    if (old_role in {"creator", "admin"} or new_role in {"creator", "admin"}) and role != "creator":
        raise HTTPException(403, "Only a creator may manage privileged memberships")
    if old_role == "creator" and new_role != "creator":
        count = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.company_id == company_id, Membership.role == "creator")
        )
        if count <= 1:
            raise HTTPException(409, "A company must retain at least one creator")


@router.post("/memberships", status_code=201, response_model=out.Membership)
async def create_membership(data: MembershipCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await membership_change_allowed(session, principal, str(data.company_id), None, data.role)
        await grant_allowed(session, principal, str(data.company_id), data.role, data.permissions)
        user = (
            await resolve_user(session, data.identifier)
            if data.identifier
            else await session.get(User, str(data.user_id))
        )
        if not user:
            raise HTTPException(404, "User not found")
        row = Membership(
            user_id=user.id,
            company_id=str(data.company_id),
            role=data.role,
            permissions=sorted(set(data.permissions)) if data.permissions is not None else None,
        )
        session.add(row)
        await session.flush()
        return await member_view(session, row)


@router.get("/memberships", response_model=list[out.Membership])
async def list_memberships(
    request: Request,
    response: Response,
    company_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
    user_id: UUID | None = None,
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        await company_access(session, principal, str(company_id))
        conditions = [
            Membership.company_id == str(company_id),
            Membership.user_id == str(user_id) if user_id else True,
        ]
        rows = await session.scalars(
            select(Membership)
            .where(*conditions)
            .order_by(Membership.id)
            .limit(limit)
            .offset(offset)
        )
        response.headers["X-Total-Count"] = str(
            await session.scalar(select(func.count()).select_from(Membership).where(*conditions))
        )
        return [await member_view(session, row) for row in rows]


async def get_membership(session, principal, membership_id, *, access_only=False):
    row = await session.get(Membership, str(membership_id))
    if not row:
        raise HTTPException(404, "Membership not found")
    if access_only:
        await company_access(session, principal, row.company_id, permission=None)
    else:
        await company_access(session, principal, row.company_id)
    return row


async def revoke_membership_tokens(session, membership, now):
    owners = select(TokenOwnership.id).where(TokenOwnership.company_id == membership.company_id)
    await session.execute(
        update(AccessToken)
        .where(
            AccessToken.ownership_id.in_(owners),
            AccessToken.created_by_user_id == membership.user_id,
            AccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await session.execute(
        update(CompanyInvitation)
        .where(
            CompanyInvitation.company_id == membership.company_id,
            CompanyInvitation.created_by_user_id == membership.user_id,
            CompanyInvitation.status == "pending",
        )
        .values(status="revoked")
    )


@router.get("/memberships/{membership_id}", response_model=out.Membership)
async def read_membership(membership_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        return await member_view(session, await get_membership(session, principal, membership_id))


@router.patch("/memberships/{membership_id}", response_model=out.Membership)
async def update_membership(membership_id: UUID, data: MembershipUpdate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await get_membership(session, principal, membership_id, access_only=True)
        role = data.role or row.role
        permissions = (
            data.permissions
            if "permissions" in data.model_fields_set
            else (row.permissions if role == row.role else None)
        )
        await membership_change_allowed(session, principal, row.company_id, row.role, role)
        await grant_allowed(session, principal, row.company_id, role, permissions)
        before = effective_permissions(row.role, row.permissions)
        after = effective_permissions(role, permissions)
        if (
            not before <= after
            or (row.role in {"creator", "admin"} and role == "user")
            or (row.role == "creator" and role != "creator")
        ):
            await revoke_membership_tokens(session, row, request.app.state.clock())
        row.role = role
        row.permissions = sorted(set(permissions)) if permissions is not None else None
        return await member_view(session, row)


@router.delete("/memberships/{membership_id}", status_code=204)
async def delete_membership(membership_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await get_membership(session, principal, membership_id, access_only=True)
        if row.user_id != personal(principal) or row.role == "creator":
            await membership_change_allowed(session, principal, row.company_id, row.role, None)
        await revoke_membership_tokens(session, row, request.app.state.clock())
        await session.delete(row)


@router.post("/token-ownerships", status_code=201, response_model=out.TokenOwnership)
async def create_token_owner(data: OwnerInput, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        personal(principal)
        return await saved(
            session, TokenOwnership(**await validate_owner(session, principal, data))
        )


@router.get("/token-ownerships", response_model=list[out.TokenOwnership])
async def list_token_owners(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        rows = await session.scalars(
            select(TokenOwnership)
            .where(owned_filter(TokenOwnership, principal))
            .order_by(TokenOwnership.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


@router.get("/token-ownerships/{ownership_id}", response_model=out.TokenOwnership)
async def get_token_owner(ownership_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        return public(await owned(session, TokenOwnership, ownership_id, principal))


@router.patch("/token-ownerships/{ownership_id}", response_model=out.TokenOwnership)
async def update_token_owner(ownership_id: UUID, data: OwnerInput, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        personal(principal)
        row = await owned(session, TokenOwnership, ownership_id, principal, admin=True)
        if await session.scalar(
            select(AccessToken.id).where(AccessToken.ownership_id == row.id).limit(1)
        ):
            raise HTTPException(409, "Delete attached tokens before changing their ownership")
        for key, value in (await validate_owner(session, principal, data)).items():
            setattr(row, key, value)
        return public(row)


@router.delete("/token-ownerships/{ownership_id}", status_code=204)
async def delete_token_owner(ownership_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        personal(principal)
        row = await owned(session, TokenOwnership, ownership_id, principal, admin=True)
        await session.execute(delete(AccessToken).where(AccessToken.ownership_id == row.id))
        await session.delete(row)


@router.post("/access-tokens", status_code=201, response_model=out.IssuedToken)
async def create_token(data: TokenCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        actor_id = personal(principal)
        owner = await owned(session, TokenOwnership, data.ownership_id, principal, admin=True)
        if not set(data.scopes) <= principal.scopes:
            raise HTTPException(403, "Cannot issue scopes absent from the current token")
        token, raw = await issue_token(
            session,
            owner,
            actor_id,
            data.name,
            set(data.scopes),
            request.app.state.clock() + timedelta(seconds=data.expires_in_seconds),
        )
        return {**public(token), "token": raw}


@router.get("/access-tokens", response_model=list[out.AccessToken])
async def list_tokens(
    request: Request, limit: int = Query(100, ge=1, le=200), offset: PaginationOffset = 0
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        rows = await session.scalars(
            select(AccessToken)
            .join(TokenOwnership, AccessToken.ownership_id == TokenOwnership.id)
            .where(owned_filter(TokenOwnership, principal))
            .order_by(AccessToken.id)
            .limit(limit)
            .offset(offset)
        )
        return [public(row) for row in rows]


async def token_for_user(session, principal, token_id, admin=False):
    token = await session.get(AccessToken, str(token_id))
    if not token:
        raise HTTPException(404, "Token not found")
    await owned(session, TokenOwnership, token.ownership_id, principal, admin=admin)
    return token


@router.get("/access-tokens/{token_id}", response_model=out.AccessToken)
async def get_token(token_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(await token_for_user(session, await authenticate(session, request), token_id))


@router.patch("/access-tokens/{token_id}", response_model=out.AccessToken)
async def update_token(token_id: UUID, data: TokenUpdate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        token = await token_for_user(session, principal, token_id, admin=True)
        if data.name is not None:
            token.name = data.name
        if data.revoke:
            token.revoked_at = request.app.state.clock()
        return public(token)


@router.delete("/access-tokens/{token_id}", status_code=204)
async def delete_token(token_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        await session.delete(await token_for_user(session, principal, token_id, admin=True))
