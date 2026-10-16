"""GitHub account connection and one-time OAuth state."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_accounts",
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("tokens", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("refresh_nonce", sa.String(64)),
        sa.Column("refresh_until", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "github_oauth_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_oauth_flows_user_id", "github_oauth_flows", ["user_id"])
    op.create_index("ix_github_oauth_flows_expires_at", "github_oauth_flows", ["expires_at"])
    create_ci_tables()


def downgrade():
    op.drop_table("github_deliveries")
    op.drop_table("ci_reservations")
    op.drop_table("ci_routes")
    op.drop_table("github_oauth_flows")
    op.drop_table("github_accounts")


def create_ci_tables():
    def identity():
        return [
            sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        ]

    op.create_table(
        "ci_routes",
        *identity(),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("company_id", sa.Uuid(as_uuid=False), sa.ForeignKey("companies.id")),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND company_id IS NULL) OR (user_id IS NULL AND company_id IS NOT NULL)",
            name="exactly_one_owner",
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_owner_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(250), nullable=False),
        sa.Column("workflow_path", sa.String(250), nullable=False),
        sa.Column("ref", sa.String(250), nullable=False),
        sa.Column("job_key", sa.String(100), nullable=False),
        sa.Column("computer_ids", sa.JSON(), nullable=False),
        sa.Column("cpu_millis", sa.Integer(), nullable=False),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=False),
        sa.Column("preparation_seconds", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_parallel", sa.Integer(), nullable=False),
        sa.Column("fallback", sa.String(100), nullable=False),
        sa.Column(
            "state_artifact_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    for field in ("user_id", "company_id", "created_by"):
        op.create_index(f"ix_ci_routes_{field}", "ci_routes", [field])
    op.create_table(
        "ci_reservations",
        *identity(),
        sa.Column(
            "route_id", sa.Uuid(as_uuid=False), sa.ForeignKey("ci_routes.id"), nullable=False
        ),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("instance", sa.String(100), nullable=False),
        sa.UniqueConstraint("route_id", "run_id", "run_attempt", "instance"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column("label", sa.String(100), nullable=False, unique=True),
        sa.Column("isolate_id", sa.Uuid(as_uuid=False), sa.ForeignKey("isolates.id"), unique=True),
        sa.Column("command_id", sa.Uuid(as_uuid=False), sa.ForeignKey("commands.id"), unique=True),
        sa.Column("runner_id", sa.BigInteger()),
        sa.Column("credentials", sa.Text()),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fallback", sa.String(100), nullable=False),
        sa.Column("lease_nonce", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_check", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
    )
    for field in ("route_id", "status"):
        op.create_index(f"ix_ci_reservations_{field}", "ci_reservations", [field])
    op.create_table(
        "github_deliveries",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_deliveries_created_at", "github_deliveries", ["created_at"])
