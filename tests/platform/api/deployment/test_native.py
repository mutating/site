"""Verify native supervision, SQLite recovery and nginx virtual host routing."""

import contextlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def unused_port():
    """Reserve a candidate loopback port for the test's real processes."""
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def wait_for(check, timeout=20):
    """Wait for a real network or process state and fail with the last error."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for the deployment")


def request(port, path, host=None, secure=False, method="GET", body=None, headers=None):
    """Send a real HTTP request with the virtual host nginx must select."""
    connection = (
        http.client.HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context())
        if secure
        else http.client.HTTPConnection("127.0.0.1", port)
    )
    connection.timeout = 2
    with contextlib.closing(connection):
        connection.request(
            method, path, body=body, headers={"Host": host or "localhost", **(headers or {})}
        )
        response = connection.getresponse()
        return (
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            response.read(),
        )


def test_sqlite_backup_restores_wal_data_and_handles_an_absent_database(tmp_path):
    """Keep committed WAL data and recover both an existing and a first-deploy database."""
    database = tmp_path / "throng.db"
    env_file = tmp_path / "server.env"
    env_file.write_text(f"THRONG_DATABASE_URL=sqlite+aiosqlite:///{database}\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("THRONG_")}

    def backup(action, name):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "deploy/sqlite_backup.py"),
                action,
                name,
                "--env-file",
                str(env_file),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

    backup("backup", "empty")
    database.touch()
    backup("restore", "empty")
    assert not database.exists()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('before')")
        connection.commit()
        backup("backup", "before-migration")
        connection.execute("UPDATE evidence SET value = 'after'")
        connection.commit()
    backup("restore", "before-migration")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("before",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.skipif(
    not shutil.which("nginx") or not (ROOT / "platform/web/dist/index.html").is_file(),
    reason="Requires nginx and the built web UI; also runs in web CI after npm run build",
)
def test_native_platform_and_subdomain_routing(tmp_path):
    """Route TLS hosts to real services, preserve data and stop both when either child exits."""
    api_port, dashboard_port, http_port, https_port = [unused_port() for _ in range(4)]
    database = tmp_path / "api.db"
    env_file = tmp_path / "server.env"
    env_file.write_text(
        f"THRONG_DATABASE_URL=sqlite+aiosqlite:///{database}\n"
        f"THRONG_STORAGE_PATH={tmp_path / 'artifacts'}\n"
        "DASHBOARD_PUBLIC_ORIGIN=https://platform.mutating.tech\n"
        "DASHBOARD_SECURE_COOKIE=true\n"
        "DASHBOARD_SESSION_KEY=MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE=\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("THRONG_", "DASHBOARD_"))
    }
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(tmp_path / "privkey.pem"),
            "-out",
            str(tmp_path / "fullchain.pem"),
        ],
        check=True,
        capture_output=True,
    )
    hosts = (
        (ROOT / "deploy/nginx.conf.j2").read_text().replace("{{ certificate_path }}", str(tmp_path))
    )
    hosts = re.sub(r"^\s*listen \[::\].*;\n", "", hosts, flags=re.MULTILINE)
    hosts = hosts.replace("listen 80;", f"listen 127.0.0.1:{http_port};")
    hosts = hosts.replace("listen 443 ssl;", f"listen 127.0.0.1:{https_port} ssl;")
    hosts = hosts.replace("127.0.0.1:8000", f"127.0.0.1:{api_port}")
    hosts = hosts.replace("127.0.0.1:8001", f"127.0.0.1:{dashboard_port}")
    config = tmp_path / "nginx.conf"
    config.write_text(
        f'pid "{tmp_path}/nginx.pid";\nerror_log "{tmp_path}/nginx-error.log";\n'
        "events { worker_connections 128; }\nhttp {\n"
        f'access_log "{tmp_path}/nginx-access.log";\n'
        f'client_body_temp_path "{tmp_path}/body";\nproxy_temp_path "{tmp_path}/proxy";\n'
        + hosts
        + f"\nserver {{ listen 127.0.0.1:{https_port} ssl; server_name mutating.tech; "
        + f'ssl_certificate "{tmp_path}/fullchain.pem"; ssl_certificate_key "{tmp_path}/privkey.pem"; '
        + 'location / { return 200 "main site"; } }\n}'
    )
    subprocess.run(
        ["nginx", "-t", "-p", f"{tmp_path}/", "-c", str(config)], check=True, capture_output=True
    )
    processes = []

    def start():
        log = (tmp_path / f"platform-{len(processes)}.log").open("w")
        child = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "deploy/run.py"),
                "--env-file",
                str(env_file),
                "--api-port",
                str(api_port),
                "--dashboard-port",
                str(dashboard_port),
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        processes.append(child)
        wait_for(lambda: request(api_port, "/health/ready")[0] == 200)
        wait_for(lambda: request(dashboard_port, "/login")[0] == 200)
        return child

    try:
        platform = start()
        nginx = subprocess.Popen(
            ["nginx", "-p", f"{tmp_path}/", "-c", str(config), "-g", "daemon off;"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(nginx)
        wait_for(lambda: request(https_port, "/health/ready", "api.mutating.tech", True)[0] == 200)
        status, headers, _ = request(http_port, "/docs?probe=1", "api.mutating.tech")
        assert status == 308
        assert headers["location"] == "https://api.mutating.tech/docs?probe=1"
        assert request(https_port, "/", "mutating.tech", True)[2] == b"main site"
        assert request(https_port, "/openapi.json", "api.mutating.tech", True)[0] == 200
        assert request(https_port, "/account/login", "api.mutating.tech", True)[0] == 404
        assert request(https_port, "/health/ready", "platform.mutating.tech", True)[0] == 404
        status, _, html = request(https_port, "/", "platform.mutating.tech", True)
        assert status == 200
        assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html)
        assert assets
        for asset in assets:
            assert request(https_port, asset.decode(), "platform.mutating.tech", True)[0] == 200
        for path in ("/login", "/profile", "/organizations"):
            assert request(https_port, path, "platform.mutating.tech", True)[0] == 200
        for path in (
            "/account",
            "/account/",
            "/account/profile?probe=1",
            "/account/api/session",
        ):
            status, headers, _ = request(https_port, path, "platform.mutating.tech", True)
            assert status == 404
            assert "location" not in headers
        assert request(https_port, "/missing.js", "platform.mutating.tech", True)[0] == 404
        identity = {"email": "deploy@example.com", "password": "a long deployment password"}
        assert (
            request(
                https_port,
                "/v1/users",
                "api.mutating.tech",
                True,
                "POST",
                json.dumps(identity),
                {"Content-Type": "application/json"},
            )[0]
            == 201
        )
        csrf = {
            "Content-Type": "application/json",
            "Origin": "https://platform.mutating.tech",
            "X-Requested-With": "ThrongDashboard",
        }
        credentials = {"login": identity["email"], "password": identity["password"]}
        status, headers, body = request(
            https_port,
            "/api/session",
            "platform.mutating.tech",
            True,
            "POST",
            json.dumps(credentials),
            csrf,
        )
        assert status == 200
        assert "access_token" not in json.loads(body)
        cookie = headers["set-cookie"]
        assert all(attribute in cookie for attribute in ("HttpOnly", "Secure", "Path=/api/"))
        assert "Domain=" not in cookie
        assert (
            request(
                https_port,
                "/api/session",
                "platform.mutating.tech",
                True,
                "POST",
                json.dumps(credentials),
                {**csrf, "Origin": "https://evil.example"},
            )[0]
            == 403
        )

        # An unexpected component exit must shut down its sibling and fail the unit.
        rows = subprocess.check_output(["ps", "-axww", "-o", "pid=,ppid=,command="], text=True)
        children = [line.split(None, 2) for line in rows.splitlines()]
        api_pid = next(
            int(pid)
            for pid, parent, command in children
            if int(parent) == platform.pid and "throng_api.app:app" in command
        )
        os.kill(api_pid, signal.SIGTERM)
        assert platform.wait(timeout=15) == 1
        with pytest.raises(OSError):
            request(dashboard_port, "/login")
        platform = start()
        assert (
            request(
                https_port,
                "/api/session",
                "platform.mutating.tech",
                True,
                headers={"Cookie": cookie.split(";", 1)[0]},
            )[0]
            == 200
        )
        platform.terminate()
        assert platform.wait(timeout=15) == 0
        with pytest.raises(OSError):
            request(api_port, "/health/ready")
    finally:
        for child in processes:
            if child.poll() is None:
                child.terminate()
        for child in processes:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
