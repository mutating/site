from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from typing import Literal, Self
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary, ref

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    event,
    false,
    inspect,
    or_,
    select,
    table,
    text,
    update,
)
from sqlalchemy import (
    column as sql_column,
)
from sqlalchemy.engine import Engine
from sqlalchemy.engine.base import OptionEngine
from sqlalchemy.exc import DBAPIError, InvalidRequestError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import (
    DeclarativeBase,
    FromStatement,
    Mapped,
    MapperProperty,
    ORMExecuteState,
    Session,
    mapped_column,
    relationship,
    validates,
)
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.state import InstanceState
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.elements import ClauseElement, ClauseList, Grouping, Null
from sqlalchemy.sql.selectable import (
    CompoundSelect,
    SelectBase,
    SelectStatementGrouping,
    TextualSelect,
)
from sqlalchemy.types import TypeDecorator, TypeEngine

from throng_api.account_tariff_schema import (
    PUBLICATION_STORAGE_CHECKS,
    PUBLICATION_UNIQUE,
    PostgreSQLExactTimestamp,
    normalize_utc,
    stored_utc,
)
from throng_api.billing_schema import (
    FIELD_DOCUMENTATION_TABLE,
    billing_text_check,
    billing_uuid_check,
    field_documentation_checks,
)
from throng_api.first_tariff_schema import register_first_tariff_triggers
from throng_api.organization_names import name_key, normalize_name
from throng_api.tariff_documentation_schema import (
    register_billing_index_creation,
    register_field_documentation_triggers,
    sqlite_documentation_lookup_schema,
    sqlite_documentation_schema,
    sqlite_documentation_storage,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return normalize_utc(value)

    def process_result_value(self, value, dialect):
        return stored_utc(value)


PUBLICATION_TIMESTAMP = UTCDateTime().with_variant(PostgreSQLExactTimestamp(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Identity:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SQLiteStoredOwnerKey(str):
    """A UUID whose hyphens are part of an existing SQLite primary key."""


class SQLiteOwnerUUID(Uuid):
    """Keep distinct physical SQLite owner keys distinct in the ORM."""

    def bind_processor(self, dialect):
        bind = super().bind_processor(dialect)

        def bind_key(value):
            if isinstance(value, SQLiteStoredOwnerKey):
                return value.removeprefix("urn:uuid:")
            return bind(value)

        return bind_key

    def result_processor(self, dialect, coltype):
        normalize = super().result_processor(dialect, coltype)

        def read_key(value):
            normalized = normalize(value)
            if value is None or value == normalized.replace("-", ""):
                return normalized
            if "-" in value:
                # Distinguish a raw dashed key from the dashed display of a compact key.
                return SQLiteStoredOwnerKey(f"urn:uuid:{value}")
            if len(value) != 32:
                return value
            return "-".join((value[:8], value[8:12], value[12:16], value[16:20], value[20:]))

        return read_key


class OwnerIdentity(Identity):
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False).with_variant(SQLiteOwnerUUID(as_uuid=False), "sqlite"),
        primary_key=True,
        default=new_id,
    )


class Ownership:
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)


def owner_check():
    return CheckConstraint(
        "(user_id IS NOT NULL AND company_id IS NULL) OR "
        "(user_id IS NULL AND company_id IS NOT NULL)",
        name="exactly_one_owner",
    )


class Coordination(Base):
    """A transactional lock shared by all API/controller processes in the MVP."""

    __tablename__ = "coordination"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)


class User(OwnerIdentity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    name_search: Mapped[str] = mapped_column(Text, server_default="", deferred=True)
    nickname: Mapped[str | None] = mapped_column(String(64), unique=True)
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_version: Mapped[str | None] = mapped_column(String(64))
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    @validates("first_name", "last_name")
    def prepare_name(self, key, value):
        first_name = value if key == "first_name" else self.first_name
        last_name = value if key == "last_name" else self.last_name
        self.name_search = normalize_name(" ".join(f"{first_name or ''} {last_name or ''}".split()))
        return value


class Company(OwnerIdentity, Base):
    __tablename__ = "companies"
    name: Mapped[str] = mapped_column(String(200))
    name_search: Mapped[str] = mapped_column(Text, server_default="")
    name_key: Mapped[str] = mapped_column(Text, server_default="")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    background_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_version: Mapped[str | None] = mapped_column(String(64))
    background_version: Mapped[str | None] = mapped_column(String(64))

    @validates("name")
    def prepare_name(self, _, value):
        self.name_search = normalize_name(value)
        self.name_key = name_key(value)
        return value


class Membership(Identity, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id"),
        CheckConstraint("role IN ('creator', 'admin', 'user')", name="valid_role"),
        Index("ix_memberships_company_order", "company_id", "id"),
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    role: Mapped[str] = mapped_column(String(16))
    permissions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class CompanyInvitation(Identity, Base):
    """Single-use links. IDs remain as history after accounts or companies are removed."""

    __tablename__ = "company_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('creator', 'admin', 'user')", name="valid_role"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked')", name="valid_status"
        ),
    )
    company_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    company_name: Mapped[str] = mapped_column(String(200))
    created_by_user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    invitee_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(16))
    permissions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), default="pending")
    accepted_by_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


Index(
    "ix_company_invitations_company_order",
    CompanyInvitation.company_id,
    CompanyInvitation.created_at.desc(),
    CompanyInvitation.id,
)


class TokenOwnership(Identity, Ownership, Base):
    __tablename__ = "token_ownerships"
    __table_args__ = (owner_check(),)


