"""Retire unallocated work left by the former CI capacity queue.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE ci_reservations
        SET status = 'failed', reason = 'no_capacity', credentials = NULL,
            finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
            next_check = CURRENT_TIMESTAMP
        WHERE status = 'preparing' AND (
            isolate_id IS NULL OR isolate_id IN (
                SELECT id FROM isolates WHERE computer_id IS NULL
            )
        )
    """)
    op.execute("""
        UPDATE isolates
        SET status = 'destroyed', destroyed_at = COALESCE(destroyed_at, CURRENT_TIMESTAMP)
        WHERE status = 'provisioning' AND computer_id IS NULL AND reserved = FALSE
          AND id IN (SELECT isolate_id FROM ci_reservations WHERE isolate_id IS NOT NULL)
    """)


def downgrade():
    # Retired work must not resume when an older API is restored.
    pass
