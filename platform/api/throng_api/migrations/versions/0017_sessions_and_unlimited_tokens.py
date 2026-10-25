"""Separate login sessions from API tokens and allow non-expiring API tokens.

Revision ID: 0017
Revises: 0016
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def reflected(name):
    return sa.Table(name, sa.MetaData(), autoload_with=op.get_bind())


def upgrade():
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table(
        "legacy_credentials",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ownership_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("token_ownerships.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="legacy"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('login', 'legacy')", name="credential_purpose"),
    )
    for column in ("ownership_id", "created_by_user_id"):
        op.create_index(f"ix_legacy_credentials_{column}", "legacy_credentials", [column])
    connection = op.get_bind()
    tokens, owners = reflected("access_tokens"), reflected("token_ownerships")
    sessions, legacy = reflected("login_sessions"), reflected("legacy_credentials")
    # Only a known personal login whose owner matches its issuer can become a
    # session. Unknown history keeps its original authority in a separate store.
    personal_login = sa.and_(
        tokens.c.purpose == "login",
        owners.c.company_id.is_(None),
        owners.c.user_id == tokens.c.created_by_user_id,
    )
    connection.execute(
        sessions.insert().from_select(
            [
                "id",
                "created_at",
                "user_id",
                "name",
                "scopes",
                "session_hash",
                "expires_at",
                "revoked_at",
            ],
            sa.select(
                tokens.c.id,
                tokens.c.created_at,
                owners.c.user_id,
                tokens.c.name,
                tokens.c.scopes,
                tokens.c.token_hash,
                tokens.c.expires_at,
                tokens.c.revoked_at,
            )
            .select_from(tokens.join(owners, tokens.c.ownership_id == owners.c.id))
            .where(personal_login),
        )
    )
    connection.execute(
        legacy.insert().from_select(
            [column.name for column in tokens.c],
            sa.select(tokens).where(
                tokens.c.purpose != "api", ~tokens.c.id.in_(sa.select(sessions.c.id))
            ),
        )
    )
    # Record exactly the old login ownerships before removing those rows; empty
    # ownerships deliberately created for API use must not be removed.
    login_owners = list(
        connection.scalars(
            sa.select(tokens.c.ownership_id).where(tokens.c.id.in_(sa.select(sessions.c.id)))
        )
    )
    connection.execute(tokens.delete().where(tokens.c.purpose != "api"))
    for offset in range(0, len(login_owners), 500):
        connection.execute(
            owners.delete().where(
                owners.c.id.in_(login_owners[offset : offset + 500]),
                ~owners.c.id.in_(sa.select(tokens.c.ownership_id)),
                ~owners.c.id.in_(sa.select(legacy.c.ownership_id)),
            )
        )
    with op.batch_alter_table("access_tokens") as batch:
        batch.drop_constraint(op.f("ck_access_tokens_token_purpose"), type_="check")
        batch.create_check_constraint("token_purpose", "purpose = 'api'")
        batch.alter_column("purpose", existing_type=sa.String(16), server_default="api")
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade():
    connection = op.get_bind()
    tokens = reflected("access_tokens")
    if connection.scalar(sa.select(tokens.c.id).where(tokens.c.expires_at.is_(None)).limit(1)):
        raise CommandError(
            "Cannot downgrade while non-expiring API tokens exist; delete them first"
        )
    with op.batch_alter_table("access_tokens") as batch:
        batch.drop_constraint(op.f("ck_access_tokens_token_purpose"), type_="check")
        batch.create_check_constraint("token_purpose", "purpose IN ('api', 'login', 'legacy')")
        batch.alter_column("purpose", existing_type=sa.String(16), server_default="legacy")
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    legacy, sessions, owners = (
        reflected(name) for name in ("legacy_credentials", "login_sessions", "token_ownerships")
    )
    connection.execute(
        tokens.insert().from_select(
            [column.name for column in tokens.c],
            sa.select(*(legacy.c[column.name] for column in tokens.c)),
        )
    )
    for row in connection.execute(sa.select(sessions)).mappings():
        owner_id = uuid4().hex
        connection.execute(
            owners.insert().values(
                id=owner_id,
                created_at=row["created_at"],
                user_id=row["user_id"],
                company_id=None,
            )
        )
        connection.execute(
            tokens.insert().values(
                id=row["id"],
                created_at=row["created_at"],
                ownership_id=owner_id,
                created_by_user_id=row["user_id"],
                name=row["name"],
                purpose="login",
                token_hash=row["session_hash"],
                scopes=row["scopes"],
                expires_at=row["expires_at"],
                revoked_at=row["revoked_at"],
            )
        )
    op.drop_table("login_sessions")
    op.drop_table("legacy_credentials")