class TokenCredential(Identity):
    ownership_id: Mapped[str] = mapped_column(ForeignKey("token_ownerships.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(16), default="api", server_default="api")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AccessToken(TokenCredential, Base):
    __tablename__ = "access_tokens"
    __table_args__ = (CheckConstraint("purpose = 'api'", name="token_purpose"),)


class LegacyCredential(TokenCredential, Base):
    """Historical credentials keep their owner and scopes; personal ones follow execution policy."""

    __tablename__ = "legacy_credentials"
    __table_args__ = (CheckConstraint("purpose IN ('login', 'legacy')", name="credential_purpose"),)
    purpose: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LoginSession(Identity, Base):
    __tablename__ = "login_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="login")
    scopes: Mapped[list] = mapped_column(JSON)
    session_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Computer(Identity, Base):
    __tablename__ = "computers"
    __table_args__ = (
        CheckConstraint("disk_bytes > 0", name="positive_capacity"),
        CheckConstraint("reserved_disk >= 0", name="nonnegative_reservation"),
        CheckConstraint("reserved_disk <= disk_bytes", name="reservation_within_capacity"),
        Index(
            "ix_computers_connected_account_order",
            "connected_user_id",
            "created_at",
            "id",
        ),
    )
    name: Mapped[str | None] = mapped_column(String(200))
    registered_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    connected_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    os: Mapped[str] = mapped_column(String(40))
    architecture: Mapped[str] = mapped_column(String(40))
    runtime: Mapped[str] = mapped_column(String(80))
    disk_bytes: Mapped[int] = mapped_column(BigInteger)
    reserved_disk: Mapped[int] = mapped_column(BigInteger, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    # Inventory remains a hard upper bound. A session's offered target may fall below reservations.
    accepting_work: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    capacity_revision: Mapped[int] = mapped_column(BigInteger, default=-1, server_default="-1")
    capacity_report: Mapped[dict | None] = mapped_column(JSON)
    target_cpu: Mapped[int | None] = mapped_column(BigInteger)
    target_memory: Mapped[int | None] = mapped_column(BigInteger)
    target_disk: Mapped[int | None] = mapped_column(BigInteger)
    default_isolation: Mapped[str] = mapped_column(String(8), default="vm", server_default="vm")
    isolation_modes: Mapped[list | None] = mapped_column(JSON)
    network_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    internet_only: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id"), index=True)
    resources: Mapped[list["ComputerResource"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def can_start_work(self):
        return self.accepting_work and all(
            target is None or reserved <= target
            for target, reserved in (
                (self.target_cpu, self.reserved_cpu),
                (self.target_memory, self.reserved_memory),
                (self.target_disk, self.reserved_disk),
            )
        )

    def resource(self, resource_type_id):
        return next(
            (
                row
                for row in self.resources
                if row.resource_type_id == resource_type_id and row.deleted_at is None
            ),
            None,
        )

    @property
    def cpu_millis(self):
        row = self.resource(CPU_RESOURCE_ID)
        return row.total if row else 0

    @property
    def memory_bytes(self):
        row = self.resource(MEMORY_RESOURCE_ID)
        return row.total if row else 0

    @property
    def reserved_cpu(self):
        row = self.resource(CPU_RESOURCE_ID)
        return row.occupied if row else 0

    @property
    def reserved_memory(self):
        row = self.resource(MEMORY_RESOURCE_ID)
        return row.occupied if row else 0


CPU_RESOURCE_ID = "00000000-0000-0000-0000-000000000001"
MEMORY_RESOURCE_ID = "00000000-0000-0000-0000-000000000002"


class ResourceType(Identity, Base):
    __tablename__ = "resource_types"
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str] = mapped_column(String(64), unique=True)
    unit: Mapped[str] = mapped_column(String(40))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BenchmarkResult(Identity, Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (
        UniqueConstraint("computer_id", "benchmark_name", "run_id"),
        CheckConstraint("number >= 0", name="nonnegative_score"),
    )
    computer_id: Mapped[str] = mapped_column(
        ForeignKey("computers.id", ondelete="CASCADE"), index=True
    )
    benchmark_name: Mapped[str] = mapped_column(String(200))
    number: Mapped[float] = mapped_column(Float)
    benchmarker_version: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), default=new_id)
    pi_equivalent: Mapped[float | None] = mapped_column(Float)
    reference_machine: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    reference_run: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    aggregated_scores: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))


class BenchmarkAggregateInvalidation(Base):
    """Remember an invalidated aggregate even after all run results are deleted."""

    __tablename__ = "benchmark_aggregate_invalidations"
    computer_id: Mapped[str] = mapped_column(
        ForeignKey("computers.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)


class ComputerResource(Identity, Base):
    __tablename__ = "computer_resources"
    __table_args__ = (
        UniqueConstraint("computer_id", "resource_type_id"),
        CheckConstraint("total >= 0 AND total <= 1152921504606846976", name="valid_total"),
        CheckConstraint("occupied >= 0 AND occupied <= total", name="valid_occupation"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("deleted_at IS NULL OR occupied = 0", name="deleted_unoccupied"),
    )
    computer_id: Mapped[str] = mapped_column(
        ForeignKey("computers.id", ondelete="CASCADE"), index=True
    )
    resource_type_id: Mapped[str] = mapped_column(ForeignKey("resource_types.id"), index=True)
    total: Mapped[int] = mapped_column(BigInteger)
    occupied: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ComputerResourceChange(Identity, Base):
    """Immutable snapshots: historical identities deliberately survive computer deletion."""

    __tablename__ = "computer_resource_changes"
    __table_args__ = (
        UniqueConstraint("computer_resource_id", "version"),
        Index("ix_resource_history_revision", "revision"),
        Index("ix_resource_history_created_at", "created_at"),
        Index("ix_resource_history_reason_created_at", "reason", "created_at"),
        Index("ix_resource_history_before_user", "before_user_id", "created_at"),
        Index("ix_resource_history_before_company", "before_company_id", "created_at"),
        Index("ix_resource_history_after_user", "after_user_id", "created_at"),
        Index("ix_resource_history_after_company", "after_company_id", "created_at"),
        CheckConstraint(
            "before_user_id IS NULL OR before_company_id IS NULL", name="before_single_owner"
        ),
        CheckConstraint(
            "after_user_id IS NULL OR after_company_id IS NULL", name="after_single_owner"
        ),
        CheckConstraint(
            "before_total >= 0 AND before_occupied >= 0 AND before_occupied <= before_total",
            name="valid_before",
        ),
        CheckConstraint(
            "after_total >= 0 AND after_occupied >= 0 AND after_occupied <= after_total",
            name="valid_after",
        ),
    )
    computer_resource_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    computer_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    resource_type_id: Mapped[str] = mapped_column(ForeignKey("resource_types.id"))
    version: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    before_total: Mapped[int] = mapped_column(BigInteger)
    before_occupied: Mapped[int] = mapped_column(BigInteger)
    after_total: Mapped[int] = mapped_column(BigInteger)
    after_occupied: Mapped[int] = mapped_column(BigInteger)
    before_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    before_company_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    after_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    after_company_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    actor_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    reason: Mapped[str] = mapped_column(String(32))


COMPANY_OWNERSHIP_CHANGED = ComputerResourceChange.before_company_id.is_distinct_from(
    ComputerResourceChange.after_company_id
)
USER_OWNERSHIP_CHANGED = ComputerResourceChange.before_user_id.is_distinct_from(
    ComputerResourceChange.after_user_id
)
INVENTORY_CHANGED = or_(
    ComputerResourceChange.before_total != ComputerResourceChange.after_total,
    USER_OWNERSHIP_CHANGED,
    COMPANY_OWNERSHIP_CHANGED,
)
for side, owner, changed in (
    ("before", ComputerResourceChange.before_company_id, INVENTORY_CHANGED),
    ("after", ComputerResourceChange.after_company_id, COMPANY_OWNERSHIP_CHANGED),
):
    predicate = and_(owner.is_not(None), changed)
    Index(
        f"ix_inventory_history_{side}_company_time",
        owner,
        ComputerResourceChange.created_at,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )


class ComputerOwnership(Identity, Ownership, Base):
    __tablename__ = "computer_ownerships"
    __table_args__ = (
        owner_check(),
        Index("ix_computer_ownerships_company_order", "company_id", "computer_id"),
    )
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    computer_id: Mapped[str] = mapped_column(ForeignKey("computers.id"), unique=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class AvailabilityBaseline(Base):
    __tablename__ = "availability_baseline"
    id: Mapped[int] = mapped_column(primary_key=True)
    available_from: Mapped[datetime] = mapped_column(UTCDateTime())


class CapacityInterval(Identity, Base):
    """Historical ownership and capacity; lease renewal extends an open interval."""

    __tablename__ = "capacity_intervals"
    __table_args__ = (
        Index("ix_capacity_intervals_user_time", "user_id", "ends_at"),
        Index("ix_capacity_intervals_company_time", "company_id", "ends_at"),
        CheckConstraint("ends_at >= starts_at", name="ordered_interval"),
        CheckConstraint("cpu_millis >= 0 AND memory_bytes >= 0", name="positive_capacity"),
        owner_check(),
    )
    computer_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    company_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    cpu_millis: Mapped[int] = mapped_column(BigInteger)
    memory_bytes: Mapped[int] = mapped_column(BigInteger)
    cpu_occupied_millis: Mapped[int] = mapped_column(BigInteger)
    memory_occupied_bytes: Mapped[int] = mapped_column(BigInteger)


class AgentCredential(Identity, Base):
    __tablename__ = "agent_credentials"
    computer_id: Mapped[str] = mapped_column(ForeignKey("computers.id"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pending_connected_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Artifact(Identity, Ownership, Base):
    __tablename__ = "artifacts"
    __table_args__ = (owner_check(),)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(36), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))


class Isolate(Identity, Ownership, Base):
    __tablename__ = "isolates"
    __table_args__ = (owner_check(),)
    status: Mapped[str] = mapped_column(String(24), default="provisioning", index=True)
    computer_id: Mapped[str | None] = mapped_column(ForeignKey("computers.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    state_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    image: Mapped[str] = mapped_column(String(300))
    os: Mapped[str] = mapped_column(String(40))
    architecture: Mapped[str] = mapped_column(String(40))
    runtime: Mapped[str] = mapped_column(String(80))
    cpu_millis: Mapped[int] = mapped_column(Integer)
    memory_bytes: Mapped[int] = mapped_column(BigInteger)
    disk_bytes: Mapped[int] = mapped_column(BigInteger)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    provisioning_deadline: Mapped[datetime] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    destroyed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_sequence: Mapped[int] = mapped_column(Integer, default=0)
    isolation_mode: Mapped[str | None] = mapped_column(String(8))
    effective_isolation: Mapped[str | None] = mapped_column(String(8))
    retry_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    network_enabled: Mapped[bool | None] = mapped_column(Boolean)
    internet_only: Mapped[bool | None] = mapped_column(Boolean)


class Device(Identity, Base):
    """Client-generated installation identity; survives account and computer deletion."""

    __tablename__ = "devices"
    client_key: Mapped[str] = mapped_column(String(64), unique=True)


class DeviceAccount(Base):
    __tablename__ = "device_accounts"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AgentEnrollment(Identity, Base):
    __tablename__ = "agent_enrollments"
    actor_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    request: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    computer_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    finish_key: Mapped[str | None] = mapped_column(String(128))


class AgentMutation(Base):
    """Exact bounded retries, including disconnect and revoked-credential unbinding."""

    __tablename__ = "agent_mutations"
    credential_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    response: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AgentDiagnostic(Base):
    __tablename__ = "agent_diagnostics"
    __table_args__ = (owner_check(),)
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    company_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    computer_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    task_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Bounded records in the database make acceptance/retention atomic with deduplication.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class Command(Identity, Base):
    __tablename__ = "commands"
    __table_args__ = (UniqueConstraint("isolate_id", "sequence"),)
    isolate_id: Mapped[str] = mapped_column(ForeignKey("isolates.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    command: Mapped[str] = mapped_column(Text)
    shell: Mapped[bool] = mapped_column(Boolean, default=False)
    cwd: Mapped[str] = mapped_column(String(1024), default=".")
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    queued_deadline: Mapped[datetime] = mapped_column(UTCDateTime())
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancel_reason: Mapped[str | None] = mapped_column(String(40))


class CommandResult(Base):
    __tablename__ = "command_results"
    command_id: Mapped[str] = mapped_column(ForeignKey("commands.id"), primary_key=True)
    success: Mapped[bool] = mapped_column(Boolean)
    returncode: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    stdout_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    stderr_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AgentTask(Identity, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_delivery", "computer_id", "session_id", "status"),)
    computer_id: Mapped[str] = mapped_column(ForeignKey("computers.id"))
    session_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    isolate_id: Mapped[str] = mapped_column(ForeignKey("isolates.id"), index=True)
    command_id: Mapped[str | None] = mapped_column(ForeignKey("commands.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completion_hash: Mapped[str | None] = mapped_column(String(64))
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    error: Mapped[str | None] = mapped_column(Text)


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), unique=True)


class Idempotency(Base):
    __tablename__ = "idempotency"
    principal: Mapped[str] = mapped_column(String(80), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class GitHubAccount(Base):
    """User tokens are encrypted; App credentials are kept in server configuration."""

    __tablename__ = "github_accounts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(100))
    tokens: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    refresh_nonce: Mapped[str | None] = mapped_column(String(64))
    refresh_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GitHubOAuthFlow(Base):
    """One-time state tied to the initiating user session, including installation flows."""

    __tablename__ = "github_oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class CIRoute(Identity, Ownership, Base):
    """An explicitly authorized repository/workflow/job to resource-pool binding."""

    __tablename__ = "ci_routes"
    __table_args__ = (owner_check(),)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[int] = mapped_column(BigInteger)
    repository_id: Mapped[int] = mapped_column(BigInteger)
    repository_owner_id: Mapped[int] = mapped_column(BigInteger)
    repository: Mapped[str] = mapped_column(String(250))
    workflow_path: Mapped[str] = mapped_column(String(250))
    ref: Mapped[str] = mapped_column(String(250))
    job_key: Mapped[str] = mapped_column(String(100))
    computer_ids: Mapped[list] = mapped_column(JSON)
    cpu_millis: Mapped[int] = mapped_column(Integer)
    memory_bytes: Mapped[int] = mapped_column(BigInteger)
    disk_bytes: Mapped[int] = mapped_column(BigInteger)
    preparation_seconds: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    max_parallel: Mapped[int] = mapped_column(Integer)
    state_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    workflow_paths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    workflow_profiles: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    repository_binding: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    authorized_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class CIRouteRevision(Identity, Base):
    """Immutable configuration history; execution history keeps its original route ID."""

    __tablename__ = "ci_route_revisions"
    __table_args__ = (UniqueConstraint("route_id", "revision"),)
    route_id: Mapped[str] = mapped_column(ForeignKey("ci_routes.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    configuration: Mapped[dict] = mapped_column(JSON)


class CIReservation(Identity, Base):
    """Durable decision; runner credentials are encrypted separately from Command/env."""

    __tablename__ = "ci_reservations"
    __table_args__ = (UniqueConstraint("route_id", "run_id", "run_attempt", "instance"),)
    route_id: Mapped[str] = mapped_column(ForeignKey("ci_routes.id"), index=True)
    route_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    workflow_path: Mapped[str | None] = mapped_column(String(250), nullable=True)
    run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    instance: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="preparing", index=True)
    reason: Mapped[str | None] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(100), unique=True)
    isolate_id: Mapped[str | None] = mapped_column(ForeignKey("isolates.id"), unique=True)
    command_id: Mapped[str | None] = mapped_column(ForeignKey("commands.id"), unique=True)
    runner_id: Mapped[int | None] = mapped_column(BigInteger)
    credentials: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_nonce: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_check: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleaned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GitHubDelivery(Base):
    __tablename__ = "github_deliveries"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


TARIFF_FIELD_TYPES = {"int": int, "decimal": Decimal, "bool": bool}
OPERATION_ID_WHITESPACE = " \t\n\r\v\f"


def canonical_uuid(value):
    return str(UUID(str(value))) if value is not None else None


def is_canonical_sqlite_owner_key(value, normalized):
    return str(value).replace("-", "") == normalized.replace("-", "")


def normalize_billing_owner_key(value, *, sqlite):
    normalized = canonical_uuid(value)
    if sqlite and value is not None and not is_canonical_sqlite_owner_key(value, normalized):
        raise ValueError(
            "The owner's stored SQLite UUID must use lowercase hexadecimal for billing"
        )
    return normalized


def prepare_billing_owner_key(row, value):
    normalized = canonical_uuid(value)
    if value is None or is_canonical_sqlite_owner_key(value, normalized):
        return normalized
    state = inspect(row)
    if state.session is None:
        # Preserve the actual input until a session knows the target dialect, including merge().
        return str(value)
    sqlite = state.session.get_bind(mapper=state.mapper).dialect.name == "sqlite"
    return normalize_billing_owner_key(value, sqlite=sqlite)


class BillingUUID(TypeDecorator):
    impl = Uuid(as_uuid=False)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return canonical_uuid(value)


class BillingOwnerUUID(BillingUUID):
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return normalize_billing_owner_key(value, sqlite=dialect.name == "sqlite")


class BillingIdentity(Identity):
    id: Mapped[str] = mapped_column(BillingUUID(), primary_key=True, default=new_id)

    @validates("id")
    def normalize_id(self, _, value):
        return canonical_uuid(value)


class BillingText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and not isinstance(value, str):
            raise ValueError("Billing names, operation IDs and comments must be text")
        return value


class TariffFields(MutableDict):
    def __ior__(self, other):
        # SQLAlchemy 2.0's MutableDict does not track the in-place dict union.
        self.update(other)
        return self


class TariffValues(TypeDecorator):
    """Encode decimals as JSON strings so their precision survives a round trip."""

    impl = JSON(none_as_null=True)
    cache_ok = True

    def compare_values(self, x, y):
        if not isinstance(x, dict) or not isinstance(y, dict):
            return super().compare_values(x, y)
        # Compare the stored representation while keeping the JSON scalar types distinct.
        return (
            x.keys() == y.keys()
            and all(type(value) is type(y[name]) for name, value in x.items())
            and self.process_bind_param(x, None) == self.process_bind_param(y, None)
        )

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return {
            name: str(item) if isinstance(item, Decimal) else item for name, item in value.items()
        }

    def process_result_value(self, value, dialect):
        if isinstance(value, str):
            return Decimal(value)
        if isinstance(value, dict):
            return {
                name: Decimal(item) if isinstance(item, str) else item
                for name, item in value.items()
            }
        return value


class TariffPlanType(BillingIdentity, Base):
    __tablename__ = "tariff_plan_types"
    __table_args__ = (
        billing_uuid_check(),
        billing_text_check("name"),
        CheckConstraint("length(name) <= 200", name="name_length"),
    )
    name: Mapped[str] = mapped_column(BillingText())
    field_types: Mapped[dict[str, Literal["int", "decimal", "bool"]]] = mapped_column(
        TariffFields.as_mutable(JSON(none_as_null=True))
    )


DOCUMENTATION_SCHEMA_SCOPE = "billing_documentation_schema_scope"
DOCUMENTATION_BIND_SCOPE = "billing_documentation_bind_scope"
DOCUMENTATION_QUERY_CONNECTION = "billing_documentation_query_connection"
POSTGRES_DOCUMENTATION_TEMP_CONNECTION = "billing_documentation_postgres_temp_connection"
POSTGRES_DOCUMENTATION_RELATION = """
    SELECT n.nspname, c.relname, c.oid FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
"""


@dataclass(frozen=True)
class DocumentationRelation:
    table_name: str | None
    schema: str | None = None
    identifier_name: str = "id"
    identifier_type: TypeEngine | None = field(default=None, compare=False)
    sqlite_storage: tuple[str, str] | None = None
    postgres_temp_connection: str | None = None
    postgres_relation_oid: int | None = None


class DocumentationSchemaScope(MapperProperty):
    """Carry the loaded schema through ORM merges without mapping another column."""

    def merge(
        self,
        _session,
        source_state,
        _source_dict,
        dest_state,
        _dest_dict,
        load,
        _recursive,
        _resolve_conflict_map,
    ):
        if load or DOCUMENTATION_SCHEMA_SCOPE in dest_state.info:
            return
        dest_state.info[DOCUMENTATION_SCHEMA_SCOPE] = source_state.info.get(
            DOCUMENTATION_SCHEMA_SCOPE
        )


class TariffFieldDocumentation(Base):
    __tablename__ = FIELD_DOCUMENTATION_TABLE
    __table_args__ = field_documentation_checks()

    id: Mapped[str] = mapped_column(BillingUUID(), primary_key=True, default=new_id)
    tariff_plan_type_id: Mapped[str] = mapped_column(
        ForeignKey("tariff_plan_types.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(BillingText())
    language_code: Mapped[str] = mapped_column(BillingText())
    comment: Mapped[str] = mapped_column(BillingText())
    plan_type: Mapped[TariffPlanType] = relationship(lazy="selectin")
    _schema_scope = DocumentationSchemaScope()

    @validates("id", "tariff_plan_type_id")
    def normalize_id(self, _, value):
        return canonical_uuid(value)


class TariffPlan(BillingIdentity, Base):
    __tablename__ = "tariff_plans"
    __table_args__ = (
        UniqueConstraint("id", "tariff_plan_type_id"),
        UniqueConstraint("tariff_plan_type_id", "published_at", name=PUBLICATION_UNIQUE),
        *(
            CheckConstraint(sql, name=name).ddl_if(dialect=dialect)
            for dialect, (name, sql) in PUBLICATION_STORAGE_CHECKS.items()
        ),
        billing_uuid_check(),
        billing_text_check("name"),
        CheckConstraint("length(name) <= 200", name="name_length"),
    )
    name: Mapped[str] = mapped_column(BillingText())
    tariff_plan_type_id: Mapped[str] = mapped_column(ForeignKey("tariff_plan_types.id"), index=True)
    published_at: Mapped[datetime] = mapped_column(
        PUBLICATION_TIMESTAMP.evaluates_none(), default=utcnow
    )
    field_values: Mapped[dict[str, int | Decimal | bool]] = mapped_column(
        TariffFields.as_mutable(TariffValues())
    )
    plan_type: Mapped[TariffPlanType] = relationship(lazy="selectin")

    @validates("tariff_plan_type_id")
    def normalize_type_id(self, _, value):
        return canonical_uuid(value)

    @validates("published_at")
    def normalize_publication(self, _, value):
        return normalize_utc(value)


class UserTariffPlan(Base):
    __tablename__ = "user_tariff_plans"
    __table_args__ = (
        billing_uuid_check("user_id"),
        ForeignKeyConstraint(
            ("tariff_plan_id", "tariff_plan_type_id"),
            ("tariff_plans.id", "tariff_plans.tariff_plan_type_id"),
        ),
    )
    user_id: Mapped[str] = mapped_column(
        BillingOwnerUUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tariff_plan_type_id: Mapped[str] = mapped_column(BillingUUID(), primary_key=True)
    tariff_plan_id: Mapped[str] = mapped_column(BillingUUID(), index=True)
    tariff_plan: Mapped[TariffPlan] = relationship(lazy="selectin")
    user: Mapped[User] = relationship(lazy="raise")

    @validates("tariff_plan_type_id", "tariff_plan_id")
    def normalize_id(self, _, value):
        return canonical_uuid(value)

    @validates("user_id")
    def normalize_owner_id(self, _, value):
        return prepare_billing_owner_key(self, value)


@event.listens_for(Session, "do_orm_execute")
def require_tariff_unit_of_work(state):
    if (state.is_insert or state.is_update) and any(
        mapper.class_ in (TariffPlanType, TariffPlan) for mapper in state.all_mappers
    ):
        raise InvalidRequestError("Write tariff types and plans through mapped instances")


def validate_tariff_field_name_encoding(name: str):
    if "\0" in name or any("\ud800" <= char <= "\udfff" for char in name):
        raise ValueError("Tariff field names must be valid Unicode without NUL")


@event.listens_for(Session, "before_flush")
def validate_billing_input(session: Session, flush_context, _instances):
    pending = session.new
    plan_types = []
    plans = []
    for row in (pending | session.dirty) - session.deleted:
        if isinstance(row, (UserTariffPlan, PaymentAccount)):
            fields = ("user_id", "company_id") if isinstance(row, PaymentAccount) else ("user_id",)
            state = inspect(row)
            for name in fields:
                value = state.dict.get(name)
                if value != canonical_uuid(value):
                    setattr(row, name, value)
        elif isinstance(row, TariffPlanType):
            if row not in pending and not inspect(row).attrs.field_types.history.has_changes():
                continue
            if row.field_types is None:
                raise ValueError("Tariff plan type requires a field schema")
            for name, kind in row.field_types.items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Tariff field names must be nonempty strings")
                validate_tariff_field_name_encoding(name)
                if not isinstance(kind, str) or kind not in TARIFF_FIELD_TYPES:
                    raise ValueError(f"Unsupported tariff field type for {name!r}")
            plan_types.append(row)
        elif isinstance(row, TariffPlan):
            if row in pending or inspect(row).attrs.field_values.history.has_changes():
                if row.field_values is None:
                    raise ValueError("Tariff values must include exactly the declared fields")
                for name, value in row.field_values.items():
                    if not isinstance(name, str):
                        raise ValueError("Tariff field names must be strings")
                    validate_tariff_field_name_encoding(name)
                    if type(value) not in TARIFF_FIELD_TYPES.values():
                        raise ValueError(f"Tariff field {name!r} must be int, decimal or bool")
            plans.append(row)
    flush_context.attributes["tariff_changes"] = plan_types, plans


@event.listens_for(Session, "after_flush")
def validate_stored_tariffs(session: Session, flush_context):
    plan_types, plans = flush_context.attributes["tariff_changes"]
    if not (plan_types or plans):
        return
    mapper = TariffPlan if plans else TariffPlanType
    connection = session.connection(bind_arguments={"mapper": mapper})
    stored_plans = connection.execute(
        select(TariffPlan.tariff_plan_type_id, TariffPlan.field_values).where(
            or_(
                TariffPlan.id.in_([plan.id for plan in plans]),
                TariffPlan.tariff_plan_type_id.in_([plan_type.id for plan_type in plan_types]),
            )
        )
    ).all()
    if not stored_plans:
        return
    # Version each used schema row so an editor with an older snapshot cannot miss a new plan.
    schemas = dict(
        connection.execute(
            update(TariffPlanType)
            .where(TariffPlanType.id.in_({plan.tariff_plan_type_id for plan in stored_plans}))
            .values(field_types=TariffPlanType.field_types)
            .returning(TariffPlanType.id, TariffPlanType.field_types)
        ).all()
    )
    for type_id, field_values in stored_plans:
        field_types = schemas[type_id]
        if field_values.keys() != field_types.keys():
            raise ValueError("Tariff values must include exactly the declared fields")
        for name, kind in field_types.items():
            value = field_values[name]
            if type(value) is not TARIFF_FIELD_TYPES[kind]:
                raise ValueError(f"Tariff field {name!r} must be {kind}")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError(f"Tariff field {name!r} must be finite")


def documentation_connection_scope(connection, identifier, translated):
    if identifier is None:
        return DocumentationRelation(None)
    table = identifier.table
    namespace = table.schema
    table_name = table.name
    if namespace in translated:
        namespace = translated[namespace] or connection.dialect.default_schema_name
    storage = None
    temp_connection = None
    relation_oid = None
    if connection.dialect.name == "sqlite":
        if namespace is None:
            namespace = sqlite_documentation_schema(connection, table_name)
        if namespace is not None:
            storage = sqlite_documentation_storage(connection, namespace)
    elif connection.dialect.name == "postgresql":
        preparer = connection.dialect.identifier_preparer
        relation = preparer.format_table(table, use_schema=False)
        if namespace is not None:
            relation = f"{preparer.quote_schema(namespace)}.{relation}"
        location = connection.execute(
            text(
                POSTGRES_DOCUMENTATION_RELATION + "WHERE c.oid = pg_catalog.to_regclass(:relation)"
            ),
            {"relation": relation},
        ).one_or_none()
        if location is not None:
            namespace, table_name, relation_oid = location
            if namespace.startswith("pg_temp_"):
                temp_connection = connection.info.setdefault(
                    POSTGRES_DOCUMENTATION_TEMP_CONNECTION, new_id()
                )
    return DocumentationRelation(
        table_name,
        namespace,
        identifier.name,
        identifier.type,
        storage,
        temp_connection,
        relation_oid,
    )


def documentation_selectable_identifier(selectable):
    mapped = TariffFieldDocumentation.__table__.c.id
    if isinstance(selectable, Table):
        return selectable.c.get(mapped.key, mapped)
    identifier = selectable.corresponding_column(mapped)
    if identifier is None:
        identifier = selectable.exported_columns.get(mapped.key, mapped)
    # An ORM alias can read a physical table different from its mapper's table.
    sources = {
        (column.table, column.name): column
        for column in identifier.proxy_set
        if isinstance(getattr(column, "table", None), Table)
    }
    return next(iter(sources.values())) if len(sources) == 1 else None


def preferred_documentation_relation(scopes):
    # Prefer the mapped UUID codec when reflected aliases describe the same key.
    return next(
        (scope for scope in scopes if isinstance(scope.identifier_type, BillingUUID)),
        scopes[0],
    )


def documentation_statement_identifier(source):
    if isinstance(source, SelectStatementGrouping):
        return documentation_statement_identifier(source.element)
    if isinstance(source, CompoundSelect):
        # Compound SELECT exports the first branch's columns. Inspect each branch for lineage.
        identifiers = {documentation_statement_identifier(branch) for branch in source.selects}
        return next(iter(identifiers)) if len(identifiers) == 1 else None
    if isinstance(source, (SelectBase, UpdateBase)) and not isinstance(source, TextualSelect):
        return documentation_selectable_identifier(source)
    return None


def documentation_load_scope(state, context, resolve):
    mapped = TariffFieldDocumentation.__table__
    if context.refresh_state is not None:
        return resolve(mapped.c.id)
    statement = context.user_passed_query
    if isinstance(statement, FromStatement):
        return resolve(documentation_statement_identifier(statement.element))
    selectables = {
        entity.selectable
        for entity in context.compile_state._entities
        if getattr(entity, "mapper", None) is state.mapper
    }
    scopes = [
        resolve(documentation_selectable_identifier(selectable))
        for selectable in selectables or (mapped,)
    ]
    if len(set(scopes)) == 1:
        return preferred_documentation_relation(scopes)
    if context.populate_existing:
        # This mode can retain a path from an older query. Do not guess between relations.
        return DocumentationRelation(None)
    selectable = state.load_path.path[-1].selectable
    return resolve(documentation_selectable_identifier(selectable))


@event.listens_for(TariffFieldDocumentation, "after_insert", raw=True)
def remember_inserted_documentation_schema(_mapper, connection, state):
    translated = connection.get_execution_options().get("schema_translate_map") or {}
    state.info[DOCUMENTATION_SCHEMA_SCOPE] = documentation_connection_scope(
        connection, TariffFieldDocumentation.__table__.c.id, translated
    )
    state.session.info.setdefault(DOCUMENTATION_BIND_SCOPE, WeakKeyDictionary())[state] = (
        documentation_origin_binding(connection)
    )


def documentation_origin_binding(connection):
    engine = connection.engine
    while isinstance(engine, OptionEngine):
        engine = engine._proxied
    return ref(engine), ref(connection.engine.pool)


@event.listens_for(Session, "do_orm_execute")
def mark_documentation_query(execution: ORMExecuteState):
    if TariffFieldDocumentation.__mapper__ in execution.all_mappers:
        execution.update_execution_options(**{DOCUMENTATION_QUERY_CONNECTION: {}})


@event.listens_for(Engine, "before_cursor_execute")
def remember_documentation_query_connection(connection, _cursor, _sql, _params, context, _many):
    capture = context.execution_options.get(DOCUMENTATION_QUERY_CONNECTION)
    if capture is not None:
        capture["connection"] = connection


@event.listens_for(TariffFieldDocumentation, "load", raw=True)
@event.listens_for(TariffFieldDocumentation, "refresh", raw=True)
def remember_documentation_schema(state, context, *_):
    if context is None:
        return
    if DOCUMENTATION_BIND_SCOPE not in context.attributes:
        capture = context.execution_options.get(DOCUMENTATION_QUERY_CONNECTION, {})
        connection = capture.get("connection")
        if connection is None:
            connection = context.session.connection(bind_arguments=context.bind_arguments)
        statement_options = context.user_passed_query.get_execution_options()
        bound_options = connection.get_execution_options()
        option = "schema_translate_map"
        translated = (
            context.execution_options.get(
                option, bound_options.get(option, statement_options.get(option))
            )
            or {}
        )
        context.attributes[DOCUMENTATION_BIND_SCOPE] = (
            connection,
            translated,
            documentation_origin_binding(connection),
        )
    connection, translated, binding = context.attributes[DOCUMENTATION_BIND_SCOPE]

    def resolve(identifier):
        key = DOCUMENTATION_SCHEMA_SCOPE, identifier
        if key not in context.attributes:
            context.attributes[key] = documentation_connection_scope(
                connection, identifier, translated
            )
        return context.attributes[key]

    state.info[DOCUMENTATION_SCHEMA_SCOPE] = documentation_load_scope(state, context, resolve)
    context.session.info.setdefault(DOCUMENTATION_BIND_SCOPE, WeakKeyDictionary())[state] = binding


def documentation_state_pool(session, state):
    origin = session.info.get(DOCUMENTATION_BIND_SCOPE, {}).get(state)
    if origin is not None:
        engine, pool = (reference() for reference in origin)
        return engine.pool if engine is not None else pool
    # A merged or reattached snapshot has the destination session's binding.
    return session.get_bind(mapper=TariffFieldDocumentation).engine.pool


def documentation_lookup_id(value, sql_type):
    # A reflected UUID may load as a UUID object while the mapped key loads as a string.
    if isinstance(sql_type, Uuid) and sql_type.as_uuid:
        return UUID(str(value))
    if isinstance(sql_type, (BillingUUID, Uuid)):
        return canonical_uuid(value)
    return value


def postgres_documentation_location(connection, scope):
    if scope.postgres_temp_connection is not None and (
        connection.info.get(POSTGRES_DOCUMENTATION_TEMP_CONNECTION)
        != scope.postgres_temp_connection
    ):
        return None
    return connection.execute(
        text(POSTGRES_DOCUMENTATION_RELATION + "WHERE c.oid = :oid"),
        {"oid": scope.postgres_relation_oid},
    ).one_or_none()


def postgres_documentation_matches(connection, table, relation_oid):
    relation = connection.dialect.identifier_preparer.format_table(table)
    return (
        connection.scalar(
            text("SELECT pg_catalog.to_regclass(:relation)::oid"), {"relation": relation}
        )
        == relation_oid
    )


def documentation_lookup_column(connection, scope):
    if scope is None:
        return TariffFieldDocumentation.id, {}
    if scope.table_name is None:
        return None
    namespace, table_name = scope.schema, scope.table_name
    if scope.sqlite_storage is not None:
        namespace = sqlite_documentation_lookup_schema(connection, scope.sqlite_storage, table_name)
        if namespace is None:
            return None
    if scope.postgres_relation_oid is not None:
        location = postgres_documentation_location(connection, scope)
        if location is None:
            return None
        namespace, table_name, _ = location
    physical = table(
        table_name,
        sql_column(
            scope.identifier_name, scope.identifier_type or TariffFieldDocumentation.id.type
        ),
        schema=namespace,
    )
    return physical.c[scope.identifier_name], {"schema_translate_map": {}}


def missing_documentation_states(connection, scope, states):
    relation_oid = scope.postgres_relation_oid if scope is not None else None
    # Retry from the same OID if a concurrent rename invalidates its first location.
    for _ in range(2 if relation_oid is not None else 1):
        lookup = documentation_lookup_column(connection, scope)
        if lookup is None:
            return ()
        identifier, options = lookup
        documents = defaultdict(list)
        for state in states:
            documents[documentation_lookup_id(state.identity[0], identifier.type)].append(state)
        ids = tuple(documents)
        savepoint = connection.begin_nested() if relation_oid is not None else nullcontext()
        try:
            with savepoint:
                for start in range(0, len(ids), 500):
                    statement = select(identifier).where(identifier.in_(ids[start : start + 500]))
                    for saved_id in connection.scalars(statement, execution_options=options):
                        documents.pop(documentation_lookup_id(saved_id, identifier.type), None)
                if relation_oid is None or postgres_documentation_matches(
                    connection, identifier.table, relation_oid
                ):
                    return [state for aliases in documents.values() for state in aliases]
        except DBAPIError as error:
            if (
                relation_oid is None
                or error.connection_invalidated
                or postgres_documentation_matches(connection, identifier.table, relation_oid)
            ):
                raise
    return ()


def cascaded_field_documentation(session: Session):
    document_states = [
        state
        for state in session.identity_map.all_states()
        if state.mapper.class_ is TariffFieldDocumentation
    ]
    if not document_states:
        return ()
    connection = session.connection(bind_arguments={"mapper": TariffPlanType})
    scopes = defaultdict(list)
    for state in document_states:
        if documentation_state_pool(session, state) is connection.engine.pool:
            scopes[state.info.get(DOCUMENTATION_SCHEMA_SCOPE)].append(state)
    missing = []
    # Check the stored rows so deferred foreign keys and documents moved in this flush work alike.
    for scope, states in scopes.items():
        if scope is not None:
            scope = preferred_documentation_relation(
                [state.info[DOCUMENTATION_SCHEMA_SCOPE] for state in states]
            )
        missing.extend(missing_documentation_states(connection, scope, states))
    return missing


@event.listens_for(Session, "after_flush_postexec")
def mark_cascaded_billing_rows_deleted(session: Session, flush_context):
    for account in flush_context.attributes.get("accounts_to_expire", ()):
        # The caller's collection can omit links that were inserted or retained by the database.
        expire_account_tariffs(session, account)
    if "accounts_excluded_from_tariff_expiry" in flush_context.attributes:
        expire_loaded_account_tariffs(
            session, exclude=flush_context.attributes["accounts_excluded_from_tariff_expiry"]
        )
    expire_replaced_account_links(session, flush_context.attributes.get("replaced_account_ids", ()))
    deleted_users = {
        state.identity[0]
        for state, (deleted, list_only) in flush_context.states.items()
        if state.mapper.class_ is User and deleted and not list_only
    }
    deleted_accounts = {
        state.identity[0]
        for state, (deleted, list_only) in flush_context.states.items()
        if state.mapper.class_ is PaymentAccount and deleted and not list_only
    }
    deleted_types = any(
        state.mapper.class_ is TariffPlanType and deleted and not list_only
        for state, (deleted, list_only) in flush_context.states.items()
    )
    if deleted_users:
        # The legacy SQLite owner column stores case-sensitive text. Billing IDs are lowercase.
        sqlite = session.get_bind(mapper=User).dialect.name == "sqlite"
        deleted_users = {
            canonical_uuid(key)
            for key in deleted_users
            if not sqlite or is_canonical_sqlite_owner_key(key, canonical_uuid(key))
        }
    if deleted_users or deleted_accounts or deleted_types:
        # Moved assignments already have their new keys. The database deleted the others.
        deleted_owners = {UserTariffPlan: deleted_users, PaymentAccountTariffPlan: deleted_accounts}
        states = [
            state
            for state in session.identity_map.all_states()
            if state.identity[0] in deleted_owners.get(state.mapper.class_, ())
        ]
        if deleted_types:
            states.extend(cascaded_field_documentation(session))
        for state in states:
            record_billing_delete(session, state)
        # Drop the keys now and let the ORM restore the deleted states on rollback.
        session._remove_newly_deleted(states)


class PaymentAmount(TypeDecorator):
    """Store exact amounts. Explicit SQL operands use PaymentAmount units.

    SQLite's generic SQL functions see integer millionths.
    """

    impl = Numeric(18, 6).with_variant(BigInteger(), "sqlite")
    cache_ok = True

    class Comparator(TypeDecorator.Comparator):
        def check_scale(self, op, operands):
            if op in (operators.mul, operators.truediv, operators.floordiv, operators.mod):
                raise TypeError(
                    "Use Decimal arithmetic on loaded payment amounts for this operation"
                )
            for operand in operands:
                # RETURNING compilation compares physical columns from the same table.
                if (
                    op in (operators.eq, operators.ne)
                    and isinstance(operand, Column)
                    and operand.table is getattr(self.expr, "table", None)
                ):
                    continue
                sql_type = getattr(operand, "type", None)
                if (
                    sql_type is not None
                    and not isinstance(sql_type, PaymentAmount)
                    and not isinstance(operand, Null)
                ):
                    raise TypeError("Use PaymentAmount SQL operands or Python Decimal values")

        def operate(self, op, *other, **kwargs):
            if op not in (operators.in_op, operators.not_in_op):
                self.check_scale(op, other)
                return super().operate(op, *other, **kwargs)
            # SQLAlchemy gives literal lists and subqueries their operand types here.
            expression = super().operate(op, *other, **kwargs)
            right = expression.right
            if isinstance(right, Grouping):
                right = right.element
            self.check_scale(op, right.clauses if isinstance(right, ClauseList) else (right,))
            return expression

        def reverse_operate(self, op, other, **kwargs):
            self.check_scale(op, (other,))
            return super().reverse_operate(op, other, **kwargs)

    comparator_factory = Comparator

    def compare_values(self, x, y):
        if type(x) is not type(y):
            return False
        if isinstance(x, Decimal):
            return x.is_finite() and y.is_finite() and x == y
        return super().compare_values(x, y)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("Payment amounts must be finite Decimals")
        if value.copy_abs() >= Decimal("1000000000000"):
            raise ValueError("Payment amount exceeds NUMERIC(18, 6)")
        with localcontext(Context(prec=19)):
            quantized = value.quantize(Decimal("0.000001"))
            if quantized != value:
                raise ValueError("Payment amounts support up to six fractional digits")
            return int(quantized.scaleb(6)) if dialect.name == "sqlite" else quantized

    def process_result_value(self, value, dialect):
        if value is not None and dialect.name == "sqlite":
            return Decimal(value).scaleb(-6, context=Context(prec=19))
        return value


def payment_amount_checks(column):
    return (
        CheckConstraint(
            f"typeof({column}) = 'integer' AND "
            f"{column} BETWEEN -999999999999999999 AND 999999999999999999",
            name=f"{column}_storage",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(f"{column} <> 'NaN'", name=f"{column}_finite").ddl_if(dialect="postgresql"),
    )


class PaymentAccount(BillingIdentity, Base):
    __tablename__ = "payment_accounts"
    __table_args__ = (
        billing_uuid_check(),
        billing_uuid_check("user_id", nullable=True),
        billing_uuid_check("company_id", nullable=True),
        owner_check(),
        CheckConstraint("balance >= 0", name="nonnegative_balance"),
        *payment_amount_checks("balance"),
    )
    user_id: Mapped[str | None] = mapped_column(
        BillingOwnerUUID(), ForeignKey("users.id"), index=True
    )
    company_id: Mapped[str | None] = mapped_column(
        BillingOwnerUUID(), ForeignKey("companies.id"), index=True
    )
    balance: Mapped[Decimal] = mapped_column(PaymentAmount(), server_default="0")
    user: Mapped[User | None] = relationship(lazy="raise")
    company: Mapped[Company | None] = relationship(lazy="raise")
    tariff_assignments: Mapped[list["PaymentAccountTariffPlan"]] = relationship(
        back_populates="payment_account",
        passive_deletes="all",
        lazy="raise",
    )

    @validates("user_id", "company_id")
    def normalize_owner_id(self, _, value):
        return prepare_billing_owner_key(self, value)


class PaymentAccountTariffPlan(Base):
    __tablename__ = "payment_account_tariff_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tariff_plan_id", "tariff_plan_type_id"),
            ("tariff_plans.id", "tariff_plans.tariff_plan_type_id"),
        ),
    )
    payment_account_id: Mapped[str] = mapped_column(
        BillingUUID(), ForeignKey("payment_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    tariff_plan_type_id: Mapped[str] = mapped_column(BillingUUID(), primary_key=True)
    tariff_plan_id: Mapped[str] = mapped_column(BillingUUID(), index=True)
    payment_account: Mapped[PaymentAccount] = relationship(
        back_populates="tariff_assignments", lazy="raise"
    )
    tariff_plan: Mapped[TariffPlan] = relationship(lazy="selectin")

    @validates("payment_account_id", "tariff_plan_type_id", "tariff_plan_id")
    def normalize_id(self, _, value):
        return canonical_uuid(value)


BILLING_TRANSACTION_CHANGES = "billing_transaction_changes"


@dataclass(frozen=True)
class AuthoredTariffLink:
    link: PaymentAccountTariffPlan
    values: dict[str, str | PaymentAccount | TariffPlan]


@dataclass
class BillingTransactionChanges:
    account_ids: set[str] = field(default_factory=set)
    replaced_account_ids: set[str] = field(default_factory=set)
    seeded_tariff_types: set[str] = field(default_factory=set)
    refresh_tariff_collections: bool = False
    inserted_states: set[InstanceState] = field(default_factory=set)
    authored_tariff_links: dict[InstanceState, AuthoredTariffLink | None] = field(
        default_factory=dict
    )
    loaded_tariff_owners: dict[tuple[InstanceState, str], PaymentAccount] = field(
        default_factory=dict
    )
    detached_tariff_accounts: set[InstanceState] = field(default_factory=set)
    original_keys: dict[InstanceState, tuple | None] = field(default_factory=dict)
    loaded_keys: dict[InstanceState, tuple[tuple, tuple]] = field(default_factory=dict)
    # Map affected keys to their prior identity. Reloads of new or replaced rows are temporary.
    row_origins: dict[tuple, tuple | None] = field(default_factory=dict)
    rolled_back: bool = False


def current_billing_changes(session: Session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    changes = session.info.setdefault(BILLING_TRANSACTION_CHANGES, {})
    return changes.setdefault(transaction, BillingTransactionChanges())


def active_billing_changes(session: Session):
    changes = session.info.get(BILLING_TRANSACTION_CHANGES)
    transaction = session.get_nested_transaction() or session.get_transaction()
    while changes and transaction is not None:
        posted = changes.get(transaction)
        if posted is not None:
            yield posted
        transaction = transaction.parent


@event.listens_for(Session, "persistent_to_detached")
def remember_detached_tariff_account(session: Session, row):
    if isinstance(row, PaymentAccount):
        state = inspect(row)
        for posted in active_billing_changes(session):
            if posted.seeded_tariff_types and "tariff_assignments" in state.dict:
                posted.detached_tariff_accounts.add(state)


def billing_row_origin(changes: BillingTransactionChanges, key):
    if (
        key is not None
        and key[0] is PaymentAccountTariffPlan
        and key[1][1] in changes.seeded_tariff_types
    ):
        return changes.row_origins.get(key)
    return changes.row_origins.get(key, key)


def merge_billing_changes(parent: BillingTransactionChanges, child: BillingTransactionChanges):
    parent.account_ids.update(child.account_ids)
    parent.replaced_account_ids.update(child.replaced_account_ids)
    parent.refresh_tariff_collections |= child.refresh_tariff_collections
    parent.inserted_states.update(child.inserted_states)
    parent.authored_tariff_links.update(child.authored_tariff_links)
    parent.loaded_tariff_owners.update(child.loaded_tariff_owners)
    parent.detached_tariff_accounts.update(child.detached_tariff_accounts)
    for state, original_key in child.original_keys.items():
        if state in parent.original_keys or state in parent.inserted_states:
            continue
        if state in child.loaded_keys:
            loaded_key, origin = child.loaded_keys[state]
            earlier = billing_row_origin(parent, origin)
            if earlier is None:
                parent.inserted_states.add(state)
                continue
            parent.loaded_keys[state] = (loaded_key, earlier)
            original_key = loaded_key if loaded_key == earlier else None
        parent.original_keys[state] = original_key
    parent.row_origins.update(
        {key: billing_row_origin(parent, origin) for key, origin in child.row_origins.items()}
    )
    parent.seeded_tariff_types.update(child.seeded_tariff_types)


@event.listens_for(Session, "after_commit")
def commit_billing_changes(session: Session):
    changes = session.info.get(BILLING_TRANSACTION_CHANGES)
    if changes:
        transaction = session.get_nested_transaction() or session.get_transaction()
        posted = changes.pop(transaction, None)
        if posted is not None and transaction.parent is not None:
            parent = changes.setdefault(transaction.parent, BillingTransactionChanges())
            merge_billing_changes(parent, posted)


@event.listens_for(Session, "after_rollback")
def mark_billing_rollback(session: Session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    changes = session.info.get(BILLING_TRANSACTION_CHANGES, {}).get(transaction)
    if changes is not None:
        changes.rolled_back = True
        for state in changes.inserted_states:
            account = state.object
            if (
                isinstance(account, PaymentAccount)
                and session.identity_map.get(state.key) is account
            ):
                # The active account may differ from an authored link's original backreference.
                remember_loaded_tariff_owners(changes, account, state)


def discard_billing_state(session: Session, state: InstanceState):
    row = state.object
    if row is not None and state.session is session:
        session.expunge(row)
    else:
        # ORM rollback can restore a detached state at its previous identity key.
        session.identity_map.safe_discard(state)


def restore_billing_key(session: Session, state: InstanceState, original_key):
    row = state.object
    attached = row is not None and state.session is session and (state.persistent or state.deleted)
    occupant = session.identity_map.get(original_key) if original_key is not None else None
    if original_key is not None and attached and (occupant is None or occupant is row):
        # Use the outer scope's key and the ORM's normal rollback path for deleted states.
        state.key = original_key
        if state.deleted:
            session._update_impl(state, revert_deletion=True)
        elif occupant is None:
            session.identity_map.add(state)
        session.expire(row)
        # An outer savepoint can still own the row just restored in this one.
        remember_reloaded_billing_state(state, None)
        return
    discard_billing_state(session, state)
    if original_key is not None and state.session is None:
        # Keep its old key available to a surviving parent that tracked the inserted row.
        state.key = original_key


def expire_replaced_account_links(session: Session, account_ids):
    if not account_ids:
        return
    for link in session.identity_map.values():
        if not isinstance(link, PaymentAccountTariffPlan):
            continue
        state = inspect(link)
        account_id = state.identity[0]
        if account_id not in account_ids:
            continue
        # A row switch keeps the FK but replaces the object behind that identity.
        account = session.identity_map.get(session.identity_key(PaymentAccount, account_id))
        if state.dict.get("payment_account", account) is not account:
            session.expire(link, ["payment_account"])


def remember_loaded_tariff_owners(changes, account: PaymentAccount, state: InstanceState):
    account_id = state.identity[0]
    changes.loaded_tariff_owners.update(
        ((inspect(link), account_id), account) for link in state.dict.get("tariff_assignments", ())
    )


def expire_account_tariffs(session: Session, account: PaymentAccount):
    state = inspect(account)
    for posted in active_billing_changes(session):
        if state in posted.inserted_states:
            # Keep the collection with this account instance, even if its links span savepoints.
            remember_loaded_tariff_owners(posted, account, state)
            break
    session.expire(account, ["tariff_assignments"])


def expire_loaded_account_tariffs(session: Session, *, exclude=()):
    for account in session.identity_map.values():
        if (
            isinstance(account, PaymentAccount)
            and account not in exclude
            and "tariff_assignments" in inspect(account).dict
        ):
            expire_account_tariffs(session, account)


def remove_rolled_back_tariff_links(changes: BillingTransactionChanges):
    implicit = changes.inserted_states.copy()
    authored = defaultdict(list)
    authored_owners = {}
    for link_state, draft in changes.authored_tariff_links.items():
        if draft is None:
            continue
        implicit.discard(link_state)
        account = changes.loaded_tariff_owners.get(
            (link_state, draft.values["payment_account_id"]), draft.values.get("payment_account")
        )
        if account is not None and link_state in changes.inserted_states:
            account_state = inspect(account)
            authored[account_state].append(draft)
            authored_owners[link_state] = account_state

    for state in changes.inserted_states | changes.detached_tariff_accounts:
        account = state.object
        if not isinstance(account, PaymentAccount):
            continue
        assignments = state.dict.get("tariff_assignments", ())
        retained = [
            link
            for link in assignments
            if (link_state := inspect(link)) not in implicit
            and authored_owners.get(link_state, state) is state
        ]
        if state in changes.inserted_states:
            # Internal expiration may have removed the whole collection, including draft links.
            for draft in authored.get(state, ()):
                inspect(draft.link).dict.pop("tariff_plan", None)
                for name, value in (draft.values | {"payment_account": account}).items():
                    set_committed_value(draft.link, name, value)
                if draft.link not in retained:
                    retained.append(draft.link)
        if retained != list(assignments):
            # An account can be added to a session again after rollback. Keep its explicit inputs
            # and discard links read back from the rolled-back trigger.
            set_committed_value(account, "tariff_assignments", retained)


@event.listens_for(Session, "after_transaction_end")
def discard_rolled_back_billing_changes(session: Session, transaction):
    changes = session.info.get(BILLING_TRANSACTION_CHANGES)
    posted = changes.pop(transaction, None) if changes else None
    if posted is None:
        return
    if not posted.rolled_back:
        # SQLAlchemy closes descendants before rolling back an ancestor's snapshot.
        if transaction.parent is not None:
            parent = changes.setdefault(transaction.parent, BillingTransactionChanges())
            merge_billing_changes(parent, posted)
        return
    remove_rolled_back_tariff_links(posted)
    for state in posted.inserted_states:
        discard_billing_state(session, state)
    # Vacate changed keys before restoring any of them. Two states can exchange keys.
    for state, original_key in posted.original_keys.items():
        if state.key != original_key or state.session is not session:
            session.identity_map.safe_discard(state)
    for state, original_key in posted.original_keys.items():
        if state not in posted.inserted_states:
            restore_billing_key(session, state, original_key)
    expire_replaced_account_links(session, posted.replaced_account_ids)
    if posted.seeded_tariff_types or posted.refresh_tariff_collections:
        expire_loaded_account_tariffs(session)
        if transaction.parent is not None:
            # A collection reloaded after this rollback can reflect uncommitted parent edits.
            parent = changes.setdefault(transaction.parent, BillingTransactionChanges())
            parent.refresh_tariff_collections = True
    for account_id in posted.account_ids:
        account = session.identity_map.get(session.identity_key(PaymentAccount, account_id))
        if account is not None:
            session.expire(account)


SQLITE_SAVEPOINT_WITHOUT_BEGIN = "billing_sqlite_savepoint_without_begin"


def sqlite_supports_transactions(connection):
    driver = connection.connection.driver_connection
    if connection.dialect.driver == "aiosqlite":
        # aiosqlite does not expose autocommit. Read it on its SQLite worker thread.
        autocommit = connection.connection.dbapi_connection.run_async(
            lambda driver: driver._execute(lambda: getattr(driver._conn, "autocommit", None))
        )
    else:
        autocommit = getattr(driver, "autocommit", None)
    if autocommit is True or (
        driver.isolation_level is None
        and getattr(connection.dialect, "skip_autocommit_rollback", False)
    ):
        return False
    if autocommit is False or driver.isolation_level is not None:
        return True
    # Engines that disable implicit BEGIN can open the parent transaction themselves.
    unguarded = connection.info.get(SQLITE_SAVEPOINT_WITHOUT_BEGIN)
    return driver.in_transaction and (
        unguarded is None or unguarded() is not connection.get_transaction()
    )


@event.listens_for(Engine, "savepoint")
def begin_sqlite_savepoint(connection, _):
    """Give a SQLite savepoint a physical parent transaction in legacy driver mode."""
    if (
        connection.dialect.name != "sqlite"
        or connection.connection.driver_connection.in_transaction
    ):
        return
    if sqlite_supports_transactions(connection):
        isolation_level = connection.connection.driver_connection.isolation_level
        connection.exec_driver_sql(f"BEGIN {isolation_level}" if isolation_level else "BEGIN")
    else:
        # An outermost SAVEPOINT alone makes in_transaction true without a parent BEGIN.
        connection.info[SQLITE_SAVEPOINT_WITHOUT_BEGIN] = ref(connection.get_transaction())


class PaymentLedgerEntry(BillingIdentity, Base):
    __tablename__ = "payment_ledger_entries"
    __table_args__ = (
        billing_uuid_check(),
        CheckConstraint(
            "length(operation_id) BETWEEN 1 AND 128 "
            f"AND trim(operation_id, '{OPERATION_ID_WHITESPACE}') <> ''",
            name="valid_operation_id",
        ),
        billing_text_check("operation_id"),
        billing_text_check("comment"),
        *payment_amount_checks("amount"),
    )
    payment_account_id: Mapped[str] = mapped_column(ForeignKey("payment_accounts.id"), index=True)
    operation_id: Mapped[str] = mapped_column(BillingText(), index=True)
    amount: Mapped[Decimal] = mapped_column(PaymentAmount())
    comment: Mapped[str | None] = mapped_column(BillingText())
    payment_account: Mapped[PaymentAccount] = relationship(lazy="raise")

    @validates("payment_account_id")
    def normalize_account_id(self, _, value):
        return canonical_uuid(value)

    @classmethod
    async def post(
        cls,
        session: AsyncSession,
        *,
        payment_account_id: str,
        operation_id: str,
        amount: Decimal,
        comment: str | None = None,
        entry_id: str | None = None,
    ) -> Self:
        """Apply a signed amount and persist its ledger entry in the caller's transaction."""
        if not isinstance(amount, Decimal):
            raise TypeError("Posting amount must be a Decimal")
        entry = cls(
            id=entry_id,
            payment_account_id=payment_account_id,
            operation_id=operation_id,
            amount=amount,
            comment=comment,
        )
        conn = await session.connection(bind_arguments={"mapper": PaymentAccount})
        if conn.dialect.name == "sqlite" and not await conn.run_sync(sqlite_supports_transactions):
            raise InvalidRequestError("Posting requires a transactional SQLite connection")
        # Starting a savepoint flushes pending work before changing money. It also lets
        # the caller catch a posting failure without aborting other work in the transaction.
        async with session.begin_nested():
            account_id = entry.payment_account_id
            # Look for the account before PostgreSQL takes the billing write lock.
            if conn.dialect.name == "postgresql":
                account_id = await session.scalar(
                    select(PaymentAccount.id).where(PaymentAccount.id == account_id)
                )
            statement = (
                update(PaymentAccount)
                .where(PaymentAccount.id == account_id)
                .values(balance=PaymentAccount.balance + amount)
                .returning(PaymentAccount.balance)
                .execution_options(synchronize_session=False)
            )
            balance = await session.scalar(statement) if account_id is not None else None
            if balance is None:
                raise NoResultFound("Payment account not found")
            session.add(entry)
            await session.flush()
        current_billing_changes(session.sync_session).account_ids.add(account_id)
        account = session.identity_map.get(session.identity_key(PaymentAccount, account_id))
        # Preserve a full expiration so an async lookup reloads every account field.
        if account is not None and not inspect(account).expired:
            set_committed_value(account, "balance", balance)
        return entry


# Drop documentation before retiring the catalogue guards, while the guarded tables still exist.
PaymentAccountTariffPlan.__table__.add_is_dependent_on(PaymentLedgerEntry.__table__)
PaymentAccountTariffPlan.__table__.add_is_dependent_on(UserTariffPlan.__table__)
TariffFieldDocumentation.__table__.add_is_dependent_on(PaymentAccountTariffPlan.__table__)
register_first_tariff_triggers(PaymentAccountTariffPlan.__table__)
register_field_documentation_triggers(TariffFieldDocumentation.__table__)
register_billing_index_creation(PaymentAccountTariffPlan.__table__)
register_billing_index_creation(TariffFieldDocumentation.__table__)


@event.listens_for(PaymentAccount.balance, "set")
@event.listens_for(PaymentLedgerEntry.amount, "set")
def validate_payment_amount_assignment(_target, value, _previous, _initiator):
    # SQL expressions bypass the target column's bind processor during an ORM flush.
    if isinstance(value, ClauseElement) or hasattr(value, "__clause_element__"):
        raise TypeError("Payment amount assignments require a Python Decimal")


BILLING_MODELS = (
    TariffPlanType,
    TariffFieldDocumentation,
    TariffPlan,
    UserTariffPlan,
    PaymentAccount,
    PaymentAccountTariffPlan,
    PaymentLedgerEntry,
)


def billing_key_after_flush(state: InstanceState):
    mapper = state.mapper
    if state.key is None:
        return mapper.identity_key_from_instance(state.object)
    primary_key = tuple(
        state.dict.get(mapper.get_property_by_column(column).key, value)
        for column, value in zip(mapper.primary_key, state.identity, strict=True)
    )
    return mapper.identity_key_from_primary_key(primary_key, identity_token=state.identity_token)


def remember_authored_tariff_link(session: Session, changes, state, key):
    previous = next(
        (
            posted.authored_tariff_links[state]
            for posted in active_billing_changes(session)
            if state in posted.authored_tariff_links
        ),
        None,
    )
    if state.key is not None and previous is None:
        return
    # A refresh or a savepoint rollback can unload attributes of this draft before it is retried.
    original = previous.values if previous else {}
    account_id, type_id = key[1]
    values = {
        "payment_account_id": account_id,
        "tariff_plan_type_id": type_id,
        "tariff_plan_id": state.dict.get("tariff_plan_id", original.get("tariff_plan_id")),
    }
    for relation, column in (
        ("payment_account", "payment_account_id"),
        ("tariff_plan", "tariff_plan_id"),
    ):
        row = state.dict.get(relation, original.get(relation))
        if row is not None and billing_key_after_flush(inspect(row))[1][0] == values[column]:
            values[relation] = row
    changes.authored_tariff_links[state] = AuthoredTariffLink(state.object, values)


def seeded_tariff_types(session: Session, inserted_tariffs, origins):
    connection = session.connection(bind_arguments={"mapper": TariffPlan})
    known_accounts = defaultdict(set)
    for model, key, _ in origins:
        if model is PaymentAccountTariffPlan:
            account_id, type_id = key
            known_accounts[type_id].add(account_id)

    linked_accounts = select(PaymentAccountTariffPlan.payment_account_id)
    for start in range(0, len(inserted_tariffs), 500):
        # A link to a genuinely new plan cannot predate this flush. EXISTS avoids
        # loading a potentially large fan-out just to discover the affected types.
        plans_with_links = connection.execute(
            select(TariffPlan.id, TariffPlan.tariff_plan_type_id).where(
                TariffPlan.id.in_(inserted_tariffs[start : start + 500]),
                linked_accounts.where(
                    PaymentAccountTariffPlan.tariff_plan_id == TariffPlan.id
                ).exists(),
            )
        )
        for plan_id, type_id in plans_with_links:
            tracked = known_accounts.get(type_id)
            if not tracked or any(
                account_id not in tracked
                for account_id in connection.scalars(
                    linked_accounts.where(PaymentAccountTariffPlan.tariff_plan_id == plan_id).limit(
                        len(tracked) + 1
                    )
                )
            ):
                yield type_id


@event.listens_for(Session, "after_flush")
def remember_billing_flush(session: Session, flush_context):
    rows = [
        (state, deleted)
        for state, (deleted, list_only) in flush_context.states.items()
        if state.mapper.class_ in BILLING_MODELS and not list_only
    ]
    if not rows:
        return
    changes = current_billing_changes(session)
    removed_keys = set()
    origins = {}
    new_accounts = {}
    inserted_tariffs = []
    for state, deleted in rows:
        old_key = state.key
        if deleted:
            changes.original_keys.setdefault(state, old_key)
            removed_keys.add(old_key)
            if state.mapper.class_ is PaymentAccountTariffPlan:
                changes.authored_tariff_links[state] = None
            continue
        key = billing_key_after_flush(state)
        if state.mapper.class_ is PaymentAccountTariffPlan:
            remember_authored_tariff_link(session, changes, state, key)
        if old_key is None:
            changes.inserted_states.add(state)
            previous = session.identity_map.get(key)
            row_switch = previous is not None and flush_context.states.get(inspect(previous)) == (
                True,
                True,
            )
            if row_switch:
                # The ORM replaced the instance but updated the existing database row.
                previous_state = inspect(previous)
                changes.original_keys.setdefault(previous_state, key)
                if isinstance(previous, PaymentAccountTariffPlan):
                    changes.authored_tariff_links[previous_state] = None
            if state.mapper.class_ is PaymentAccount:
                new_accounts[key[1][0]] = (state.object, not row_switch)
            if state.mapper.class_ is TariffPlan and not row_switch:
                inserted_tariffs.append(key[1][0])
            origins[key] = None
            continue
        changes.original_keys.setdefault(state, old_key)
        origins[key] = billing_row_origin(changes, old_key)
        if key != old_key:
            removed_keys.add(old_key)
    if new_accounts:
        expired = set()
        replaced = {
            account_id for account_id, (_, inserted) in new_accounts.items() if not inserted
        }
        changes.replaced_account_ids.update(replaced)
        flush_context.attributes["replaced_account_ids"] = replaced
        connection = session.connection(bind_arguments={"mapper": PaymentAccount})
        ids = tuple(new_accounts)
        # Bound the IN lists below SQLite's variable limit, even in large flushes.
        for start in range(0, len(ids), 500):
            assignments = connection.execute(
                select(
                    PaymentAccountTariffPlan.payment_account_id,
                    PaymentAccountTariffPlan.tariff_plan_type_id,
                ).where(PaymentAccountTariffPlan.payment_account_id.in_(ids[start : start + 500]))
            )
            for account_id, type_id in assignments:
                identity = session.identity_key(PaymentAccountTariffPlan, (account_id, type_id))
                if identity in origins:
                    continue
                account, inserted = new_accounts[account_id]
                expired.add(account)
                if inserted:
                    origins[identity] = None
        flush_context.attributes["accounts_to_expire"] = expired
    if inserted_tariffs and (types := set(seeded_tariff_types(session, inserted_tariffs, origins))):
        # All implicit links of a newly populated type are new. Track the type once,
        # including for loads that defer tariff_plan_id, without reading the whole fan-out.
        changes.seeded_tariff_types.update(types)
        flush_context.attributes["accounts_excluded_from_tariff_expiry"] = {
            account for account, _ in new_accounts.values()
        }
    # Resolve all rows from the pre-flush keys before publishing the resulting keys.
    changes.row_origins.update(dict.fromkeys(removed_keys))
    changes.row_origins.update(origins)


def record_billing_delete(session: Session, state: InstanceState):
    changes = current_billing_changes(session)
    changes.original_keys.setdefault(state, state.key)
    changes.row_origins[state.key] = None


def remember_reloaded_billing_state(state: InstanceState, _):
    for posted in active_billing_changes(state.session):
        # The latest write at this key identifies the row, even when a UUID was reused.
        original_key = billing_row_origin(posted, state.key)
        if original_key == state.key and state.key not in posted.row_origins:
            continue
        if original_key is None:
            posted.inserted_states.add(state)
            return
        posted.loaded_keys.setdefault(state, (state.key, original_key))
        if original_key != state.key:
            posted.original_keys.setdefault(state, None)
            return
        posted.original_keys.setdefault(state, original_key)


for billing_model in BILLING_MODELS:
    event.listen(billing_model, "load", remember_reloaded_billing_state, raw=True)
