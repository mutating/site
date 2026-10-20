"""Exercise deployment transfer failures, reuse and disconnected activation observers."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def deployment(tmp_path):
    """Build an isolated checkout and server with observable command substitutes."""
    checkout = tmp_path / "checkout"
    server = tmp_path / "server"
    binary = tmp_path / "bin"
    (checkout / "deploy").mkdir(parents=True)
    (server / "releases/new/platform/web/dist").mkdir(parents=True)
    (server / "releases/new/platform/web/dist/index.html").write_text("web")
    binary.mkdir()
    for name in ("upload.sh", "deploy.sh", "log.sh", "known_hosts"):
        text = (ROOT / "deploy" / name).read_text().replace("/opt/mutating", str(server))
        (checkout / "deploy" / name).write_text(text)
    log = tmp_path / "commands.jsonl"
    stub = binary / "stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as output:
    output.write(json.dumps([command, *args]) + '\\n')
if command == 'timeout':
    os.execvp(args[2], args[2:])
if command == 'sshpass':
    os.execvp(args[1], args[1:])
if command == 'ssh':
    if 'readlink -f' in args[-1]:
        print(os.environ.get('BASIS', ''))
    if os.environ.get('FAILURE') == 'ssh':
        sys.exit(255)
if command == 'rsync':
    time.sleep(float(os.environ.get('TRANSFER_DELAY', '0')))
    sys.exit(int(os.environ.get('TRANSFER_STATUS', '0')))
if command == 'uv' and os.environ.get('FAILURE') == 'uv':
    sys.exit(42)
if command == 'sudo':
    time.sleep(float(os.environ.get('ACTIVATION_DELAY', '0')))
    print('Activation worker finished', flush=True)
    sys.exit(int(os.environ.get('ACTIVATION_STATUS', '0')))
"""
    )
    stub.chmod(0o755)
    for name in ("timeout", "sshpass", "ssh", "rsync", "uv", "sudo", "chmod"):
        (binary / name).symlink_to(stub)
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DEPLOY_HOST": "server.invalid",
        "DEPLOY_PORT": "22",
        "DEPLOY_USER": "tester",
        "SSHPASS": "never-print-this-password",
        "BASIS": str(server / "releases/old"),
    }
    return checkout, server, binary, env, log


def run_upload(deployment, phase="all", **settings):
    """Run the real upload shell with a controlled transport."""
    checkout, _, _, env, log = deployment
    result = subprocess.run(
        ["bash", str(checkout / "deploy/upload.sh"), "new", phase],
        env={**env, **settings},
        capture_output=True,
        text=True,
        timeout=15,
    )
    actions = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert env["SSHPASS"] not in result.stdout + result.stderr
    return result, actions


@pytest.mark.parametrize("basis", ["previous", ""])
def test_upload_reuses_verified_content_and_keeps_original_one_command_entrypoint(
    deployment, basis
):
    """Reuse the server basis, expose transfer progress and keep preparation after upload."""
    _, server, _, _, _ = deployment
    result, actions = run_upload(deployment, BASIS=str(server / "releases/old") if basis else "")
    assert result.returncode == 0, result.stderr
    transfer = next(action for action in actions if action[0] == "rsync")
    assert "--checksum" in transfer and "--info=progress2" in transfer
    assert "--stats" in transfer and "--timeout=60" in transfer
    assert (f"--copy-dest={server}/releases/old" in transfer) == bool(basis)
    assert not any(arg.startswith("--link-dest") for arg in transfer)
    for excluded in (".env*", ".venv/", "node_modules/", "*.db", "tests/", ".runtime/"):
        assert f"--exclude={excluded}" in transfer
    remote = [action[-1] for action in actions if action[0] == "ssh"]
    assert len(remote) == 4
    assert "mv " in remote[1]
    assert remote[2].endswith("'new' prepare")
    assert remote[3].endswith("'new' activate")
    assert "Upload (deadline 480s, idle timeout 60s): completed" in result.stdout


@pytest.mark.parametrize("status", [23, 30, 124])
def test_failed_transfer_does_not_finalize_prepare_or_activate(deployment, status):
    """Propagate transfer errors and timeouts without ever reaching activation."""
    result, actions = run_upload(deployment, TRANSFER_STATUS=str(status))
    assert result.returncode == status
    assert sum(action[0] == "ssh" for action in actions) == 1
    assert "::error::Upload" in result.stderr
    assert f"exit {status}" in result.stderr


@pytest.mark.parametrize("basis", ["/etc", "/opt/mutating/releases/../../etc", "bad\npath"])
def test_unexpected_server_basis_is_rejected_before_rsync(deployment, basis):
    """Never interpolate an untrusted remote path into rsync or the remote shell."""
    result, actions = run_upload(deployment, BASIS=basis)
    assert result.returncode == 2
    assert not any(action[0] == "rsync" for action in actions)


def test_ssh_preflight_failure_is_reported_without_upload(deployment):
    """Name SSH authentication/connection failures before attempting any transfer."""
    result, actions = run_upload(deployment, FAILURE="ssh")
    assert result.returncode == 255
    assert "SSH preflight failed" in result.stderr
    assert not any(action[0] == "rsync" for action in actions)


