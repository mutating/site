from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates
from sqlalchemy.types import TypeDecorator

from throng_api.organization_names import name_key, normalize_name


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Timezone-aware datetime required")
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


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


class User(Identity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str | None] = mapped_column(String(64), unique=True)
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_version: Mapped[str | None] = mapped_column(String(64))
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Company(Identity, Base):
    __tablename__ = "companies"
    name: Mapped[str] = mapped_column(String(200))
    name_search: Mapped[str] = mapped_column(Text, server_default="")
    name_key: Mapped[str] = mapped_column(Text, server_default="")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

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
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
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
    company_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
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
    """Unclassified historical credentials retain their original authority."""

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
    )
    name: Mapped[str | None] = mapped_column(String(200))
    registered_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
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
    device_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    certificate_sha256: Mapped[str | None] = mapped_column(String(64))
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
        UniqueConstraint("computer_id", "benchmark_name", "benchmarker_version"),
        CheckConstraint("number >= 0", name="nonnegative_score"),
    )
    computer_id: Mapped[str] = mapped_column(
        ForeignKey("computers.id", ondelete="CASCADE"), index=True
    )
    benchmark_name: Mapped[str] = mapped_column(String(200))
    number: Mapped[float] = mapped_column(Float)
    benchmarker_version: Mapped[str] = mapped_column(String(200))


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


class ComputerOwnership(Identity, Ownership, Base):
    __tablename__ = "computer_ownerships"
    __table_args__ = (owner_check(),)
    computer_id: Mapped[str] = mapped_column(ForeignKey("computers.id"), unique=True)


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


class PhysicalDevice(Identity, Base):
    """Billing identity survives installation, account and computer deletion."""

    __tablename__ = "physical_devices"
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)


class AgentEnrollment(Identity, Base):
    __tablename__ = "agent_enrollments"
    actor_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    installation_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    request: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    acme_secret: Mapped[str] = mapped_column(String(64))
    account_jwk: Mapped[dict | None] = mapped_column(JSON)
    order_created: Mapped[bool] = mapped_column(Boolean, default=False)
    challenge_token: Mapped[str] = mapped_column(String(64))
    attested_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    identity_hash: Mapped[str | None] = mapped_column(String(64))
    certificate_sha256: Mapped[str | None] = mapped_column(String(64))
    csr_sha256: Mapped[str | None] = mapped_column(String(64))
    computer_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    finish_key: Mapped[str | None] = mapped_column(String(128))


class DeviceCertificate(Base):
    __tablename__ = "device_certificates"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("physical_devices.id"))
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("agent_enrollments.id"), unique=True)
    pem: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ProtocolNonce(Base):
    __tablename__ = "protocol_nonces"
    namespace: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AgentMutation(Base):
    """Exact bounded retries, including disconnect and revoked-credential unbinding."""

    __tablename__ = "agent_mutations"
    credential_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    certificate_sha256: Mapped[str | None] = mapped_column(String(64))
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
