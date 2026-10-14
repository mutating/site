"""Organization member permissions and single-use invitations.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memberships", sa.Column("permissions", sa.JSON(), nullable=True))
    op.create_table(
        "company_invitations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("invitee_user_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("accepted_by_user_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.CheckConstraint("role IN ('creator', 'admin', 'user')", name="valid_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked')", name="valid_status"
        ),
    )
    for column in ["company_id", "invitee_user_id", "email"]:
        op.create_index(f"ix_company_invitations_{column}", "company_invitations", [column])


def downgrade():
    op.drop_table("company_invitations")
    op.drop_column("memberships", "permissions")
