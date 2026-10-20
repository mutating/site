"""Exercise the real Ansible migration, its backup and rollback on disposable files."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PLAYBOOK = ROOT / "infra/playbooks/update-site-ui-cache.yml"
LEGACY = "^/(?:css|js)/.+"
CURRENT = "^/(?:css|js|ui/styles)/.+"
pytestmark = pytest.mark.skipif(
    not shutil.which("ansible-playbook"), reason="Requires Ansible; installed in deployment CI"
)


@pytest.fixture
def nginx_site(tmp_path):
    """Keep nginx configuration and reloads isolated from the host machine."""
    config = tmp_path / "available/mutating.tech-tls"
    enabled = tmp_path / "enabled/mutating.tech"
    config.parent.mkdir()
    enabled.parent.mkdir()
    config.write_text(
        "server {\n    server_name mutating.tech;\n    root /var/www/mutating.tech;\n"
        '    location ~* "^/(?:css|js)/.+\\.[0-9a-f]{64}\\.(?:css|js)$" {\n'
        '        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
        "    }\n}\n# Preserve the existing TLS, redirects and other site settings.\n"
    )
    config.chmod(0o640)
    enabled.symlink_to(config)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"config": str(config), "mode": "ok", "events": []}))
    command = tmp_path / "nginx-adapter.py"
    command.write_text(
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({str(state)!r})\n"
        "state = json.loads(path.read_text())\n"
        "action = sys.argv[1]\n"
        "state['events'].append(action)\n"
        "path.write_text(json.dumps(state))\n"
        f"updated = {CURRENT!r} in pathlib.Path(state['config']).read_text()\n"
        "if action == 'validate' and state['mode'] == 'invalid' and updated:\n"
        "    print('Injected invalid candidate configuration', file=sys.stderr); sys.exit(2)\n"
        "if action == 'reload' and state['mode'] == 'reload-failure' and state['events'].count('reload') == 1:\n"
        "    print('Injected reload failure', file=sys.stderr); sys.exit(3)\n"
    )
    variables = {
        "ansible_connection": "local",
        "ansible_python_interpreter": sys.executable,
        "ansible_become": False,
        "nginx_ui_cache_config": str(config),
        "nginx_ui_cache_enabled": str(enabled),
        "nginx_ui_cache_validate_command": [sys.executable, str(command), "validate"],
        "nginx_ui_cache_reload_command": [sys.executable, str(command), "reload"],
        "nginx_ui_cache_verify_public": False,
    }
    return config, enabled, state, variables


def run_migration(tmp_path, nginx_site, *, check=False, success=True):
    """Run the production playbook with local Ansible and controlled nginx commands."""
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps(nginx_site[3]))
    command = [
        shutil.which("ansible-playbook"),
        "-i",
        "localhost,",
        str(PLAYBOOK),
        "-e",
        "@" + str(variables),
    ]
    if check:
        command.extend(["--check", "--diff"])
    result = subprocess.run(
        command,
        env={**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8", "ANSIBLE_NOCOLOR": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result.stdout


def test_nginx_migration_preserves_config_and_is_idempotent(tmp_path, nginx_site):
    """Change the location once, retain its backup and never reload on a second run."""
    config, enabled, state, _ = nginx_site
    original = config.read_text()
    first = run_migration(tmp_path, nginx_site)
    assert re.search(r"changed=2\b", first)
    assert config.read_text() == original.replace(LEGACY, CURRENT)
    assert enabled.is_symlink() and enabled.resolve() == config
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    backups = list(config.parent.glob(config.name + ".*~"))
    assert len(backups) == 1 and backups[0].read_text() == original
    assert json.loads(state.read_text())["events"] == ["validate", "validate", "reload"]
    second = run_migration(tmp_path, nginx_site)
    assert "changed=0" in second
    assert json.loads(state.read_text())["events"] == ["validate", "validate", "reload", "validate"]
    assert list(config.parent.glob(config.name + ".*~")) == backups


@pytest.mark.parametrize("mode", ["invalid", "reload-failure"])
def test_nginx_migration_restores_backup_and_reports_failure(tmp_path, nginx_site, mode):
    """Preserve the original file when either nginx validation or reload fails."""
    config, _, state, _ = nginx_site
    original = config.read_text()
    data = json.loads(state.read_text())
    data["mode"] = mode
    state.write_text(json.dumps(data))
    output = run_migration(tmp_path, nginx_site, success=False)
    assert "previous configuration was restored" in output
    assert config.read_text() == original
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    actions = json.loads(state.read_text())["events"]
    assert actions == (
        ["validate", "validate", "validate", "reload"]
        if mode == "invalid"
        else ["validate", "validate", "reload", "validate", "reload"]
    )


@pytest.mark.parametrize("pattern", ["^/assets/.+", LEGACY + " " + LEGACY])
def test_nginx_migration_refuses_an_unrecognized_or_ambiguous_config(tmp_path, nginx_site, pattern):
    """Refuse unexpected input before changing a file or invoking nginx."""
    config, _, state, _ = nginx_site
    original = config.read_text().replace(LEGACY, pattern)
    config.write_text(original)
    run_migration(tmp_path, nginx_site, success=False)
    assert config.read_text() == original
    assert json.loads(state.read_text())["events"] == []
    assert not list(config.parent.glob(config.name + ".*~"))


def test_nginx_migration_check_mode_previews_without_changing_or_reloading(tmp_path, nginx_site):
    """Provide an actual Ansible dry run with no backup, mutation or reload."""
    config, _, state, _ = nginx_site
    original = config.read_text()
    output = run_migration(tmp_path, nginx_site, check=True)
    assert CURRENT in output and "changed=1" in output
    assert config.read_text() == original
    assert json.loads(state.read_text())["events"] == ["validate"]
    assert not list(config.parent.glob(config.name + ".*~"))


def test_nginx_migration_refuses_another_active_site(tmp_path, nginx_site):
    """Do not modify the expected config while nginx is actually using a different one."""
    config, enabled, state, _ = nginx_site
    original = config.read_text()
    other = tmp_path / "available/other-site"
    other.write_text("Other site")
    enabled.unlink()
    enabled.symlink_to(other)
    run_migration(tmp_path, nginx_site, success=False)
    assert config.read_text() == original and other.read_text() == "Other site"
    assert json.loads(state.read_text())["events"] == []


@pytest.mark.parametrize("immutable", [True, False])
def test_nginx_migration_verifies_public_stylesheets_through_ansible(
    tmp_path, nginx_site, immutable
):
    """Check the deployed cache header with Ansible URI rather than accepting a local edit."""
    requests = []
    css_path = "/ui/styles/header.min." + "a" * 64 + ".css"

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if self.path == "/" else "text/css")
            self.send_header(
                "Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache"
            )
            self.end_headers()
            self.wfile.write(f'<link rel=stylesheet href="{css_path}">'.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        nginx_site[3].update(
            nginx_ui_cache_verify_public=True,
            nginx_ui_cache_origin=f"http://127.0.0.1:{server.server_port}",
        )
        try:
            run_migration(tmp_path, nginx_site, success=immutable)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert requests == ["/", css_path]
    assert CURRENT in nginx_site[0].read_text()
