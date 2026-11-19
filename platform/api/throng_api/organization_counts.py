"""Small directory totals, including organizations previewed by an invitation."""

from collections.abc import Iterable

from sqlalchemy import func, select

from throng_api.models import Company, ComputerOwnership, Membership


def organization_count_columns(company_id):
    # Separate counts avoid multiplying computers by memberships.
    computers = (
        select(func.count())
        .where(ComputerOwnership.company_id == company_id)
        .correlate(Company)
        .scalar_subquery()
        .label("computer_count")
    )
    members = (
        select(func.count())
        .where(Membership.company_id == company_id)
        .correlate(Company)
        .scalar_subquery()
        .label("member_count")
    )
    return computers, members


async def organization_counts(session, company_ids: Iterable[str]):
    ids = set(company_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Company.id, *organization_count_columns(Company.id)).where(Company.id.in_(ids))
    )
    return {
        company_id: {"computer_count": computer_count, "member_count": member_count}
        for company_id, computer_count, member_count in rows
    }
