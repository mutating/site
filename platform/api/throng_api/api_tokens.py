"""Owner-aware API-token registry; login sessions never become API-token entries."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select

from throng_api import outputs as out
from throng_api.models import AccessToken, TokenOwnership, User
from throng_api.schemas import ApiTokenCreate, PaginationOffset
from throng_api.security import (
    access_token_scheme,
    authenticate,
    company_access,
    issue_token,
    owned_filter,
    owner_access,
    personal,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/v1/api-tokens", tags=["accounts"], dependencies=[Depends(access_token_scheme)]
)


def token_view(token, owner, issuer):
    return {
        **public(token),
        "user_id": owner.user_id,
        "company_id": owner.company_id,
        "issuer": out.MemberProfile(**public(issuer)),
    }


@router.get("", response_model=list[out.ApiToken])
async def registry(
    request: Request,
    response: Response,
    user_id: UUID | None = None,
    company_id: UUID | None = None,
    limit: int = Query(10, ge=1, le=200),
    offset: PaginationOffset = 0,
):
    if user_id and company_id:
        raise HTTPException(422, "Choose exactly one owner filter")
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        if user_id and str(user_id) != personal(principal):
            raise HTTPException(404, "Resource not found")
        if company_id:
            await company_access(session, principal, company_id)
        base = (
            select(AccessToken, TokenOwnership, User)
            .join(TokenOwnership, AccessToken.ownership_id == TokenOwnership.id)
            .join(User, AccessToken.created_by_user_id == User.id)
            .where(
                owned_filter(TokenOwnership, principal),
                # Legacy credentials may be logins; only explicit API issuance
                # proves a token belongs in this registry, regardless of its name.
                AccessToken.purpose == "api",
            )
        )
        if user_id:
            base = base.where(TokenOwnership.user_id == str(user_id))
        if company_id:
            base = base.where(TokenOwnership.company_id == str(company_id))
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Total-Count"] = str(
            await session.scalar(select(func.count()).select_from(base.subquery()))
        )
        rows = await session.execute(
            base.order_by(AccessToken.created_at.desc(), AccessToken.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [token_view(token, owner, issuer) for token, owner, issuer in rows]


@router.post("", status_code=201, response_model=out.IssuedApiToken)
async def create(data: ApiTokenCreate, request: Request, response: Response):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        actor = personal(principal)
        await owner_access(session, principal, data, admin=True)
        if not set(data.scopes) <= principal.scopes:
            raise HTTPException(403, "Cannot issue scopes absent from the current token")
        owner = TokenOwnership(
            user_id=str(data.user_id) if data.user_id else None,
            company_id=str(data.company_id) if data.company_id else None,
        )
        session.add(owner)
        await session.flush()
        token, raw = await issue_token(
            session,
            owner,
            actor,
            data.name,
            set(data.scopes),
            request.app.state.clock() + timedelta(seconds=data.expires_in_seconds)
            if data.expires_in_seconds is not None
            else None,
        )
        response.headers["Cache-Control"] = "no-store"
        return {**token_view(token, owner, await session.get(User, actor)), "token": raw}
