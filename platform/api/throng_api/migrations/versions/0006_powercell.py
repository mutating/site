"""Power Cell identity, capacity policies and durable agent reports.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "computers", sa.Column("accepting_work", sa.Boolean(), nullable=False, server_default="1")
    )
    op.add_column(
        "computers",
        sa.Column("capacity_revision", sa.BigInteger(), nullable=False, server_default="-1"),
    )
    op.add_column("computers", sa.Column("capacity_report", sa.JSON()))
    op.add_column("computers", sa.Column("target_cpu", sa.BigInteger()))
    op.add_column("computers", sa.Column("target_memory", sa.BigInteger()))
    op.add_column("computers", sa.Column("target_disk", sa.BigInteger()))
    op.add_column(
        "computers",
        sa.Column("default_isolation", sa.String(length=8), nullable=False, server_default="vm"),
    )
    op.add_column("computers", sa.Column("isolation_modes", sa.JSON()))
    op.add_column(
        "computers", sa.Column("network_enabled", sa.Boolean(), nullable=False, server_default="0")
    )
    op.add_column(
        "computers", sa.Column("internet_only", sa.Boolean(), nullable=False, server_default="1")
    )
    op.add_column("computers", sa.Column("device_id", sa.Uuid(as_uuid=False)))
    op.add_column("computers", sa.Column("certificate_sha256", sa.String(length=64)))
    op.add_column("isolates", sa.Column("isolation_mode", sa.String(length=8)))
    op.add_column("isolates", sa.Column("effective_isolation", sa.String(length=8)))
    op.add_column(
        "isolates", sa.Column("retry_allowed", sa.Boolean(), nullable=False, server_default="1")
    )
    op.add_column("isolates", sa.Column("network_enabled", sa.Boolean()))
    op.add_column("isolates", sa.Column("internet_only", sa.Boolean()))
    op.create_index("ix_computers_device_id", "computers", ["device_id"])
    op.create_table(
        "physical_devices",
        sa.Column("identity_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_enrollments",
        sa.Column("actor_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("installation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acme_secret", sa.String(length=64), nullable=False),
        sa.Column("account_jwk", sa.JSON()),
        sa.Column("order_created", sa.Boolean(), nullable=False),
        sa.Column("challenge_token", sa.String(length=64), nullable=False),
        sa.Column("attested_key", sa.LargeBinary()),
        sa.Column("identity_hash", sa.String(length=64)),
        sa.Column("certificate_sha256", sa.String(length=64)),
        sa.Column("csr_sha256", sa.String(length=64)),
        sa.Column("computer_id", sa.Uuid(as_uuid=False)),
        sa.Column("credential_encrypted", sa.LargeBinary()),
        sa.Column("finish_key", sa.String(length=128)),
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_enrollments_actor_id", "agent_enrollments", ["actor_id"])
    op.create_index("ix_agent_enrollments_expires_at", "agent_enrollments", ["expires_at"])
    op.create_table(
        "device_certificates",
        sa.Column("sha256", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("physical_devices.id"),
            nullable=False,
        ),
        sa.Column(
            "enrollment_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("agent_enrollments.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("pem", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "protocol_nonces",
        sa.Column("namespace", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("value", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_protocol_nonces_expires_at", "protocol_nonces", ["expires_at"])
    op.create_table(
        "agent_mutations",
        sa.Column("credential_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("operation", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=False)),
        sa.Column("certificate_sha256", sa.String(length=64)),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_mutations_expires_at", "agent_mutations", ["expires_at"])
    op.create_table(
        "agent_diagnostics",
        sa.Column("user_id", sa.Uuid(as_uuid=False)),
        sa.Column("company_id", sa.Uuid(as_uuid=False)),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND company_id IS NULL) OR (user_id IS NULL AND company_id IS NOT NULL)",
            name="exactly_one_owner",
        ),
        sa.Column("computer_id", sa.Uuid(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=False)),
        sa.Column("task_id", sa.Uuid(as_uuid=False)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_agent_diagnostics_expires_at", "agent_diagnostics", ["expires_at"])


def downgrade():
    op.drop_table("agent_diagnostics")
    op.drop_table("agent_mutations")
    op.drop_table("protocol_nonces")
    op.drop_table("device_certificates")
    op.drop_table("agent_enrollments")
    op.drop_table("physical_devices")
    op.drop_index("ix_computers_device_id", table_name="computers")
    op.drop_column("computers", "certificate_sha256")
    op.drop_column("computers", "device_id")
    op.drop_column("computers", "internet_only")
    op.drop_column("computers", "network_enabled")
    op.drop_column("computers", "isolation_modes")
    op.drop_column("computers", "default_isolation")
    op.drop_column("computers", "target_disk")
    op.drop_column("computers", "target_memory")
    op.drop_column("computers", "target_cpu")
    op.drop_column("computers", "capacity_report")
    op.drop_column("computers", "capacity_revision")
    op.drop_column("computers", "accepting_work")
    op.drop_column("isolates", "internet_only")
    op.drop_column("isolates", "network_enabled")
    op.drop_column("isolates", "retry_allowed")
    op.drop_column("isolates", "effective_isolation")
    op.drop_column("isolates", "isolation_mode")
