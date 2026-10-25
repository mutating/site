"""Fixtures for credentials retained from before the separate login-session store."""

from throng_api.models import AccessToken, LegacyCredential


async def move_to_legacy(session, identifier):
    """Move a persisted API credential into history without changing its secret or authority."""
    row = await session.get(AccessToken, identifier)
    values = {column.key: getattr(row, column.key) for column in AccessToken.__table__.columns}
    values["purpose"] = "legacy"
    legacy = LegacyCredential(**values)
    session.add(legacy)
    await session.delete(row)
    return legacy
