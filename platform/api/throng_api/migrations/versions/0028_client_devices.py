"""Replace hardware attestation with client-generated device identities.

Revision ID: 0028
Revises: 0027

Existing certified computers keep their device row and gain an account
association. Pending enrollments keep their installation UUID as a device key
so they can still finish after the upgrade.
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    op.drop_table("device_certificates")
    op.drop_table("protocol_nonces")
    op.rename_table("physical_devices", "devices")
    with op.batch_alter_table("devices") as batch:
        batch.alter_column("identity_hash", new_column_name="client_key")

    op.create_table(
        "device_accounts",
        sa.Column("device_id", sa.Uuid(as_uuid=False), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    with op.batch_alter_table("computers") as batch:
        batch.drop_column("certificate_sha256")
        batch.drop_column("legacy_transfer_user_id")
        batch.drop_column("legacy_transfer_pending")

    with op.batch_alter_table("agent_mutations") as batch:
        batch.drop_column("certificate_sha256")

    with op.batch_alter_table("agent_enrollments") as batch:
        batch.add_column(sa.Column("device_id", sa.Uuid(as_uuid=False)))

    # The installation id was the durable local key in the old protocol. Keep
    # it as the device key so an offline client can finish its pending attempt.
    seen = {}
    rows = connection.execute(
        sa.text("SELECT id, actor_id, installation_id FROM agent_enrollments")
    ).all()
    for row in rows:
        installation = str(row.installation_id or "").lower()
        if not installation:
            continue
        device = seen.get(installation)
        if device is None:
            device = connection.execute(
                sa.text("SELECT id FROM devices WHERE client_key = :key"),
                {"key": installation},
            ).scalar_one_or_none()
            if device is None:
                device = str(uuid4())
                connection.execute(
                    sa.text(
                        "INSERT INTO devices (id, client_key, created_at) "
                        "VALUES (:id, :key, CURRENT_TIMESTAMP)"
                    ),
                    {"id": device, "key": installation},
                )
            seen[installation] = device
        connection.execute(
            sa.text("UPDATE agent_enrollments SET device_id = :device WHERE id = :id"),
            {"device": device, "id": row.id},
        )
        exists = connection.execute(
            sa.text(
                "SELECT 1 FROM device_accounts "
                "WHERE device_id = :device AND user_id = :user"
            ),
            {"device": device, "user": row.actor_id},
        ).first()
        if not exists:
            connection.execute(
                sa.text(
                    "INSERT INTO device_accounts "
                    "(device_id, user_id, first_seen_at, last_seen_at) "
                    "VALUES (:device, :user, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"device": device, "user": row.actor_id},
            )

    # Preserve the account link for already connected computers.
    computers = sa.table(
        "computers",
        sa.column("id", sa.Uuid(as_uuid=False)),
        sa.column("device_id", sa.Uuid(as_uuid=False)),
        sa.column("registered_by_user_id", sa.Uuid(as_uuid=False)),
        sa.column("connected_user_id", sa.Uuid(as_uuid=False)),
        schema=None,
    )
    accounts = sa.table(
        "device_accounts",
        sa.column("device_id", sa.Uuid(as_uuid=False)),
        sa.column("user_id", sa.Uuid(as_uuid=False)),
        sa.column("first_seen_at", sa.DateTime(timezone=True)),
        sa.column("last_seen_at", sa.DateTime(timezone=True)),
        schema=None,
    )
    seen_accounts = set()
    for row in connection.execute(
        sa.select(
            computers.c.device_id,
            computers.c.registered_by_user_id,
            computers.c.connected_user_id,
        ).where(computers.c.device_id.is_not(None))
    ):
        for user_id in {row.registered_by_user_id, row.connected_user_id} - {None}:
            key = (row.device_id, user_id)
            if key in seen_accounts:
                continue
            seen_accounts.add(key)
            connection.execute(
                accounts.insert().values(
                    device_id=row.device_id,
                    user_id=user_id,
                    first_seen_at=sa.func.now(),
                    last_seen_at=sa.func.now(),
                )
            )

    with op.batch_alter_table("agent_enrollments") as batch:
        for name in (
            "installation_id",
            "acme_secret",
            "account_jwk",
            "order_created",
            "challenge_token",
            "attested_key",
            "identity_hash",
            "certificate_sha256",
            "csr_sha256",
        ):
            batch.drop_column(name)
        batch.alter_column("device_id", nullable=False)
        batch.create_foreign_key(
            "fk_agent_enrollments_device_id_devices", "devices", ["device_id"], ["id"]
        )


def downgrade():
    # Recreating the removed trust material is intentionally impossible. The
    # downgrade restores the schema shape with nullable legacy fields so an
    # operator can restore a pre-0028 backup when attestation is required.
    with op.batch_alter_table("agent_enrollments") as batch:
        batch.drop_constraint("fk_agent_enrollments_device_id_devices", type_="foreignkey")
        batch.drop_column("device_id")
        batch.add_column(sa.Column("installation_id", sa.Uuid(as_uuid=False)))
        batch.add_column(sa.Column("acme_secret", sa.String(length=64)))
        batch.add_column(sa.Column("account_jwk", sa.JSON()))
        batch.add_column(sa.Column("order_created", sa.Boolean()))
        batch.add_column(sa.Column("challenge_token", sa.String(length=64)))
        batch.add_column(sa.Column("attested_key", sa.LargeBinary()))
        batch.add_column(sa.Column("identity_hash", sa.String(length=64)))
        batch.add_column(sa.Column("certificate_sha256", sa.String(length=64)))
        batch.add_column(sa.Column("csr_sha256", sa.String(length=64)))
    op.drop_table("device_accounts")
    with op.batch_alter_table("devices") as batch:
        batch.alter_column("client_key", new_column_name="identity_hash")
    op.rename_table("devices", "physical_devices")
    with op.batch_alter_table("computers") as batch:
        batch.add_column(sa.Column("certificate_sha256", sa.String(length=64)))
        batch.add_column(sa.Column("legacy_transfer_user_id", sa.Uuid(as_uuid=False)))
        batch.add_column(
            sa.Column("legacy_transfer_pending", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_foreign_key(
            "fk_computers_legacy_transfer_user_id_users",
            "users",
            ["legacy_transfer_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("agent_mutations") as batch:
        batch.add_column(sa.Column("certificate_sha256", sa.String(length=64)))
    op.create_table(
        "device_certificates",
        sa.Column("sha256", sa.String(length=64), primary_key=True),
        sa.Column("device_id", sa.Uuid(as_uuid=False), sa.ForeignKey("physical_devices.id")),
        sa.Column("enrollment_id", sa.Uuid(as_uuid=False), nullable=False, unique=True),
        sa.Column("pem", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "protocol_nonces",
        sa.Column("namespace", sa.String(length=80), primary_key=True),
        sa.Column("value", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
