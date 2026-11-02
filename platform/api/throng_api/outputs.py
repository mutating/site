"""Explicit public contracts for OpenAPI and future plugin/agent clients."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from throng_api.permissions import Permission
from throng_api.schemas import Role, Scope

CommandStatus = Literal[
    "queued",
    "dispatched",
    "running",
    "cancel_requested",
    "completed",
    "cancelled",
    "timed_out",
    "isolate_failed",
    "failed",
]


class Identity(BaseModel):
    id: str
    created_at: datetime


class TaskReceipt(BaseModel):
    accepted: bool
    active: bool
    task_status: str


class BenchmarkResult(Identity):
    computer_id: str
    run_id: str
    benchmark_name: str
    number: float
    benchmarker_version: str


class Owner(BaseModel):
    user_id: str | None
    company_id: str | None


class Company(Identity):
    name: str
    name_key: str = ""
    created_by_user_id: str
    my_role: Role | None = None
    my_permissions: list[Permission] = []
    my_membership_id: str | None = None
    computer_count: int = 0
    member_count: int = 0


class MemberProfile(BaseModel):
    id: str
    first_name: str
    last_name: str
    nickname: str | None
    avatar_version: str | None


class Membership(Identity):
    user_id: str
    company_id: str
    role: Role
    permissions: list[Permission] | None = None
    effective_permissions: list[Permission] = []
    user: MemberProfile | None = None


class Invitation(Identity):
    company_id: str
    company_name: str
    created_by_user_id: str
    invitee_user_id: str | None
    email: str | None
    role: Role
    permissions: list[Permission] | None
    effective_permissions: list[Permission]
    expires_at: datetime
    status: Literal["pending", "accepted", "declined", "revoked", "expired"]
    accepted_by_user_id: str | None
    computer_count: int = 0
    member_count: int = 0
    name_key: str = ""


class DirectoryCompany(BaseModel):
    kind: Literal["company"]
    group: Literal[1, 2]
    company: Company


class DirectoryInvitation(BaseModel):
    kind: Literal["invitation"]
    group: Literal[0]
    invitation: Invitation


class OrganizationDirectory(BaseModel):
    items: list[DirectoryCompany | DirectoryInvitation]
    total: int
    total_all: int


class CreatedInvitation(Invitation):
    token: str


class TokenOwnership(Identity, Owner):
    pass


class AccessToken(Identity):
    ownership_id: str
    created_by_user_id: str
    name: str
    purpose: Literal["api", "login", "legacy"]
    scopes: list[Scope]
    expires_at: datetime | None
    revoked_at: datetime | None


class IssuedToken(AccessToken):
    token: str


class ApiToken(AccessToken, Owner):
    purpose: Literal["api"]
    issuer: MemberProfile


class IssuedApiToken(ApiToken):
    token: str


class LoginSession(Identity):
    user_id: str
    name: str
    scopes: list[Scope]
    expires_at: datetime
    revoked_at: datetime | None


class Login(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    token: LoginSession


class TokenAuthenticationError(BaseModel):
    detail: str
    code: Literal["token_invalid", "token_expired"]


class TokenCheck(BaseModel):
    valid: Literal[True]
    scopes: list[Scope]


class ComputerOwnership(Identity, Owner):
    computer_id: str


class Computer(Identity):
    name: str | None
    registered_by_user_id: str
    os: str
    architecture: str
    runtime: str
    cpu_millis: int
    memory_bytes: int
    disk_bytes: int
    reserved_cpu: int
    reserved_memory: int
    reserved_disk: int
    free_cpu: int
    free_memory: int
    free_disk: int
    accepting_work: bool
    capacity_revision: int
    capacity_report: dict | None
    target_cpu: int | None
    target_memory: int | None
    target_disk: int | None
    default_isolation: str
    network_enabled: bool
    internet_only: bool
    device_id: str | None
    certificate_sha256: str | None
    enabled: bool
    online: bool
    session_id: str | None
    last_seen_at: datetime | None
    lease_expires_at: datetime | None


class RegisteredComputer(Computer):
    ownership: ComputerOwnership


class AgentCredential(BaseModel):
    computer_id: str
    token: str


class Artifact(Identity, Owner):
    filename: str
    size_bytes: int
    sha256: str


class Isolate(Identity, Owner):
    isolation_mode: Literal["vm", "shared"] | None
    effective_isolation: Literal["vm", "shared"] | None
    retry_allowed: bool
    network_enabled: bool | None
    internet_only: bool | None
    status: Literal["provisioning", "ready", "destroying", "destroyed", "failed"]
    computer_id: str | None
    session_id: str | None
    state_artifact_id: str
    image: str
    os: str
    architecture: str
    runtime: str
    cpu_millis: int
    memory_bytes: int
    disk_bytes: int
    reserved: bool
    expires_at: datetime
    provisioning_deadline: datetime
    error: str | None
    destroyed_at: datetime | None
    next_sequence: int


class Command(Identity):
    isolate_id: str
    sequence: int
    command: str
    shell: bool
    cwd: str
    env: dict[str, str]
    status: CommandStatus
    timeout_seconds: int
    queued_deadline: datetime
    dispatched_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    cancel_reason: str | None


class Status(BaseModel):
    id: str
    isolate_id: str
    status: CommandStatus
    terminal: bool
    started_at: datetime | None
    finished_at: datetime | None
    cancel_reason: str | None


class Result(BaseModel):
    command_id: str
    success: bool
    returncode: int | None
    stdout: str | None
    stderr: str | None
    error: str | None
    stdout_artifact_id: str | None
    stderr_artifact_id: str | None
    created_at: datetime


class ResultEnvelope(BaseModel):
    id: str
    status: CommandStatus
    result: Result | None


class AgentTask(Identity):
    computer_id: str
    session_id: str
    isolate_id: str
    command_id: str | None
    kind: Literal["create", "run", "cancel", "destroy", "snapshot"]
    status: Literal["pending", "dispatched", "accepted", "completed", "failed", "obsolete"]
    payload: dict[str, Any]
    deadline: datetime
    delivered_at: datetime | None
    accepted_at: datetime | None
    finished_at: datetime | None
    artifact_id: str | None
    error: str | None


class AgentSession(BaseModel):
    protocol_version: Literal[1]
    computer_id: str
    session_id: str
    server_time: datetime
    lease_expires_at: datetime


class CurrentAgentSession(BaseModel):
    computer_id: str
    session_id: str | None
    server_time: datetime
    lease_expires_at: datetime | None
    active: bool


class ClaimedTasks(AgentSession):
    tasks: list[AgentTask]


class ResourceType(Identity):
    name: str
    description: str
    code: str
    unit: str
    builtin: bool


class ComputerResource(Identity):
    computer_id: str
    resource_type_id: str
    resource_type: ResourceType
    total: int
    occupied: int
    free: int
    version: int


class ResourceAmount(BaseModel):
    resource_type_id: str
    name: str
    code: str
    unit: str
    total: int
    occupied: int
    free: int


class ResourceSummary(BaseModel):
    as_of: datetime
    only_available: bool
    resources: list[ResourceAmount]
    computer_count: int


class CapacityPoint(BaseModel):
    at: datetime
    cpu_millis: int
    memory_bytes: int
    cpu_occupied_millis: int
    memory_occupied_bytes: int


class CapacityHistory(BaseModel):
    from_time: datetime
    to_time: datetime
    as_of: datetime
    available_from: datetime
    points: list[CapacityPoint]


class ResourceHistoryPoint(BaseModel):
    at: datetime
    revision: int
    resources: list[ResourceAmount]


class ResourceHistory(BaseModel):
    from_time: datetime
    to_time: datetime
    available_from: datetime | None
    initial_at: datetime | None
    initial: list[ResourceAmount]
    points: list[ResourceHistoryPoint]
    snapshot_revision: int
    next_after_revision: int | None
