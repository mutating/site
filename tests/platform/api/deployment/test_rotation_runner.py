"""Verify the GitHub-to-Ansible entry point and its credential lifecycle."""

import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("rotation_runner", ROOT / "deploy/rotate.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    """Use an isolated fake credential and runner directory instead of production access."""
    for name, value in {
        "DEPLOY_HOST": "server.invalid",
        "DEPLOY_USER": "deploy",
        "DEPLOY_PORT": "2222",
        "DEPLOY_PASSWORD": "test-password-do-not-log",
        "RUNNER_TEMP": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    return tmp_path


@pytest.mark.parametrize("exit_code", [0, 17])
def test_runner_uses_ansible_and_removes_credentials_on_success_or_failure(
    credentials, monkeypatch, exit_code, capsys
):
    """Neither process arguments nor inventory may expose the password, including failed runs."""
    captured = {}

    def execute(command, **options):
        password = Path(command[command.index("--connection-password-file") + 1])
        inventory = Path(command[command.index("-i") + 1])
        assert stat.S_IMODE(password.stat().st_mode) == 0o600
        assert password.read_text().strip() == "test-password-do-not-log"
        assert "test-password-do-not-log" not in json.dumps(command) + inventory.read_text()
        assert "DEPLOY_PASSWORD" not in options["env"]
        assert "SSHPASS" not in options["env"]
        assert json.loads(inventory.read_text())["all"]["hosts"]["production"] == {
            "ansible_host": "server.invalid",
            "ansible_user": "deploy",
            "ansible_port": 2222,
        }
        assert command[0] == "ansible-playbook"
        assert command[-1] == "rotation_release_id=reviewed-release"
        captured["directory"] = password.parent
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.run("reviewed-release") == exit_code
    assert not captured["directory"].exists()
    assert "test-password-do-not-log" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "setting,value",
    [
        ("DEPLOY_HOST", "host; injected"),
        ("DEPLOY_PORT", "65536"),
        ("DEPLOY_USER", "root bad"),
        ("DEPLOY_PASSWORD", ""),
    ],
)
def test_invalid_connection_settings_never_spawn_or_leave_credential_files(
    credentials, monkeypatch, setting, value
):
    """Reject malformed CI input before passing anything to a shell or network client."""
    monkeypatch.setenv(setting, value)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Invalid configuration must not start Ansible")

    monkeypatch.setattr(runner.subprocess, "run", unexpected)
    with pytest.raises(ValueError):
        runner.run("release")
    assert list(credentials.iterdir()) == []


def test_workflow_rotates_only_after_all_production_verifications():
    """A failed activation, content check or API check must skip automatic deletion."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    rotation_step = next(
        step for step in steps if step.get("run", "").startswith("python deploy/rotate.py")
    )
    index = steps.index(rotation_step)
    for name in [
        "Activate the release and verify startup",
        "Verify the deployed content contracts",
        "Verify API and platform through nginx",
    ]:
        assert next(i for i, step in enumerate(steps) if step.get("name") == name) < index
    assert rotation_step["if"] == "${{ success() }}"
    assert "${GITHUB_SHA}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in rotation_step["run"]
    playbook = yaml.safe_load((ROOT / "infra/playbooks/rotate-deployments.yml").read_text())[0]
    assert playbook["become"] is False
    command = next(task for task in playbook["tasks"] if "ansible.builtin.command" in task)[
        "ansible.builtin.command"
    ]["argv"]
    assert command[:3] == ["sudo", "-n", "/usr/local/sbin/mutating-activate"]
