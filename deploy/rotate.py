"""Invoke the post-deployment Ansible rotation with private, temporary credentials."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(release_id):
    host = os.environ.get("DEPLOY_HOST", "")
    user = os.environ.get("DEPLOY_USER", "")
    port = os.environ.get("DEPLOY_PORT", "")
    password = os.environ.get("DEPLOY_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id):
        raise ValueError("Invalid release ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
        raise ValueError("Invalid deployment host")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise ValueError("Invalid deployment user")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        raise ValueError("Invalid deployment port")
    if not password:
        raise ValueError("Missing deployment password")
    with tempfile.TemporaryDirectory(
        prefix="deployment-rotation-", dir=os.environ.get("RUNNER_TEMP")
    ) as temporary:
        directory = Path(temporary)
        password_file = directory / "connection-password"
        with os.fdopen(
            os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as output:
            output.write(password + "\n")
        inventory = directory / "inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "all": {
                        "hosts": {
                            "production": {
                                "ansible_host": host,
                                "ansible_user": user,
                                "ansible_port": int(port),
                            }
                        }
                    }
                }
            )
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DEPLOY_PASSWORD", "SSHPASS"}
        }
        environment["ANSIBLE_LOCAL_TEMP"] = str(directory / "ansible")
        return subprocess.run(
            [
                "ansible-playbook",
                "-i",
                str(inventory),
                str(ROOT / "infra/playbooks/rotate-deployments.yml"),
                "--connection-password-file",
                str(password_file),
                "-e",
                f"rotation_release_id={release_id}",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        ).returncode


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: rotate.py RELEASE_ID")
        sys.exit(run(sys.argv[1]))
    except ValueError as error:
        print(f"Rotation configuration error: {error}", file=sys.stderr)
        sys.exit(2)
