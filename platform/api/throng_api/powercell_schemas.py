"""Versioned additions to the existing execution transport."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from throng_api.schemas import ComputerCreate, Input

Budget = Annotated[int, Field(ge=0, le=2**60, strict=True)]
Isolation = Literal["vm", "shared"]
Reason = Literal[
    "capacity_reduced",
    "isolate_lost",
    "disabled",
    "battery",
    "thermal",
    "user_switched",
    "sleep",
    "disk_unavailable",
    "agent_quit",
    "logout",
    "lease_expired",
    "memory_pressure",
]


class EnrollmentCreate(ComputerCreate):
    device_id: UUID | None = None
    installation_id: UUID | None = None
    assign_pool: bool = False
    os: Literal["linux"] = "linux"
    architecture: Literal["arm64"] = "arm64"
    runtime: Literal["powercell-ubuntu-arm64-v1"] = "powercell-ubuntu-arm64-v1"
    isolation_modes: list[Isolation] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def identify_device(self):
        if self.device_id is None and self.installation_id is None:
            raise ValueError("device_id is required")
        return self

    @property
    def client_device_id(self) -> UUID:
        return self.device_id or self.installation_id


class EnrollmentStatus(Input):
    id: str
    status: Literal["verified", "expired"]
    expires_at: datetime


class EnrollmentFinish(Input):
    credential_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Binding(Input):
    computer_id: str
    device_id: str


class Capacity(Input):
    image_families: list[Literal["ghcr.io/actions/actions-runner"]] = Field(
        default_factory=list, max_length=1
    )
    revision: int = Field(ge=0, le=2**63 - 1, strict=True)
    accepting_work: bool
    mode: Literal["active", "idle", "suspended"]
    reason: str | None = Field(default=None, max_length=80)
    cpu_millis: int = Field(ge=0, le=1_000_000, strict=True)
    memory_bytes: Budget
    disk_bytes: Budget
    default_isolation: Isolation = "vm"
    network_enabled: bool = False
    internet_only: bool = True
    running_commands: int = Field(ge=0, le=1_000_000, strict=True)
    cpu_used_millis: Budget
    memory_used_bytes: Budget


class CapacityAccepted(Input):
    revision: int
    accepted: Literal[True] = True


class Cleanup(Input):
    cleanup_confirmed: Literal[True]


class Disconnect(Cleanup):
    reason: Reason


class Eviction(Disconnect):
    retry_allowed: bool = True


class NetworkAttempt(Input):
    created: AwareDatetime | None = None
    operation_id: UUID
    request_id: UUID
    method: str = Field(max_length=16)
    path: str = Field(max_length=2048)
    transport: Literal["direct", "relay"]
    session_id: UUID | None = None
    attempt: int = Field(ge=1, le=3)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    status: int | None = Field(default=None, ge=100, le=599)
    error_domain: str | None = Field(default=None, max_length=128)
    error_code: int | None = None
    server_code: str | None = Field(default=None, max_length=128)


class DiagnosticRecord(Input):
    id: UUID
    created: AwareDatetime
    message: str = Field(max_length=1_000_000)
    session_id: UUID | None = None
    task_id: UUID | None = None
    uploaded: bool = False
    network: NetworkAttempt | None = None


class DiagnosticAccepted(Input):
    id: str
    sha256: str
