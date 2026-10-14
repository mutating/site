"""Member profiles, scoped user lookup and grants shared by membership and invitation APIs."""

from fastapi import HTTPException
from sqlalchemy import or_, select

from throng_api.models import Membership, User
from throng_api.permissions import effective_permissions
from throng_api.security import company_access, personal
from throng_api.serialization import public


def member_profile(user):
    return {
        field: getattr(user, field)
        for field in (
            "id",
            "first_name",
            "last_name",
            "nickname",
            "avatar_version",
        )
    }


async def member_view(session, row):
    user = await session.get(User, row.user_id)
    return {
        **public(row),
        "user": member_profile(user),
        "effective_permissions": sorted(effective_permissions(row.role, row.permissions)),
    }


async def resolve_user(session, identifier):
    identity = identifier.strip().casefold()
    return await session.scalar(
        select(User).where(or_(User.email == identity, User.nickname == identity))
    )


async def grant_allowed(session, principal, company_id, role, permissions):
    """Delegated managers cannot grant rights they do not have or create privileged roles."""
    await company_access(session, principal, company_id, permission="members.manage")
    issuer = await session.scalar(
        select(Membership).where(
            Membership.company_id == company_id, Membership.user_id == personal(principal)
        )
    )
    if role == "creator" and permissions is not None:
        raise HTTPException(422, "Creators always have all permissions")
    if issuer.role != "creator":
        if role in {"creator", "admin"}:
            raise HTTPException(403, "Only a creator may manage privileged memberships")
        if not effective_permissions(role, permissions) <= effective_permissions(
            issuer.role, issuer.permissions
        ):
            raise HTTPException(403, "Cannot grant permissions you do not have")
