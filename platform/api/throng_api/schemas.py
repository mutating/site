from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from throng_api.permissions import Permission

Scope = Literal["read", "manage", "execute"]
Role = Literal["creator", "admin", "user"]
Positive = Annotated[int, Field(gt=0, le=2**60)]
Password = Annotated[str, StringConstraints(strip_whitespace=False)]
PaginationOffset = Annotated[int, Field(ge=0, le=2**63 - 1)]


def storage_text(value: str) -> str:
    """Accept Unicode text that both supported databases can store without lossy conversion."""
    if "\x00" in value:
        raise ValueError("Text cannot contain NUL; use a binary artifact for binary data")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Text must contain valid Unicode scalar values") from error
    return value


StorageText = Annotated[str, AfterValidator(storage_text)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def valid_text(cls, data):
        pending = [data]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                storage_text(value)
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return data


class UserCreate(Input):
    email: EmailStr
    password: Password = Field(min_length=12, max_length=1024)
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    nickname: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

    @field_validator("email", "nickname")
    @classmethod
    def normalize(cls, value):
        return value.casefold() if value is not None else None


class UserUpdate(Input):
    email: EmailStr | None = None
    password: Password | None = Field(default=None, min_length=12, max_length=1024)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    nickname: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

    @field_validator("email", "nickname")
    @classmethod
    def normalize(cls, value):
        return value.casefold() if value is not None else None


class Login(Input):
    login: str = Field(
        min_length=1, max_length=320, validation_alias=AliasChoices("login", "email")
    )
    password: Password = Field(max_length=1024)


class CompanyInput(Input):
    name: str = Field(min_length=1, max_length=200)


class MembershipCreate(Input):
    user_id: UUID | None = None
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    company_id: UUID
    role: Role = "user"
    permissions: list[Permission] | None = Field(default=None, max_length=9)

    @model_validator(mode="after")
    def one_user(self):
        if (self.user_id is None) == (self.identifier is None):
            raise ValueError("Choose user_id or identifier")
        return self


class MembershipUpdate(Input):
    role: Role | None = None
    permissions: list[Permission] | None = Field(default=None, max_length=9)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or ("role" in self.model_fields_set and self.role is None):
            raise ValueError("Set a role or permissions")
        return self


class InvitationCreate(Input):
    company_id: UUID
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    role: Role = "user"
    permissions: list[Permission] | None = Field(default=None, max_length=9)
    expires_in_seconds: int = Field(default=604800, ge=3600, le=2592000)


class OwnerInput(Input):
    user_id: UUID | None = None
    company_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.user_id is None) == (self.company_id is None):
            raise ValueError("Exactly one of user_id and company_id is required")
        return self


class ComputerCreate(Input):
    name: str | None = Field(default=None, max_length=200)
    company_id: UUID | None = None
    os: str = Field(min_length=1, max_length=40)
    architecture: str = Field(min_length=1, max_length=40)
    runtime: str = Field(min_length=1, max_length=80)
    cpu_millis: int = Field(gt=0, le=1_000_000)
    memory_bytes: Positive
    disk_bytes: Positive


class ComputerUpdate(Input):
    name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    cpu_millis: int | None = Field(default=None, gt=0, le=1_000_000)
    memory_bytes: Positive | None = None
    disk_bytes: Positive | None = None


class ComputerOwnerCreate(OwnerInput):
    computer_id: UUID


class TokenCreate(Input):
    ownership_id: UUID
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1, max_length=3)
    expires_in_seconds: int = Field(default=86400, ge=60, le=31536000)


class TokenUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    revoke: bool = False


class IsolateCreate(Input):
    isolation_mode: Literal["vm", "shared"] | None = None
    retry_allowed: bool = True
    company_id: UUID | None = None
    state_artifact_id: UUID
    image: str = Field(
        default="ubuntu",
        min_length=1,
        max_length=300,
        description=(
            "Image reference; defaults to ubuntu when omitted. "
            "For powercell-ubuntu-arm64-v1, aliases ubuntu and actions "
            "resolve to ubuntu:24.04 and ghcr.io/actions/actions-runner:latest; "
            "responses contain the full reference. Explicit runner tags/digests are also accepted."
        ),
    )
    os: str = Field(min_length=1, max_length=40)
    architecture: str = Field(min_length=1, max_length=40)
    runtime: str = Field(min_length=1, max_length=80)
    cpu_millis: int = Field(gt=0, le=1_000_000)
    memory_bytes: Positive
    disk_bytes: Positive

    @model_validator(mode="after")
    def supported_powercell_image(self):
        from throng_api.workload_images import IMAGE_ALIASES, RUNNER_IMAGE

        if self.runtime == "powercell-ubuntu-arm64-v1":
            self.image = IMAGE_ALIASES.get(self.image, self.image)
            if self.image != "ubuntu:24.04" and not RUNNER_IMAGE.fullmatch(self.image):
                raise ValueError(
                    "Power Cell supports bundled Ubuntu and official GitHub runner images"
                )
        return self


class CommandCreate(Input):
    # Commands, env and passwords must preserve their content, including spaces.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    command: str = Field(min_length=1, max_length=65536)
    shell: bool = False
    cwd: str = Field(default=".", min_length=1, max_length=1024)
    env: dict[str, str] = Field(default_factory=dict, max_length=100)
    timeout_seconds: int = Field(default=600, ge=1, le=86400)

    @field_validator("command")
    @classmethod
    def valid_command(cls, value):
        if not value.strip():
            raise ValueError("Command must be nonempty")
        return value

    @field_validator("cwd")
    @classmethod
    def relative_directory(cls, value):
        if (
            PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            or any(c in value for c in ("\\", ":"))
        ):
            raise ValueError("cwd must be a relative path inside the isolate")
        return value

    @field_validator("env")
    @classmethod
    def valid_env(cls, value):
        for key, item in value.items():
            if not key or len(key) > 200 or "=" in key or len(item) > 8192:
                raise ValueError("Invalid environment variable")
        return value


class IDs(Input):
    ids: list[UUID] = Field(min_length=1, max_length=200)


class SessionStart(Input):
    protocol_version: Literal[1] = 1
    clean_start: Literal[True]
    previous_session_id: UUID | None = None


class Claim(Input):
    wait_seconds: float = Field(default=25, ge=0, le=30)
    limit: int = Field(default=20, ge=1, le=100)


class TaskComplete(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    outcome: Literal["completed", "cancelled", "timed_out", "failed"] = "completed"
    returncode: int | None = Field(default=None, ge=-(2**31), le=2**31 - 1)
    stdout: str | None = Field(default=None, max_length=1_048_576)
    stderr: str | None = Field(default=None, max_length=1_048_576)
    error: str | None = Field(default=None, max_length=8192)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    first_name: str
    last_name: str
    nickname: str | None
    avatar_version: str | None
    is_system_admin: bool
    created_at: datetime


class ResourceTypeCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=8192)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    unit: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")


class ResourceTypeUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=8192)


class ComputerResourceCreate(Input):
    resource_type_id: UUID
    total: int = Field(ge=0, le=2**60, strict=True)


class ComputerResourceUpdate(Input):
    total: int = Field(ge=0, le=2**60, strict=True)
    expected_version: int | None = Field(default=None, ge=1)
