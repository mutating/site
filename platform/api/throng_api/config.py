import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="THRONG_", env_file=os.environ.get("THRONG_ENV_FILE", ".env"), extra="ignore"
    )

    database_url: str = "sqlite+aiosqlite:///./data/throng.db"
    storage_path: Path = Path("./data/artifacts")
    allow_registration: bool = True
    controller_enabled: bool = True
    controller_interval: float = Field(default=1, ge=0.01, le=60)
    agent_lease_seconds: int = Field(default=60, ge=5, le=3600)
    isolate_ttl_seconds: int = Field(default=3600, ge=10, le=86400)
    provisioning_timeout_seconds: int = Field(default=300, ge=1, le=86400)
    task_ack_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    cancellation_timeout_seconds: int = Field(default=30, ge=1, le=3600)
    snapshot_timeout_seconds: int = Field(default=300, ge=1, le=86400)
    command_queue_timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    login_token_seconds: int = Field(default=86400, ge=60, le=31536000)
    browser_origins: list[str] = Field(default_factory=lambda: ["https://platform.mutating.tech"])
    browser_secure_cookie: bool = True
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    max_owner_storage_bytes: int = Field(default=1024 * 1024 * 1024, ge=1)
    max_owner_isolates: int = Field(default=10, ge=1)
    max_pending_commands_per_isolate: int = Field(default=1000, ge=1)
    max_results_response_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    long_poll_max_seconds: float = Field(default=25, ge=0, le=30)
    long_poll_interval: float = Field(default=0.25, ge=0.01, le=5)

    # Provision explicitly; startup never creates an untrusted development CA.
    powercell_acme_url: str | None = None
    powercell_ca_certificate: Path | None = None
    powercell_ca_key: Path | None = None
    powercell_secret: str | None = Field(default=None, min_length=32, repr=False)
    powercell_relay_url: str | None = None
    powercell_relay_certificate: Path | None = None
    powercell_relay_key: Path | None = None
    powercell_relay_host: str = "127.0.0.1"
    powercell_relay_keep_alive_seconds: int = Field(default=30, ge=5, le=300)
    enrollment_seconds: int = Field(default=3600, ge=60, le=86400)
    device_certificate_days: int = Field(default=365, ge=1, le=3650)
    device_proof_seconds: int = Field(default=300, ge=30, le=600)
    agent_mutation_seconds: int = Field(default=86400, ge=60, le=604800)
    max_diagnostic_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_computer_diagnostics_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    diagnostic_retention_days: int = Field(default=30, ge=1, le=365)

    github_app_id: int | None = Field(default=None, gt=0)
    github_app_slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,100}$")
    github_client_id: str | None = None
    github_client_secret: str | None = Field(default=None, repr=False)
    github_private_key_path: Path | None = None
    github_webhook_secret: str | None = Field(default=None, min_length=32, repr=False)
    github_token_key: str | None = Field(default=None, repr=False)
    github_public_api_url: str = "https://api.mutating.tech"
    github_dashboard_url: str = "https://platform.mutating.tech"
