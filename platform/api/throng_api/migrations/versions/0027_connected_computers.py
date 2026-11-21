"""Record computers connected through an account sign-in.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

from throng_api.migrations.billing import billing_migration_schema, referenced_owner_schema

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

INDEX = "ix_computers_connected_account_order"
ACCOUNT_FK = "fk_computers_connected_user_id_users"
LEGACY_FK = "fk_computers_legacy_transfer_user_id_users"
PENDING_FK = "fk_agent_credentials_pending_connected_user_id_users"


def historical_connections(computers, changes, detachments, schema):
    intervals = sa.table(
        "capacity_intervals",
        sa.column("computer_id"),
        sa.column("user_id"),
        sa.column("company_id"),
        sa.column("starts_at"),
        sa.column("ends_at"),
        schema=schema,
    )
    first_moves = (
        sa.select(
            changes.c.computer_id,
            changes.c.before_company_id,
            sa.func.row_number()
            .over(partition_by=changes.c.computer_id, order_by=changes.c.revision)
            .label("position"),
        )
        .where(changes.c.reason == "ownership_changed")
        .subquery()
    )
    company_actors = (
        sa.select(
            changes.c.computer_id,
            changes.c.after_company_id.label("company_id"),
            changes.c.actor_id.label("user_id"),
        )
        .where(changes.c.reason == "ownership_changed", changes.c.after_company_id.is_not(None))
        .union(
            sa.select(
                first_moves.c.computer_id,
                first_moves.c.before_company_id,
                computers.c.registered_by_user_id,
            )
            .join(computers, computers.c.id == first_moves.c.computer_id)
            .where(first_moves.c.position == 1, first_moves.c.before_company_id.is_not(None))
        )
        .cte("historical_company_actors")
    )
    # Availability proves a signed device session in that pool. Some older
    # visits have only one known actor, even if no transfer time was recorded.
    other_actor = company_actors.alias()
    companies = (
        sa.select(company_actors)
        .where(
            ~sa.exists(
                sa.select(1).where(
                    other_actor.c.computer_id == company_actors.c.computer_id,
                    other_actor.c.company_id == company_actors.c.company_id,
                    other_actor.c.user_id.is_distinct_from(company_actors.c.user_id),
                )
            )
        )
        .subquery()
    )
    moves = (
        sa.select(
            changes.c.computer_id,
            changes.c.after_company_id.label("company_id"),
            changes.c.after_user_id.label("user_id"),
            changes.c.actor_id,
            changes.c.revision,
            changes.c.created_at,
        )
        .where(changes.c.reason == "ownership_changed")
        .distinct()
        .cte("historical_pool_moves")
    )
    visit = moves.alias("signed_pool_visit")
    revision_at_start = (
        sa.select(sa.func.max(moves.c.revision))
        .where(
            moves.c.computer_id == intervals.c.computer_id,
            moves.c.created_at <= intervals.c.starts_at,
        )
        .correlate(intervals)
        .scalar_subquery()
    )
    same_time = moves.alias("concurrent_pool_visits")
    ambiguous = sa.exists(
        sa.select(1).where(
            same_time.c.computer_id == visit.c.computer_id,
            same_time.c.company_id == visit.c.company_id,
            same_time.c.created_at == visit.c.created_at,
            same_time.c.actor_id.is_distinct_from(visit.c.actor_id),
        )
    )

    removals = (
        sa.select(changes.c.computer_id, changes.c.revision, changes.c.created_at)
        .where(changes.c.reason == "deleted")
        .distinct()
        .cte("resource_removals")
    )
    resource = changes.alias("resource_at_removal")
    versions = changes.alias("resource_versions_at_removal")
    current_version = (
        sa.select(sa.func.max(versions.c.version))
        .where(
            versions.c.computer_resource_id == resource.c.computer_resource_id,
            versions.c.revision <= removals.c.revision,
        )
        .correlate(resource, removals)
        .scalar_subquery()
    )
    has_live_resource = sa.exists(
        sa.select(1).where(
            resource.c.computer_id == removals.c.computer_id,
            resource.c.version == current_version,
            resource.c.reason != "deleted",
        )
    )
    full_archives = sa.select(removals).where(~has_live_resource).cte("fully_archived_computers")

    def interrupted_visit(entered, observed_at):
        changed_owner = sa.exists(
            sa.select(1).where(
                changes.c.computer_id == entered.c.computer_id,
                changes.c.revision > entered.c.revision,
                changes.c.created_at <= observed_at,
                sa.or_(
                    changes.c.after_company_id.is_distinct_from(entered.c.company_id),
                    changes.c.after_user_id.is_distinct_from(entered.c.user_id),
                ),
            )
        )
        # Legacy transfers wrote a move as long as any resource was active.
        missing_history = sa.exists(
            sa.select(1).where(
                full_archives.c.computer_id == entered.c.computer_id,
                full_archives.c.revision > entered.c.revision,
                full_archives.c.created_at <= observed_at,
            )
        )
        return sa.or_(changed_owner, missing_history)

    visit_actor = sa.case(
        (sa.and_(~ambiguous, ~interrupted_visit(visit, intervals.c.starts_at)), visit.c.actor_id)
    )
    account = sa.func.coalesce(intervals.c.user_id, companies.c.user_id, visit_actor)
    published_capacity = (
        sa.select(
            intervals.c.computer_id,
            account.label("user_id"),
            intervals.c.starts_at.label("observed_at"),
            intervals.c.ends_at.label("ends_at"),
        )
        .select_from(
            intervals.outerjoin(
                companies,
                sa.and_(
                    companies.c.computer_id == intervals.c.computer_id,
                    companies.c.company_id == intervals.c.company_id,
                ),
            ).outerjoin(
                visit,
                sa.and_(
                    visit.c.computer_id == intervals.c.computer_id,
                    visit.c.company_id == intervals.c.company_id,
                    visit.c.revision == revision_at_start,
                ),
            )
        )
        .where(account.is_not(None))
    )
    # A signed lease also updates last_seen_at before the agent offers capacity.
    # Its account is identifiable when the observation is between recorded moves.
    last_seen = computers.c.last_seen_at
    last_visit = moves.alias("last_seen_pool_visit")
    seen_revision = (
        sa.select(sa.func.max(moves.c.revision))
        .where(moves.c.computer_id == computers.c.id, moves.c.created_at < last_seen)
        .correlate(computers)
        .scalar_subquery()
    )
    on_move_boundary = sa.exists(
        sa.select(1).where(moves.c.computer_id == computers.c.id, moves.c.created_at == last_seen)
    )
    seen_account = sa.case(
        (last_visit.c.company_id.is_not(None), last_visit.c.actor_id),
        else_=last_visit.c.user_id,
    )
    signed_presence = (
        sa.select(computers.c.id, seen_account, last_seen, last_seen)
        .select_from(
            computers.join(
                last_visit,
                sa.and_(
                    last_visit.c.computer_id == computers.c.id,
                    last_visit.c.revision == seen_revision,
                ),
            )
        )
        .where(
            seen_account.is_not(None), ~on_move_boundary, ~interrupted_visit(last_visit, last_seen)
        )
    )
    observations = published_capacity.union_all(signed_presence).subquery()
    evidence = (
        sa.select(
            observations.c.computer_id,
            observations.c.user_id,
            sa.func.dense_rank()
            .over(
                partition_by=observations.c.computer_id,
                order_by=(observations.c.observed_at.desc(), observations.c.ends_at.desc()),
            )
            .label("position"),
        )
        .select_from(
            observations.outerjoin(
                detachments, detachments.c.computer_id == observations.c.computer_id
            )
        )
        .where(
            sa.or_(
                detachments.c.detached_at.is_(None),
                observations.c.observed_at > detachments.c.detached_at,
            )
        )
        .subquery()
    )
    latest = (
        sa.select(evidence.c.computer_id, evidence.c.user_id)
        .where(evidence.c.position == 1)
        .distinct()
        .cte("last_signed_pool_accounts")
    )
    other = latest.alias()
    return (
        sa.select(latest)
        .where(
            ~sa.exists(
                sa.select(1).where(
                    other.c.computer_id == latest.c.computer_id, other.c.user_id != latest.c.user_id
                )
            )
        )
        .subquery()
    )


def upgrade():
    connection = op.get_bind()
    with billing_migration_schema(connection, op.get_context(), lock_timeout="1ms") as (schema, _):
        user_schema = referenced_owner_schema(
            connection, schema, "computers", "registered_by_user_id"
        )
        with op.batch_alter_table("computers", schema=schema) as batch:
            batch.add_column(sa.Column("connected_user_id", sa.Uuid(as_uuid=False)))
            batch.add_column(sa.Column("legacy_transfer_user_id", sa.Uuid(as_uuid=False)))
            batch.add_column(
                sa.Column(
                    "legacy_transfer_pending",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
            batch.create_foreign_key(
                ACCOUNT_FK,
                "users",
                ["connected_user_id"],
                ["id"],
                referent_schema=user_schema,
                ondelete="SET NULL",
            )
            batch.create_foreign_key(
                LEGACY_FK,
                "users",
                ["legacy_transfer_user_id"],
                ["id"],
                referent_schema=user_schema,
                ondelete="SET NULL",
            )
        with op.batch_alter_table("agent_credentials", schema=schema) as batch:
            batch.add_column(sa.Column("pending_connected_user_id", sa.Uuid(as_uuid=False)))
            batch.create_foreign_key(
                PENDING_FK,
                "users",
                ["pending_connected_user_id"],
                ["id"],
                referent_schema=user_schema,
                ondelete="SET NULL",
            )
        op.add_column(
            "computer_ownerships",
            sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
            schema=schema,
        )
        computers = sa.table(
            "computers",
            sa.column("id"),
            sa.column("registered_by_user_id"),
            sa.column("certificate_sha256"),
            sa.column("session_id"),
            sa.column("last_seen_at"),
            sa.column("connected_user_id"),
            sa.column("legacy_transfer_user_id"),
            sa.column("legacy_transfer_pending"),
            schema=schema,
        )
        enrollments = sa.table(
            "agent_enrollments",
            sa.column("id"),
            sa.column("computer_id"),
            sa.column("actor_id"),
            schema=schema,
        )
        certificates = sa.table(
            "device_certificates",
            sa.column("sha256"),
            sa.column("enrollment_id"),
            sa.column("revoked_at"),
            schema=schema,
        )
        ownerships = sa.table(
            "computer_ownerships",
            sa.column("computer_id"),
            sa.column("user_id"),
            sa.column("company_id"),
            schema=schema,
        )
        credentials = sa.table(
            "agent_credentials",
            sa.column("computer_id"),
            sa.column("pending_connected_user_id"),
            schema=schema,
        )
        changes = sa.table(
            "computer_resource_changes",
            sa.column("computer_id"),
            sa.column("computer_resource_id"),
            sa.column("before_user_id"),
            sa.column("before_company_id"),
            sa.column("after_user_id"),
            sa.column("after_company_id"),
            sa.column("actor_id"),
            sa.column("reason"),
            sa.column("version"),
            sa.column("revision"),
            sa.column("created_at"),
            schema=schema,
        )
        users = sa.table("users", sa.column("id"), schema=user_schema)
        position = (
            sa.func.row_number()
            .over(partition_by=changes.c.computer_id, order_by=changes.c.revision.desc())
            .label("position")
        )
        last_moves = (
            sa.select(
                changes.c.computer_id,
                changes.c.before_user_id,
                changes.c.before_company_id,
                changes.c.after_company_id,
                changes.c.after_user_id,
                changes.c.actor_id,
                changes.c.revision,
                position,
            )
            .where(changes.c.reason == "ownership_changed")
            .subquery()
        )
        last_resource_owner = sa.select(
            changes.c.computer_id, changes.c.after_user_id, changes.c.after_company_id, position
        ).subquery()
        # A recorded detach ends the earlier sign-in association. Later owner
        # assignments and issued keys alone don't renew it.
        detachments = (
            sa.select(changes.c.computer_id, sa.func.max(changes.c.created_at).label("detached_at"))
            .where(
                changes.c.reason == "ownership_changed",
                changes.c.after_user_id.is_(None),
                changes.c.after_company_id.is_(None),
                sa.or_(
                    changes.c.before_user_id.is_not(None), changes.c.before_company_id.is_not(None)
                ),
            )
            .group_by(changes.c.computer_id)
            .cte("recorded_detachments")
        )
        historical = historical_connections(computers, changes, detachments, schema)
        never_detached = detachments.c.detached_at.is_(None)
        # Detachment clears the session ID. A saved session is newer even if
        # its wall clock timestamp is older than the monotonic resource ledger.
        current_owner_evidence = sa.or_(never_detached, computers.c.session_id.is_not(None))
        # Legacy handoffs revoked the credential and only became usable
        # again after issuing a new key and authenticating with the bound certificate.
        company_actor = users.alias("company_handoff_actor")
        later = changes.alias("later_owner_observations")
        observed_another_owner = sa.exists(
            sa.select(1).where(
                later.c.computer_id == computers.c.id,
                later.c.revision > last_moves.c.revision,
                sa.or_(
                    later.c.after_user_id.is_distinct_from(ownerships.c.user_id),
                    later.c.after_company_id.is_distinct_from(ownerships.c.company_id),
                ),
            )
        )
        moved_to_current_pool = sa.and_(
            sa.or_(
                last_moves.c.before_user_id.is_not(None),
                last_moves.c.before_company_id.is_not(None),
            ),
            sa.or_(
                last_moves.c.after_company_id == ownerships.c.company_id,
                last_moves.c.after_user_id == ownerships.c.user_id,
            ),
            ~observed_another_owner,
        )
        # The legacy ownership update removed the old key in its transaction.
        # An existing key therefore follows the handoff even if the clock was corrected.
        replacement_issued = credentials.c.computer_id.is_not(None)
        completed_handoff = sa.case(
            (
                sa.and_(
                    moved_to_current_pool,
                    replacement_issued,
                    computers.c.session_id.is_not(None),
                ),
                last_moves.c.actor_id,
            )
        )
        unfinished_handoff = sa.and_(
            moved_to_current_pool,
            sa.or_(
                credentials.c.computer_id.is_(None),
                computers.c.session_id.is_(None),
            ),
        )
        pending_personal_owner = sa.and_(
            ownerships.c.user_id != computers.c.registered_by_user_id,
            computers.c.session_id.is_(None),
        )
        # When all resources are archived, the ledger can still show the
        # previous pool of a device that never opened an agent session.
        archived_handoff = sa.and_(
            last_resource_owner.c.computer_id.is_not(None),
            sa.or_(
                last_resource_owner.c.after_user_id.is_distinct_from(ownerships.c.user_id),
                last_resource_owner.c.after_company_id.is_distinct_from(ownerships.c.company_id),
            ),
        )
        pending_connection = sa.and_(
            computers.c.session_id.is_(None),
            sa.or_(
                unfinished_handoff,
                pending_personal_owner,
                computers.c.last_seen_at.is_not(None),
                archived_handoff,
            ),
        )
        account = sa.func.coalesce(
            sa.case(
                (sa.or_(unfinished_handoff, pending_personal_owner), None),
                (current_owner_evidence, ownerships.c.user_id),
            ),
            completed_handoff,
            historical.c.user_id,
            sa.case((never_detached, computers.c.registered_by_user_id)),
        )
        # Older versions did not preserve what kind of account credential was used.
        # Reconstruct completed personal and organization handoffs for signed bindings.
        bound = (
            sa.select(
                computers.c.id.label("computer_id"),
                account.label("user_id"),
                sa.case(
                    (pending_personal_owner, ownerships.c.user_id),
                    (unfinished_handoff, company_actor.c.id),
                ).label("pending_user_id"),
                sa.func.coalesce(pending_connection, sa.false()).label("pending_connection"),
            )
            .select_from(
                computers.join(
                    certificates, certificates.c.sha256 == computers.c.certificate_sha256
                )
                .join(enrollments, certificates.c.enrollment_id == enrollments.c.id)
                .outerjoin(ownerships, ownerships.c.computer_id == computers.c.id)
                .outerjoin(credentials, credentials.c.computer_id == computers.c.id)
                .outerjoin(detachments, detachments.c.computer_id == computers.c.id)
                .outerjoin(
                    last_moves,
                    sa.and_(last_moves.c.computer_id == computers.c.id, last_moves.c.position == 1),
                )
                .outerjoin(company_actor, company_actor.c.id == last_moves.c.actor_id)
                .outerjoin(historical, historical.c.computer_id == computers.c.id)
                .outerjoin(
                    last_resource_owner,
                    sa.and_(
                        last_resource_owner.c.computer_id == computers.c.id,
                        last_resource_owner.c.position == 1,
                    ),
                )
            )
            .where(
                enrollments.c.computer_id == computers.c.id,
                enrollments.c.actor_id == computers.c.registered_by_user_id,
                certificates.c.revoked_at.is_(None),
            )
            .cte("connected_accounts")
        )
        op.execute(
            computers.update()
            .where(computers.c.id == bound.c.computer_id)
            .values(
                # Apply existence checks after ranking so account deletion
                # cannot restore an earlier user's connection.
                connected_user_id=sa.select(users.c.id)
                .where(users.c.id == bound.c.user_id)
                .correlate(bound)
                .scalar_subquery(),
                legacy_transfer_user_id=bound.c.pending_user_id,
                legacy_transfer_pending=bound.c.pending_connection,
            )
        )
        # Keep a pre-upgrade replacement key usable even if the native app is
        # offline or has not yet recovered a lost credential response.
        op.execute(
            credentials.update()
            .where(
                credentials.c.computer_id == computers.c.id,
                computers.c.legacy_transfer_user_id.is_not(None),
            )
            .values(pending_connected_user_id=computers.c.legacy_transfer_user_id)
        )
        op.create_index(
            INDEX,
            "computers",
            ["connected_user_id", "created_at", "id"],
            schema=schema,
        )


def downgrade():
    with billing_migration_schema(op.get_bind(), op.get_context(), lock_timeout="1ms") as (
        schema,
        _,
    ):
        op.drop_index(INDEX, table_name="computers", schema=schema)
        op.drop_column("computer_ownerships", "revision", schema=schema)
        with op.batch_alter_table("agent_credentials", schema=schema) as batch:
            batch.drop_constraint(PENDING_FK, type_="foreignkey")
            batch.drop_column("pending_connected_user_id")
        with op.batch_alter_table("computers", schema=schema) as batch:
            batch.drop_constraint(ACCOUNT_FK, type_="foreignkey")
            batch.drop_constraint(LEGACY_FK, type_="foreignkey")
            batch.drop_column("connected_user_id")
            batch.drop_column("legacy_transfer_user_id")
            batch.drop_column("legacy_transfer_pending")
