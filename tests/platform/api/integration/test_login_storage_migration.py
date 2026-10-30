"""Storage separation preserves credentials instead of inferring purpose from names."""

import asyncio
import json
from datetime import timedelta

import anyio
import httpx
import pytest
from alembic import command
from alembic.util import CommandError
from sqlalchemy import inspect, text, update

from throng_api.models import AccessToken

pytestmark = pytest.mark.anyio


async def tables(database):
    """Inspect the database schema without depending on the current ORM version."""
    async with database.engine.connect() as connection:
        return set(
            await connection.run_sync(lambda connection: inspect(connection).get_table_names())
        )


async def old_credentials(database):
    """Include logical ownership and every credential field needed to detect lossy migration."""
    async with database.engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT a.id, a.created_at, a.token_hash, a.name, a.scopes, a.expires_at, "
                    "a.revoked_at, a.created_by_user_id, a.purpose, o.user_id, o.company_id "
                    "FROM access_tokens a JOIN token_ownerships o ON o.id = a.ownership_id ORDER BY a.id"
                )
            )
        ).all()
        return [tuple(row) for row in rows]


async def rollback_state(database):
    """Capture schema, revision and every credential field before a rejected rollback."""

    def schema(connection):
        """Reflect columns and constraints without relying on the current ORM models."""
        inspector = inspect(connection)
        return {
            name: json.dumps(
                {
                    "columns": inspector.get_columns(name),
                    "primary_key": inspector.get_pk_constraint(name),
                    "foreign_keys": inspector.get_foreign_keys(name),
                    "unique_constraints": inspector.get_unique_constraints(name),
                    "check_constraints": inspector.get_check_constraints(name),
                    "indexes": inspector.get_indexes(name),
                },
                sort_keys=True,
                default=str,
            )
            for name in inspector.get_table_names()
        }

    async with database.engine.connect() as connection:
        rows = {}
        for name, sql in {
            "access_tokens": "SELECT * FROM access_tokens ORDER BY id",
            "login_sessions": "SELECT * FROM login_sessions ORDER BY id",
            "legacy_credentials": "SELECT * FROM legacy_credentials ORDER BY id",
            "token_ownerships": "SELECT * FROM token_ownerships ORDER BY id",
        }.items():
            rows[name] = [tuple(row) for row in await connection.execute(text(sql))]
        return {
            "schema": await connection.run_sync(schema),
            "revision": await connection.scalar(text("SELECT version_num FROM alembic_version")),
            "rows": rows,
        }


@pytest.mark.parametrize("session_mode", ["active", "revoked", "expired", "read_only"])
async def test_split_upgrade_and_rollback_preserve_identity_scope_and_ownership(
    api,
    app,
    clock,
    session_mode,
    user,
    access_token,
    company_token,
    company,
    http_server,
    migration_database,
    alembic_config,
):
    """Migrate renamed logins, real API tokens called login, unknown legacy and anomalous company logins without changing hashes, authority, dates or IDs."""
    extra = await api.post(
        "/v1/api-tokens",
        json={"company_id": company["id"], "name": "Restricted", "scopes": ["read"]},
    )
    assert extra.status_code == 201
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0016")
    async with migration_database.engine.begin() as connection:
        for identifier, purpose, name in [
            (user["login"]["token"]["id"], "login", "Renamed session"),
            (access_token["id"], "legacy", "login"),
            (company_token["id"], "api", "login"),
            (extra.json()["id"], "login", "login"),
        ]:
            await connection.execute(
                text("UPDATE access_tokens SET purpose=:purpose, name=:name WHERE id=:id"),
                {"id": identifier.replace("-", ""), "purpose": purpose, "name": name},
            )
    async with migration_database.engine.begin() as connection:
        values = {
            "revoked": {"revoked_at": clock.now},
            "expired": {"expires_at": clock.now - timedelta(seconds=1)},
            "read_only": {"scopes": ["read"]},
        }.get(session_mode)
        if values:
            await connection.execute(
                update(AccessToken)
                .where(AccessToken.id == user["login"]["token"]["id"])
                .values(**values)
            )
    before = await old_credentials(migration_database)
    assert len(before) == 4
    await migration_database.close()
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    assert {"login_sessions", "legacy_credentials"} <= await tables(migration_database)
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT purpose FROM access_tokens"))
        ).scalars().all() == ["api"]
        assert (
            await connection.execute(text("SELECT name FROM login_sessions"))
        ).scalars().all() == ["Renamed session"]
        assert set(
            (await connection.execute(text("SELECT purpose FROM legacy_credentials"))).scalars()
        ) == {"legacy", "login"}
    await anyio.to_thread.run_sync(command.check, alembic_config)
    # Use the production handlers and the reopened real DB after the server's
    # shutdown; neither authentication nor storage is mocked by this transport.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://migration.test"
    ) as restored:
        for raw, expected in (
            (
                user["login"]["access_token"],
                {"active": 200, "revoked": 401, "expired": 401, "read_only": 403}[session_mode],
            ),
            (access_token["token"], 200),
            (company_token["token"], 200),
        ):
            response = await restored.get(
                "/v1/auth/check", headers={"Authorization": "Bearer " + raw}
            )
            assert response.status_code == expected, response.text
        assert (
            await restored.get(
                "/v1/auth/check", headers={"Authorization": "Bearer " + extra.json()["token"]}
            )
        ).status_code == 403
    await migration_database.close()
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0016")
    assert await old_credentials(migration_database) == before
    await migration_database.close()
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(command.check, alembic_config)


