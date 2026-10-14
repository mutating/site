"""Optional nicknames and bounded profile avatars.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column("nickname", existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column("avatar_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("avatar_version", sa.String(64), nullable=True))


def downgrade():
    # Preserve all users when the previous contract requires a nickname.
    users = sa.table(
        "users", sa.column("id", sa.Uuid(as_uuid=False)), sa.column("nickname", sa.String(64))
    )
    bind = op.get_bind()
    for user_id in bind.scalars(sa.select(users.c.id).where(users.c.nickname.is_(None))):
        from uuid import uuid4

        while True:
            nickname = "user_" + uuid4().hex
            if not bind.scalar(sa.select(users.c.id).where(users.c.nickname == nickname)):
                break
        bind.execute(users.update().where(users.c.id == user_id).values(nickname=nickname))
    with op.batch_alter_table("users") as batch:
        batch.drop_column("avatar_version")
        batch.drop_column("avatar_data")
        batch.alter_column("nickname", existing_type=sa.String(64), nullable=False)
