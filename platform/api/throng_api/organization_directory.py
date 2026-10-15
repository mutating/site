"""The personal organization directory: authorize, match, sort, then paginate."""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import and_, case, func, or_, select

from throng_api import outputs as out
from throng_api.invitations import invitation_view, recipient_condition
from throng_api.models import Company, CompanyInvitation, ComputerOwnership, Membership, User
from throng_api.organization_names import normalize_name
from throng_api.permissions import effective_permissions
from throng_api.schemas import PaginationOffset
from throng_api.security import access_token_scheme, authenticate, personal
from throng_api.serialization import public

router = APIRouter(
    prefix="/v1", tags=["organizations"], dependencies=[Depends(access_token_scheme)]
)


@router.get("/organization-directory", response_model=out.OrganizationDirectory)
async def directory(
    request: Request,
    response: Response,
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=200),
    offset: PaginationOffset = 0,
):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request)
        user_id = personal(principal)
        user = await session.get(User, user_id)
        now = request.app.state.clock()
        # Pick one valid addressed invitation per company, never open links.
        invitation_id = (
            select(CompanyInvitation.id)
            .where(
                CompanyInvitation.company_id == Company.id,
                recipient_condition(user),
                CompanyInvitation.status == "pending",
                CompanyInvitation.expires_at > now,
            )
            .order_by(CompanyInvitation.id)
            .limit(1)
            .correlate(Company)
            .scalar_subquery()
        )
        visible = or_(Membership.id.is_not(None), invitation_id.is_not(None))
        base = (
            select(Company, Membership)
            .outerjoin(
                Membership, and_(Membership.company_id == Company.id, Membership.user_id == user_id)
            )
            .where(visible)
        )
        total_all = await session.scalar(select(func.count()).select_from(base.subquery()))
        normalized = normalize_name(q.strip())
        if normalized:
            # autoescape makes %, _ and the escape character literal substrings.
            base = base.where(Company.name_search.contains(normalized, autoescape=True))
            total = await session.scalar(select(func.count()).select_from(base.subquery()))
        else:
            total = total_all
        priority = case(
            (Membership.id.is_(None), 0), (Company.created_by_user_id == user_id, 1), else_=2
        )
        computers = (
            select(func.count(ComputerOwnership.id))
            .where(ComputerOwnership.company_id == Company.id)
            .correlate(Company)
            .scalar_subquery()
        )
        members = (
            select(func.count(Membership.id))
            .where(Membership.company_id == Company.id)
            .correlate(Company)
            .scalar_subquery()
        )
        collation = "C" if session.bind.dialect.name == "postgresql" else "BINARY"
        rows = (
            await session.execute(
                base.add_columns(invitation_id, priority, computers, members)
                .order_by(
                    priority, computers.desc(), Company.name_key.collate(collation), Company.id
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        ids = [invite_id for _, member, invite_id, *_ in rows if member is None]
        invitations = (
            {
                row.id: row
                for row in (
                    await session.scalars(
                        select(CompanyInvitation).where(CompanyInvitation.id.in_(ids))
                    )
                ).all()
            }
            if ids
            else {}
        )
        items = []
        for company, member, invite_id, group, computer_count, member_count in rows:
            counts = {"computer_count": computer_count, "member_count": member_count}
            if member is not None:
                items.append(
                    {
                        "kind": "company",
                        "group": group,
                        "company": {
                            **public(company),
                            **counts,
                            "my_role": member.role,
                            "my_membership_id": member.id,
                            "my_permissions": sorted(
                                effective_permissions(member.role, member.permissions)
                            ),
                        },
                    }
                )
            else:
                items.append(
                    {
                        "kind": "invitation",
                        "group": 0,
                        "invitation": {
                            **invitation_view(invitations[invite_id], now),
                            **counts,
                            "company_name": company.name,
                            "name_key": company.name_key,
                        },
                    }
                )
        response.headers["X-Total-Count"] = str(total)
        return {"items": items, "total": total, "total_all": total_all}
