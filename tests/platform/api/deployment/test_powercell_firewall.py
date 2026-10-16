"""Run the real Ansible tasks against disposable firewall command adapters."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
TASKS = ROOT / "infra/playbooks/tasks"
pytestmark = pytest.mark.skipif(
    not shutil.which("ansible-playbook"),
    reason="Requires Ansible; installed in native deployment CI",
)


@pytest.fixture
def firewall(tmp_path):
    """Keep firewall state and existing SSH/web rules outside the machine's policy."""
    state = tmp_path / "firewall.json"
    state.write_text(
        json.dumps(
            {
                "ufw": False,
                "firewalld": False,
                "calls": [],
                "ufw_rules": ["22/tcp", "443/tcp"],
                "runtime": ["22/tcp", "443/tcp"],
                "permanent": ["22/tcp", "443/tcp"],
            }
        )
    )
    source = """import json,pathlib,sys
p=pathlib.Path(STATE)
s=json.loads(p.read_text());cmd=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:]
s['calls'].append([cmd,*a]);p.write_text(json.dumps(s))
if cmd=='ufw':
 if a==['status']:
  if s.get('error'):print('Cannot read firewall',file=sys.stderr);sys.exit(1)
  print('Status: active' if s['ufw'] else 'Status: inactive');sys.exit(0)
 assert s['ufw'] and a[0]=='allow' and a[2:]==['comment','Power Cell relay'],a
 if a[1] in s['ufw_rules']:print('Skipping adding existing rule');sys.exit(0)
 s['ufw_rules'].append(a[1]);p.write_text(json.dumps(s));print('Rule added');sys.exit(0)
if a==['--state']:print('running' if s['firewalld'] else 'not running');sys.exit(0 if s['firewalld'] else 252)
assert s['firewalld'],a
if a==['--get-default-zone']:print('public');sys.exit(0)
zone=next(x for x in a if x.startswith('--zone='))
key='permanent' if '--permanent' in a else 'runtime'
query=next((x for x in a if x.startswith('--query-port=')),None)
if query:sys.exit(0 if zone+':'+query.split('=',1)[1] in s[key] else 1)
port=next(x for x in a if x.startswith('--add-port=')).split('=',1)[1]
assert len(a)==(3 if key=='permanent' else 2),a
s[key].append(zone+':'+port);p.write_text(json.dumps(s));print('success')
""".replace("STATE", repr(str(state)))
    binaries = {}
    for name in ["ufw", "firewall-cmd"]:
        path = tmp_path / name
        path.write_text("#!" + sys.executable + "\n" + source)
        path.chmod(0o755)
        binaries[name] = str(path)
    return state, binaries


def run_playbook(tmp_path, task, variables, *, success=True):
    """Execute the actual task include with Ansible's real command/wait modules."""
    wrapper = tmp_path / "review.yml"
    wrapper.write_text(
        json.dumps(
            [
                {
                    "hosts": "all",
                    "gather_facts": False,
                    "become": False,
                    "vars": {
                        "ansible_connection": "local",
                        "ansible_python_interpreter": sys.executable,
                        **variables,
                    },
                    "tasks": [{"ansible.builtin.include_tasks": str(TASKS / task)}],
                }
            ]
        )
    )
    result = subprocess.run(
        [shutil.which("ansible-playbook"), "-i", "localhost,", str(wrapper)],
        env={**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8", "ANSIBLE_NOCOLOR": "1"},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("manager", "zone"), [("ufw", ""), ("firewalld", ""), ("firewalld", "external")]
)
def test_relay_rule_is_persistent_and_idempotent_without_changing_ssh(
    firewall, tmp_path, manager, zone
):
    """Open the configured port twice while preserving other rules and firewall state."""
    state, binaries = firewall
    initial = json.loads(state.read_text())
    initial[manager] = True
    state.write_text(json.dumps(initial))
    variables = {
        "powercell_relay_url": "https://api.example:18443",
        "powercell_firewalld_zone": zone,
        "powercell_ufw_binary": binaries["ufw"],
        "powercell_firewalld_binary": binaries["firewall-cmd"],
    }
    first = run_playbook(tmp_path, "powercell-firewall.yml", variables)
    assert re.search(r"changed=[12]\b", first)
    second = run_playbook(tmp_path, "powercell-firewall.yml", variables)
    assert re.search(r"changed=0\b", second)
    final = json.loads(state.read_text())
    assert final[manager] and not final["ufw" if manager == "firewalld" else "firewalld"]
    if manager == "ufw":
        assert final["ufw_rules"] == ["22/tcp", "443/tcp", "18443/tcp"]
        assert final["runtime"] == initial["runtime"] and final["permanent"] == initial["permanent"]
    else:
        for key in ["runtime", "permanent"]:
            assert final[key] == initial[key] + ["--zone=" + (zone or "public") + ":18443/tcp"]
        assert final["ufw_rules"] == initial["ufw_rules"]


@pytest.mark.parametrize("missing", [False, "absolute", "name"])
def test_absent_or_inactive_firewalls_are_not_enabled(firewall, tmp_path, missing):
    """Accept absent utilities, including dash's 127, without enabling a policy."""
    state, binaries = firewall
    if missing:
        for path in binaries.values():
            Path(path).unlink()
    if missing == "name":
        binaries = {
            "ufw": "powercell-audit-absent-ufw",
            "firewall-cmd": "powercell-audit-absent-firewalld",
        }
    output = run_playbook(
        tmp_path,
        "powercell-firewall.yml",
        {
            "powercell_relay_url": "https://api.example:8444",
            "powercell_ufw_binary": binaries["ufw"],
            "powercell_firewalld_binary": binaries["firewall-cmd"],
        },
    )
    assert "changed=0" in output
    final = json.loads(state.read_text())
    assert not final["ufw"] and not final["firewalld"]
    assert final["ufw_rules"] == final["runtime"] == final["permanent"] == ["22/tcp", "443/tcp"]


@pytest.mark.parametrize("case", ["two_managers", "status_error", "invalid_port"])
def test_unsafe_detection_stops_before_changing_firewall(firewall, tmp_path, case):
    """Reject ambiguity, command errors and invalid relay ports without edits."""
    state, binaries = firewall
    initial = json.loads(state.read_text())
    if case == "two_managers":
        initial["ufw"] = initial["firewalld"] = True
    if case == "status_error":
        initial["error"] = True
    state.write_text(json.dumps(initial))
    run_playbook(
        tmp_path,
        "powercell-firewall.yml",
        {
            "powercell_relay_url": "https://api.example"
            if case == "invalid_port"
            else "https://api.example:8444",
            "powercell_ufw_binary": binaries["ufw"],
            "powercell_firewalld_binary": binaries["firewall-cmd"],
        },
        success=False,
    )
    final = json.loads(state.read_text())
    for key in ["ufw", "firewalld", "ufw_rules", "runtime", "permanent"]:
        assert final[key] == initial[key]


@pytest.mark.parametrize("reachable", [True, False])
def test_external_tcp_probe_rejects_an_unreachable_relay(tmp_path, reachable):
    """Use real sockets so a blocked/unavailable relay cannot produce success."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        if reachable:
            listener.listen()
        output = run_playbook(
            tmp_path,
            "powercell-connectivity.yml",
            {
                "powercell_relay_url": f"https://127.0.0.1:{port}",
                "powercell_relay_probe_timeout": 1,
            },
            success=reachable,
        )
    if not reachable:
        assert "unreachable from the Ansible controller" in output
