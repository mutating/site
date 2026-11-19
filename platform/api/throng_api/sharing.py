"""Member profiles, scoped user lookup and grants shared by membership and invitation APIs."""

from fastapi import HTTPException
from sqlalchemy import or_, select

from throng_api.models import User
from throng_api.permissions import effective_permissions
from throng_api.security import company_access, personal
from throng_api.serialization import public

MEMBER_PROFILE_COLUMNS = (
    User.id,
    User.first_name,
    User.last_name,
    User.nickname,
    User.avatar_version,
)


def member_profile(user):
    return {column.key: getattr(user, column.key) for column in MEMBER_PROFILE_COLUMNS}


async def member_view(session, row):
    return member_with_profile(row, await session.get(User, row.user_id))


def member_with_profile(row, user):
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
    issuer = await company_access(session, principal, company_id, permission="members.manage")
    personal(principal)
    check_grant(issuer, role, permissions)


def check_grant(issuer, role, permissions):
    if role == "creator" and permissions is not None:
        raise HTTPException(422, "Creators always have all permissions")
    if issuer.role != "creator":
        if role in {"creator", "admin"}:
            raise HTTPException(403, "Only a creator may manage privileged memberships")
        if not effective_permissions(role, permissions) <= effective_permissions(
            issuer.role, issuer.permissions
        ):
            raise HTTPException(403, "Cannot grant permissions you do not have")
