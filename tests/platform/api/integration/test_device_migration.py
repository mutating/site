"""Migration 0028 keeps pending installations usable without attestation."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest
from alembic import command
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.anyio


async def test_pending_installation_becomes_a_device_key(alembic_config, settings):
    """An offline enrollment survives the upgrade and is linked to its account."""
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0027")
    user_id, enrollment_id, installation_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_enrollments "
                    "(id, actor_id, installation_id, request, expires_at, acme_secret, "
                    " order_created, challenge_token, created_at) "
                    "VALUES (:id, :actor, :installation, :request, :expires, :acme, 0, :challenge, :created)"
                ),
                {
                    "id": str(enrollment_id),
                    "actor": str(user_id),
                    "installation": str(installation_id),
                    "request": json.dumps({"installation_id": str(installation_id)}),
                    "expires": now,
                    "acme": "legacy-secret",
                    "challenge": "legacy-challenge",
                    "created": now,
                },
            )
    finally:
        engine.dispose()

    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")

    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    try:
        with engine.connect() as connection:
            device = connection.execute(
                text("SELECT id, client_key FROM devices WHERE client_key = :key"),
                {"key": str(installation_id)},
            ).one()
            enrollment = connection.execute(
                text("SELECT device_id FROM agent_enrollments WHERE id = :id"),
                {"id": str(enrollment_id)},
            ).one()
            account = connection.execute(
                text("SELECT 1 FROM device_accounts WHERE device_id = :device AND user_id = :user"),
                {"device": device.id, "user": str(user_id)},
            ).one()
    finally:
        engine.dispose()

    assert enrollment.device_id == device.id
    assert account[0] == 1
