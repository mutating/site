"""The supervisor can provision the registration secret without a server edit."""

import importlib.util
import stat
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]


def supervisor():
    spec = importlib.util.spec_from_file_location("deploy_run", ROOT / "deploy/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supervisor_creates_and_reuses_a_private_secret(tmp_path):
    """A code-only deployment gets a stable secret after the first restart."""
    settings = SimpleNamespace(enrollment_secret=None, storage_path=tmp_path / "artifacts")
    run = supervisor()

    first = run.ensure_enrollment_secret(settings)
    second = run.ensure_enrollment_secret(settings)

    assert first == second
    assert len(first) == 64
    secret = tmp_path / ".enrollment-secret"
    assert secret.read_text(encoding="utf-8").strip() == first
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_explicit_environment_secret_takes_precedence(tmp_path):
    """Production configuration remains authoritative when it is present."""
    configured = "x" * 64
    settings = SimpleNamespace(
        enrollment_secret=configured, storage_path=tmp_path / "artifacts"
    )

    assert supervisor().ensure_enrollment_secret(settings) == configured
    assert not (tmp_path / ".enrollment-secret").exists()