@pytest.mark.parametrize("phase", ["upload", "prepare", "activate"])
def test_ci_can_run_each_deployment_phase_separately(deployment, phase):
    """Keep upload, dependency preparation and activation visible as separate CI steps."""
    result, actions = run_upload(deployment, phase)
    assert result.returncode == 0, result.stderr
    assert any(action[0] == "rsync" for action in actions) == (phase == "upload")
    if phase != "upload":
        assert [action[-1] for action in actions if action[0] == "ssh"] == [
            f"bash '{deployment[1]}/releases/new/deploy/deploy.sh' 'new' {phase}"
        ]


def test_prepare_failure_never_starts_activation(deployment):
    """A failed Python installation leaves the running service alone."""
    checkout, _, _, env, log = deployment
    result = subprocess.run(
        ["bash", str(checkout / "deploy/deploy.sh"), "new"],
        env={**env, "FAILURE": "uv"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42
    assert "Install Python 3.12" in result.stderr
    actions = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(action[0] == "sudo" for action in actions)


@pytest.mark.parametrize("status", [0, 17])
def test_activation_status_and_log_survive_disconnected_observer(deployment, status):
    """Disconnect the monitor, let maintenance finish, and reattach without activating twice."""
    checkout, server, _, env, log = deployment
    script = checkout / "deploy/deploy.sh"
    command = ["bash", str(script), "new", "activate"]
    worker_env = {**env, "ACTIVATION_DELAY": "1", "ACTIVATION_STATUS": str(status)}
    observer = subprocess.Popen(
        command,
        env=worker_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    state = server / "releases/new/.activation"
    deadline = time.monotonic() + 10
    try:
        while not log.exists() or not any(
            json.loads(line)[0] == "sudo" for line in log.read_text().splitlines()
        ):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        os.killpg(observer.pid, signal.SIGHUP)
        observer.wait(timeout=5)
        while not (state / "status").exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert (state / "status").read_text().strip() == str(status)
        result = subprocess.run(command, env=worker_env, capture_output=True, text=True, timeout=5)
        assert result.returncode == status, result.stderr
        assert "Activation worker finished" in result.stdout
        actions = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(action[0] == "sudo" for action in actions) == 1
    finally:
        if observer.poll() is None:
            os.killpg(observer.pid, signal.SIGKILL)
            observer.wait(timeout=5)


def test_total_upload_deadline_catches_a_slow_trickle(deployment):
    """Enforce the total deadline even when an I/O idle timeout would never fire."""
    real_timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if not real_timeout:
        pytest.skip("GNU timeout is required (the deployment runner is Linux)")
    checkout, _, binary, _, _ = deployment
    (binary / "timeout").unlink()
    (binary / "timeout").symlink_to(real_timeout)
    script = checkout / "deploy/upload.sh"
    script.write_text(script.read_text().replace("480s", "1s"))
    started = time.monotonic()
    result, actions = run_upload(deployment, TRANSFER_DELAY="20")
    assert result.returncode == 124, result.stderr
    assert time.monotonic() - started < 10
    assert sum(action[0] == "ssh" for action in actions) == 1


def test_rsync_copies_unchanged_bytes_locally_without_sharing_inodes(tmp_path):
    """A fresh checkout mtime and same-size changed file cannot fool basis reuse."""
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("rsync is required")
    source, basis, target = (tmp_path / name for name in ("source", "basis", "target"))
    source.mkdir()
    basis.mkdir()
    target.mkdir()
    for name, content in (("image.webp", b"unchanged" * 10000), ("changed.txt", b"new!")):
        (source / name).write_bytes(content)
        (basis / name).write_bytes(b"old!" if name == "changed.txt" else content)
        os.utime(source / name, (2000000000, 2000000000))
        os.utime(basis / name, (1000000000, 1000000000))
    result = subprocess.run(
        [
            rsync,
            "--archive",
            "--checksum",
            "--stats",
            f"--copy-dest={basis}",
            str(source) + "/",
            str(target) + "/",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (target / "changed.txt").read_bytes() == b"new!"
    assert (target / "image.webp").read_bytes() == (source / "image.webp").read_bytes()
    assert (target / "image.webp").stat().st_ino != (basis / "image.webp").stat().st_ino
    assert "Literal data: 4 bytes" in result.stdout, result.stdout
    (target / "image.webp").write_bytes(b"new release edit")
    assert (basis / "image.webp").read_bytes() == (source / "image.webp").read_bytes()


def test_long_stage_emits_heartbeat_and_preserves_command_failure(deployment):
    """A silent operation remains observable and cannot be mistaken for success."""
    checkout, _, _, env, _ = deployment
    helper = checkout / "deploy/log.sh"
    helper.write_text(helper.read_text().replace("sleep 15", "sleep 0.05"))
    command = f'source "{helper}"; deploy_stage "Slow stage" bash -c "sleep 0.2; exit 7"'
    result = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 7
    assert "Slow stage: started" in result.stdout
    assert "Slow stage: still running" in result.stdout
    assert "Slow stage failed" in result.stderr
    assert "exit 7" in result.stderr
