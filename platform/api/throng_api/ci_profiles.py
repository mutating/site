"""Sparse workflow resource overrides; identity always comes from verified OIDC claims."""

from pydantic import Field, model_validator

from throng_api.schemas import Input

GiB = 1024**3
PROFILE_FIELDS = (
    "cpu_millis",
    "memory_bytes",
    "disk_bytes",
    "computer_ids",
    "preparation_seconds",
    "timeout_seconds",
)


class ProfileOverride(Input):
    cpu_millis: int | None = Field(default=None, gt=0, le=1000000)
    memory_bytes: int | None = Field(default=None, ge=16 * 1024**2, le=2**50)
    disk_bytes: int | None = Field(default=None, ge=GiB, le=1024 * GiB)
    computer_ids: list[str] | None = Field(default=None, max_length=100)
    preparation_seconds: int | None = Field(default=None, ge=10, le=300)
    timeout_seconds: int | None = Field(default=None, ge=60, le=21600)

    @model_validator(mode="after")
    def explicit_values(self):
        from uuid import UUID

        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Omit inherited fields instead of setting them to null")
        if self.computer_ids is not None:
            self.computer_ids = sorted({str(UUID(value)) for value in self.computer_ids})
        return self


def profile_values(data):
    return {
        path: profile.model_dump(exclude_none=True)
        for path, profile in sorted(data.workflow_profiles.items())
    }


def selected_computers(data):
    return set(map(str, data.computer_ids)) | {
        computer
        for profile in data.workflow_profiles.values()
        for computer in profile.computer_ids or []
    }


def effective_profile(route, workflow_path, fields=PROFILE_FIELDS):
    """Edits are locked until all reservations are cleaned, preserving running policies."""
    overrides = (getattr(route, "workflow_profiles", None) or {}).get(workflow_path, {})
    return {name: overrides.get(name, getattr(route, name)) for name in fields}
