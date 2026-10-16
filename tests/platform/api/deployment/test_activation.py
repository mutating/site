"""Exercise service activation failures without administrative access or a live server."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "failure", ["", "backup", "migrate", "health", "unsafe", "concurrent", "protected"]
)
def test_activation_preserves_the_database_and_current_release_on_failure(tmp_path, failure):
    """Recover before startup, preserve accepted writes afterwards and only switch migrated code."""
    deploy_root = tmp_path / "server"
    releases = deploy_root / "releases"
    old = releases / "old"
    new = releases / "new"
    old.mkdir(parents=True)
    new.mkdir()
    (new / "site/public").mkdir(parents=True)
    (new / "site/public/index.html").write_text("site")
    current = deploy_root / "current"
    current.symlink_to(old)
    server_env = tmp_path / "server.env"
    server_env.touch()
    if failure in {"unsafe", "protected"}:
        server_env.write_text('THRONG_POWERCELL_RELAY_URL="https://api.example:8444"\n')
        (new / "deploy").mkdir()
        (new / "deploy/run.py").write_text(
            "launch('--no-access-log')\n"
            if failure == "protected"
            else "# Previous supervisor without ACME protection\n"
        )
    activation = tmp_path / "activate.sh"
    activation.write_text(
        (ROOT / "deploy/activate.sh")
        .read_text()
        .replace('deploy_root="/opt/mutating"', f'deploy_root="{deploy_root}"')
        .replace('server_env="/etc/throng/throng.env"', f'server_env="{server_env}"')
    )
    log = tmp_path / "actions.jsonl"
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    stub = binary_directory / "stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["ACTION_LOG"], "a") as output:
    output.write(json.dumps([command, *args]) + "\\n")
failure = os.environ["FAILURE"]
if command == "runuser":
    action = "migrate" if "migrate" in args else ("restore" if "restore" in args else "backup")
    sys.exit(1 if failure == action else 0)
if command == "curl":
    sys.exit(1 if failure == "health" else 0)
if command == "seq":
    print("1\\n2")
elif command == "mv":
    os.replace(args[-2], args[-1])
"""
    )
    stub.chmod(0o755)
    for command in (
        "flock",
        "systemctl",
        "runuser",
        "curl",
        "seq",
        "sleep",
        "journalctl",
        "mv",
        "rsync",
    ):
        (binary_directory / command).symlink_to(stub)
    result = subprocess.run(
        ["bash", str(activation), "new"],
        env={
            **os.environ,
            "PATH": f"{binary_directory}:{os.environ['PATH']}",
            "ACTION_LOG": str(log),
            "FAILURE": failure,
            "MUTATING_EXPECTED_CURRENT": str(releases / "another")
            if failure == "concurrent"
            else "",
        },
        text=True,
        capture_output=True,
    )
    actions = [json.loads(line) for line in log.read_text().splitlines()]
    if failure in {"unsafe", "concurrent"}:
        assert result.returncode == 2, result.stderr
        assert current.resolve() == old
        assert all(action[0] == "flock" for action in actions)
        return
    maintenance = [action for action in actions if action[0] == "runuser"]
    stops = [action for action in actions if action[:2] == ["systemctl", "stop"]]
    starts = [action for action in actions if action[:2] == ["systemctl", "start"]]
    assert stops and actions.index(stops[0]) < actions.index(maintenance[0])
    assert result.returncode == (0 if failure in {"", "protected"} else 1), result.stderr
    assert current.resolve() == (old if failure in {"backup", "migrate"} else new)
    assert any("restore" in action for action in maintenance) == (failure == "migrate")
    assert any("migrate" in action for action in maintenance) == (failure != "backup")
    assert len(starts) == 1
    assert len(stops) == (2 if failure == "health" else 1)
