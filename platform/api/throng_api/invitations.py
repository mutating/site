"""Addressed inbox invitations and revocable, expiring, single-use share links."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import and_, func, or_, select

from throng_api import outputs as out
from throng_api.accounts import membership_change_allowed
from throng_api.models import Company, CompanyInvitation, Membership, User
from throng_api.permissions import effective_permissions
from throng_api.schemas import InvitationCreate, PaginationOffset
from throng_api.security import (
    SCOPES,
    Principal,
    access_token_scheme,
    authenticate,
    company_access,
    digest,
    personal,
    secret,
)
from throng_api.serialization import public
from throng_api.sharing import grant_allowed, member_profile, member_view, resolve_user

router = APIRouter(
    prefix="/api/v1", tags=["organization sharing"], dependencies=[Depends(access_token_scheme)]
)


def invitation_view(row, now):
    return {
        **public(row),
        "status": "expired" if row.status == "pending" and row.expires_at <= now else row.status,
        "effective_permissions": sorted(effective_permissions(row.role, row.permissions)),
    }


def recipient_condition(user):
    return or_(
        CompanyInvitation.invitee_user_id == user.id,
        and_(CompanyInvitation.invitee_user_id.is_(None), CompanyInvitation.email == user.email),
    )


def addressed_to(row, user):
    return (
        row.invitee_user_id == user.id
        if row.invitee_user_id
        else (row.email is None or row.email == user.email)
    )


def pending(row, now):
    if row.status != "pending" or row.expires_at <= now:
        raise HTTPException(410, "Invitation is no longer available")


async def invitation_for_manager(session, principal, invitation_id, now):
    row = await session.get(CompanyInvitation, str(invitation_id))
    if not row:
        raise HTTPException(404, "Invitation not found")
    await membership_change_allowed(session, principal, row.company_id, None, row.role)
    pending(row, now)
    return row


@router.get("/companies/{company_id}/users/lookup", response_model=out.MemberProfile)
async def lookup_user(
    company_id: UUID, request: Request, identifier: str = Query(min_length=1, max_length=320)
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, "manage")
        await company_access(session, principal, str(company_id), permission="members.manage")
        user = await resolve_user(session, identifier)
        if not user:
            raise HTTPException(404, "User not found")
        return member_profile(user)


@router.get("/memberships/{membership_id}/avatar", response_class=Response)
async def member_avatar(membership_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        row = await session.get(Membership, str(membership_id))
        if not row:
            raise HTTPException(404, "Member not found")
        await company_access(session, principal, row.company_id, permission="members.read")
        avatar = await session.scalar(select(User.avatar_data).where(User.id == row.user_id))
        if avatar is None:
            raise HTTPException(404, "Avatar not found")
        return Response(
            avatar, media_type="image/webp", headers={"Cache-Control": "private, no-store"}
        )


@router.post("/invitations", status_code=201, response_model=out.CreatedInvitation)
async def create_invitation(data: InvitationCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        company_id = str(data.company_id)
        await grant_allowed(session, principal, company_id, data.role, data.permissions)
        company = await session.get(Company, company_id)
        invitee = await resolve_user(session, data.identifier) if data.identifier else None
        email = invitee.email if invitee else None
        if data.identifier and not invitee:
            try:
                email = str(TypeAdapter(EmailStr).validate_python(data.identifier)).casefold()
            except ValidationError:
                raise HTTPException(404, "User not found") from None
        now = request.app.state.clock()
        if invitee:
            if await session.scalar(
                select(Membership.id).where(
                    Membership.company_id == company_id, Membership.user_id == invitee.id
                )
            ):
                raise HTTPException(409, "User is already a member")
        if email:
            if await session.scalar(
                select(CompanyInvitation.id).where(
                    CompanyInvitation.company_id == company_id,
                    CompanyInvitation.email == email,
                    CompanyInvitation.status == "pending",
                    CompanyInvitation.expires_at > now,
                )
            ):
                raise HTTPException(409, "An active invitation already exists")
        raw = secret("inv_")
        row = CompanyInvitation(
            company_id=company_id,
            company_name=company.name,
            created_by_user_id=personal(principal),
            invitee_user_id=invitee.id if invitee else None,
            email=email,
            role=data.role,
            permissions=sorted(set(data.permissions)) if data.permissions is not None else None,
            token_hash=digest(raw),
            expires_at=now + timedelta(seconds=data.expires_in_seconds),
        )
        session.add(row)
        await session.flush()
        return {**invitation_view(row, now), "token": raw}


@router.get("/invitations", response_model=list[out.Invitation])
async def list_invitations(
    request: Request,
    response: Response,
    company_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: PaginationOffset = 0,
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        user = await session.get(User, personal(principal))
        now = request.app.state.clock()
        if company_id:
            await company_access(session, principal, str(company_id), permission="members.manage")
            condition = CompanyInvitation.company_id == str(company_id)
        else:
            # Open links are never listed publicly; addressed invitations appear only in their recipient's inbox.
            condition = and_(
                recipient_condition(user),
                CompanyInvitation.status == "pending",
                CompanyInvitation.expires_at > now,
            )
        response.headers["X-Total-Count"] = str(
            await session.scalar(
                select(func.count()).select_from(CompanyInvitation).where(condition)
            )
        )
        rows = await session.scalars(
            select(CompanyInvitation)
            .where(condition)
            .order_by(CompanyInvitation.created_at.desc(), CompanyInvitation.id)
            .limit(limit)
            .offset(offset)
        )
        return [invitation_view(row, now) for row in rows]


@router.get("/invitations/link/{token}", response_model=out.Invitation)
async def read_link(token: str, request: Request):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        row = await session.scalar(
            select(CompanyInvitation).where(CompanyInvitation.token_hash == digest(token))
        )
        if not row:
            raise HTTPException(404, "Invitation not found")
        user = await session.get(User, personal(principal))
        if not addressed_to(row, user):
            raise HTTPException(403, "Invitation belongs to another account")
        pending(row, request.app.state.clock())
        return invitation_view(row, request.app.state.clock())


async def accept(session, principal, row, now):
    pending(row, now)
    user_id = personal(principal)
    user = await session.get(User, user_id)
    if not addressed_to(row, user):
        raise HTTPException(403, "Invitation belongs to another account")
    # Revalidate the issuer's live membership and grant ceiling, including pending invitations issued before demotion.
    issuer = Principal(row.created_by_user_id, None, row.created_by_user_id, SCOPES)
    await grant_allowed(session, issuer, row.company_id, row.role, row.permissions)
    if await session.scalar(
        select(Membership.id).where(
            Membership.company_id == row.company_id, Membership.user_id == user_id
        )
    ):
        raise HTTPException(409, "User is already a member")
    member = Membership(
        company_id=row.company_id, user_id=user_id, role=row.role, permissions=row.permissions
    )
    session.add(member)
    row.status = "accepted"
    row.accepted_by_user_id = user_id
    await session.flush()
    return await member_view(session, member)


@router.post("/invitations/link/{token}/accept", response_model=out.Membership)
async def accept_link(token: str, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await session.scalar(
            select(CompanyInvitation).where(CompanyInvitation.token_hash == digest(token))
        )
        if not row:
            raise HTTPException(404, "Invitation not found")
        return await accept(session, principal, row, request.app.state.clock())


@router.post("/invitations/{invitation_id}/accept", response_model=out.Membership)
async def accept_addressed(invitation_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user = await session.get(User, personal(principal))
        row = await session.scalar(
            select(CompanyInvitation).where(
                CompanyInvitation.id == str(invitation_id), recipient_condition(user)
            )
        )
        if not row:
            raise HTTPException(404, "Invitation not found")
        return await accept(session, principal, row, request.app.state.clock())


@router.post("/invitations/{invitation_id}/decline", status_code=204)
async def decline_invitation(invitation_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        user = await session.get(User, personal(principal))
        row = await session.scalar(
            select(CompanyInvitation).where(
                CompanyInvitation.id == str(invitation_id), recipient_condition(user)
            )
        )
        if not row:
            raise HTTPException(404, "Invitation not found")
        pending(row, request.app.state.clock())
        row.status = "declined"


@router.post("/invitations/{invitation_id}/link", response_model=out.CreatedInvitation)
async def rotate_link(invitation_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        now = request.app.state.clock()
        row = await invitation_for_manager(session, principal, invitation_id, now)
        await grant_allowed(session, principal, row.company_id, row.role, row.permissions)
        raw = secret("inv_")
        row.token_hash = digest(raw)
        row.created_by_user_id = personal(principal)
        return {**invitation_view(row, now), "token": raw}


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        row = await invitation_for_manager(
            session, principal, invitation_id, request.app.state.clock()
        )
        row.status = "revoked"
