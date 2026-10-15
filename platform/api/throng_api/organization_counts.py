"""Small directory totals, including organizations previewed by an invitation."""

from collections.abc import Iterable

from sqlalchemy import func, select

from throng_api.models import Company, ComputerOwnership, Membership


async def organization_counts(session, company_ids: Iterable[str]):
    ids = set(company_ids)
    if not ids:
        return {}
    # Separate correlated counts avoid multiplying computers by memberships.
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
    rows = await session.execute(select(Company.id, computers, members).where(Company.id.in_(ids)))
    return {
        company_id: {"computer_count": computer_count, "member_count": member_count}
        for company_id, computer_count, member_count in rows
    }
