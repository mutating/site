"""Versioned additions to the existing execution transport."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

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
    installation_id: UUID
    os: Literal["linux"] = "linux"
    architecture: Literal["arm64"] = "arm64"
    runtime: Literal["powercell-ubuntu-arm64-v1"] = "powercell-ubuntu-arm64-v1"
    isolation_modes: list[Isolation] = Field(min_length=1, max_length=2)


class RelayConfiguration(Input):
    url: str
    api_origin: str


class EnrollmentStatus(Input):
    id: str
    status: Literal["pending_attestation", "verified", "expired"]
    expires_at: datetime
    certificate_sha256: str | None
    relay: RelayConfiguration | None = None


class Binding(Input):
    computer_id: str
    credential: str
    device_id: str
    certificate_sha256: str
    relay: RelayConfiguration | None = None


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


class DiagnosticRecord(Input):
    id: UUID
    created: AwareDatetime
    message: str = Field(max_length=1_000_000)
    session_id: UUID | None = None
    task_id: UUID | None = None
    uploaded: bool = False


class DiagnosticAccepted(Input):
    id: str
    sha256: str