async def test_rollback_with_unlimited_tokens_is_rejected_before_any_schema_change(
    api,
    user,
    http_server,
    migration_database,
    alembic_config,
):
    """An old schema cannot represent unlimited expiry; rollback must fail before dropping sessions or silently shortening/deleting a credential."""
    response = await api.post(
        "/v1/api-tokens",
        json={
            "user_id": user["id"],
            "name": "Forever",
            "scopes": ["read"],
            "expires_in_seconds": None,
        },
    )
    assert response.status_code == 201, response.text
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    # Isolate 0017's guard from successful rollbacks of later revisions. SQLite
    # does not roll those earlier steps back when this revision rejects a downgrade.
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0017")
    before = await rollback_state(migration_database)
    assert before["revision"] == "0017"
    assert before["rows"]["login_sessions"]
    await migration_database.close()
    with pytest.raises(CommandError, match="non-expiring"):
        await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0016")
    assert await rollback_state(migration_database) == before
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT expires_at FROM access_tokens"))
        ).scalars().all() == [None]
    await migration_database.close()
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(command.check, alembic_config)


async def test_many_old_logins_migrate_with_bounded_database_parameters(
    user, http_server, migration_database, alembic_config, monkeypatch, clock
):
    """A large session history must not turn owner cleanup into one oversized IN query that prevents upgrading a real SQLite database."""
    import sqlite3
    from uuid import uuid4

    from throng_api.models import TokenOwnership

    if migration_database.engine.dialect.name != "sqlite":
        pytest.skip("Exercises SQLite's configurable bind-parameter limit")
    http_server.server.should_exit = True
    await asyncio.wait_for(http_server.task, timeout=10)
    await migration_database.close()
    await anyio.to_thread.run_sync(command.downgrade, alembic_config, "0016")
    owners = [str(uuid4()) for _ in range(1100)]
    async with migration_database.engine.begin() as connection:
        await connection.execute(
            TokenOwnership.__table__.insert(),
            [
                {"id": identifier, "user_id": user["id"], "company_id": None}
                for identifier in owners
            ],
        )
        await connection.execute(
            AccessToken.__table__.insert(),
            [
                {
                    "ownership_id": identifier,
                    "created_by_user_id": user["id"],
                    "name": "login",
                    "purpose": "login",
                    "token_hash": f"{index:064x}",
                    "scopes": ["read"],
                    "expires_at": clock.now + timedelta(days=1),
                }
                for index, identifier in enumerate(owners)
            ],
        )
    async with migration_database.engine.begin() as connection:
        await connection.execute(TokenOwnership.__table__.insert().values(user_id=user["id"]))
        await connection.execute(
            AccessToken.__table__.insert().values(
                ownership_id=owners[0],
                created_by_user_id=user["id"],
                name="API sharing an old login owner",
                purpose="api",
                token_hash="f" * 64,
                scopes=["read"],
                expires_at=clock.now + timedelta(days=1),
            )
        )
    await migration_database.close()
    original = sqlite3.connect

    def limited_connection(*args, **kwargs):
        """Use SQLite's real parameter limit, without mocking migration SQL or storage."""
        connection = original(*args, **kwargs)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 1000)
        return connection

    monkeypatch.setattr(sqlite3, "connect", limited_connection)
    await anyio.to_thread.run_sync(command.upgrade, alembic_config, "head")
    async with migration_database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT COUNT(*) FROM login_sessions")) == 1101
        assert await connection.scalar(text("SELECT COUNT(*) FROM access_tokens")) == 1
        assert await connection.scalar(text("SELECT COUNT(*) FROM token_ownerships")) == 2
