"""Policy helpers remain safe for internal callers without a route-specific permission context."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from throng_api.models import ComputerOwnership
from throng_api.security import SCOPES, Principal, company_access, owned_filter

pytestmark = pytest.mark.anyio


async def test_internal_membership_policy_requires_admin_and_preserves_owner_filter(
    app, user, other_user, membership, company, company_agent
):
    """Legacy internal callers get membership-scoped reading and still require privilege for management."""
    caller = Principal(other_user["id"], None, other_user["id"], SCOPES)
    async with app.state.db.read() as session:
        await company_access(session, caller, company["id"])
        with pytest.raises(HTTPException) as error:
            await company_access(session, caller, company["id"], admin=True)
        assert error.value.status_code == 403
        rows = (
            await session.scalars(
                select(ComputerOwnership).where(owned_filter(ComputerOwnership, caller))
            )
        ).all()
        assert len(rows) == 1 and rows[0].computer_id == company_agent["computer"]["id"]
