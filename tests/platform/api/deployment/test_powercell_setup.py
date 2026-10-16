"""Exercise administrator provisioning with real OpenSSL, files and API startup."""

import grp
import json
import os
import socket
import ssl
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from throng_api.app import create_app
from throng_api.cli import migrate
from throng_api.config import Settings

ROOT = Path(__file__).resolve().parents[4]


def tls_pair(directory, *, host="api.example", expired=False, ca=False):
    """Issue an actual X.509 certificate and private key for OpenSSL validation."""
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / "fullchain.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (directory / "privkey.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


@pytest.fixture
def provisioner(tmp_path):
    """Run the administrator CLI against isolated files and a real TLS lineage."""
    lineage = tmp_path / "letsencrypt"
    tls_pair(lineage)
    env = tmp_path / "server.env"
    env.write_text(
        "# Existing production settings must survive.\n"
        f'THRONG_DATABASE_URL="sqlite+aiosqlite:///{tmp_path}/audit.db"\n'
        f'THRONG_STORAGE_PATH="{tmp_path}/artifacts"\n'
        "THRONG_CONTROLLER_ENABLED=false\n"
        "DASHBOARD_API_URL=https://api.example/v1\n"
    )
    directory = tmp_path / "powercell"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = [
        sys.executable,
        str(ROOT / "deploy/powercell_setup.py"),
        "--directory",
        str(directory),
        "--env-file",
        str(env),
        "--tls-lineage",
        str(lineage),
        "--group",
        grp.getgrgid(os.getgid()).gr_name,
        "--acme-url",
        "https://api.example/acme/powercell",
        "--relay-url",
        f"https://api.example:{port}",
        "--relay-host",
        "127.0.0.1",
    ]

    def run(*arguments, success=True, environment=None):
        result = subprocess.run(
            [*base, *arguments],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stderr
        for private in (directory / "recovery.secret", directory / "issuer/ca-key.pem"):
            if private.exists():
                assert private.read_text().strip() not in result.stdout + result.stderr
        return result

    return directory, env, lineage, run


def test_explicit_setup_is_idempotent_and_enables_the_actual_api(provisioner):
    """Keep identity across retries and boot a mutually authenticated relay."""
    directory, env, lineage, run = provisioner
    run("configure", success=False)
    assert not (directory / "issuer").exists()
    assert json.loads(run("configure", "--create-issuer").stdout)["changed"]
    paths = [
        env,
        directory / "issuer/ca.pem",
        directory / "issuer/ca-key.pem",
        directory / "recovery.secret",
    ]
    before = [p.read_bytes() for p in paths]
    assert not json.loads(run("configure", "--create-issuer").stdout)["changed"]
    assert [p.read_bytes() for p in paths] == before
    assert env.read_text().startswith("# Existing production settings must survive.\n")
    for path in [*paths, directory / "tls/privkey.pem"]:
        assert path.stat().st_mode & 0o777 == 0o640
    settings = Settings(_env_file=env)
    migrate(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/v1/agent-capabilities")
        assert response.status_code == 200
        assert {"powercell.enrollment.v1", "powercell.relay.v1"} <= set(
            response.json()["extensions"]
        )
        assert response.json()["attestation_required"]
        # Check the provisioned CA over TLS; Apple attestation has separate tests.
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TLS test identity")]))
            .sign(key, hashes.SHA256())
        )
        certificate = app.state.powercell_authority.issue(
            SimpleNamespace(id=str(uuid4())), csr, datetime.now(UTC)
        )
        (directory / "client.pem").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
            + (directory / "issuer/ca.pem").read_bytes()
        )
        (directory / "client.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.create_default_context(cafile=str(lineage / "fullchain.pem"))
        context.set_alpn_protocols(["h2"])
        context.load_cert_chain(directory / "client.pem", directory / "client.key")
        with socket.create_connection(
            ("127.0.0.1", urlsplit(settings.powercell_relay_url).port), timeout=3
        ) as connection:
            with context.wrap_socket(connection, server_hostname="api.example") as authenticated:
                assert authenticated.version() == "TLSv1.3"
                assert authenticated.selected_alpn_protocol() == "h2"


def test_repairing_environment_reuses_the_persistent_issuer_and_secret(provisioner):
    """Recover removed settings without changing the issuer or encryption secret."""
    directory, env, _, run = provisioner
    run("configure", "--create-issuer")
    before = [
        (directory / name).read_bytes()
        for name in ["issuer/ca.pem", "issuer/ca-key.pem", "recovery.secret"]
    ]
    env.write_text(
        "\n".join(
            line
            for line in env.read_text().splitlines()
            if not line.startswith("THRONG_POWERCELL_")
        )
        + "\n"
    )
    run("configure", "--create-issuer")
    assert before == [
        (directory / name).read_bytes()
        for name in ["issuer/ca.pem", "issuer/ca-key.pem", "recovery.secret"]
    ]
    secret = dotenv_values(env)["THRONG_POWERCELL_SECRET"]
    env.write_text(env.read_text().replace(secret, "different-secret-" + "a" * 64))
    result = run("configure", "--create-issuer", success=False)
    assert "refusing to rotate" in result.stderr
    assert (directory / "recovery.secret").read_text().strip() == secret


@pytest.mark.parametrize("damage", ["missing_key", "partial_environment", "missing_secret"])
def test_partial_issuer_state_is_not_silently_replaced(provisioner, damage):
    """Refuse to recreate missing identity material or lose credential recovery."""
    directory, env, _, run = provisioner
    run("configure", "--create-issuer")
    certificate = (directory / "issuer/ca.pem").read_bytes()
    if damage == "missing_key":
        (directory / "issuer/ca-key.pem").unlink()
    elif damage == "missing_secret":
        (directory / "recovery.secret").unlink()
        env.write_text(
            "\n".join(
                line
                for line in env.read_text().splitlines()
                if not line.startswith("THRONG_POWERCELL_SECRET=")
            )
            + "\n"
        )
    else:
        env.write_text(
            "\n".join(
                line
                for line in env.read_text().splitlines()
                if not line.startswith("THRONG_POWERCELL_CA_KEY=")
            )
            + "\n"
        )
    before = env.read_bytes()
    run("configure", "--create-issuer", success=False)
    assert (directory / "issuer/ca.pem").read_bytes() == certificate
    assert env.read_bytes() == before
    if damage == "missing_key":
        assert not (directory / "issuer/ca-key.pem").exists()


@pytest.mark.parametrize("damage", ["wrong_key", "wrong_hostname", "expired"])
def test_bad_tls_material_does_not_replace_a_working_pair(provisioner, damage):
    """Keep the active pair when renewal offers the wrong key, name or lifetime."""
    directory, env, lineage, run = provisioner
    run("configure", "--create-issuer")
    before = [(directory / "tls" / name).read_bytes() for name in ["fullchain.pem", "privkey.pem"]]
    environment = env.read_bytes()
    old = (lineage / "fullchain.pem").read_bytes()
    tls_pair(
        lineage,
        host="wrong.example" if damage == "wrong_hostname" else "api.example",
        expired=damage == "expired",
    )
    if damage == "wrong_key":
        (lineage / "fullchain.pem").write_bytes(old)
    run("renew", success=False)
    assert before == [
        (directory / "tls" / name).read_bytes() for name in ["fullchain.pem", "privkey.pem"]
    ]
    assert env.read_bytes() == environment


def test_renewal_restarts_with_a_complete_pair_and_retries_a_failed_restart(provisioner, tmp_path):
    """Publish both files atomically and retry after systemd rejects restart."""
    directory, env, lineage, run = provisioner
    run("configure", "--create-issuer")
    old = (directory / "tls").resolve()
    preserved = [
        env.read_bytes(),
        (directory / "issuer/ca.pem").read_bytes(),
        (directory / "recovery.secret").read_bytes(),
    ]
    tls_pair(lineage)
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "systemctl.calls"
    failed = tmp_path / "fail-systemctl"
    failed.touch()
    command = binary / "systemctl"
    command.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\ntest ! -e "{failed}"\n')
    command.chmod(0o755)
    environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    run("renew", "--restart", environment=environment, success=False)
    assert (directory / "tls").resolve() != old
    failed.unlink()
    run("renew", "--restart", environment=environment)
    assert calls.read_text().splitlines() == ["try-restart throng.service"] * 2
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
        directory / "tls/fullchain.pem", directory / "tls/privkey.pem"
    )
    assert preserved == [
        env.read_bytes(),
        (directory / "issuer/ca.pem").read_bytes(),
        (directory / "recovery.secret").read_bytes(),
    ]


@pytest.mark.parametrize(
    "secret",
    [
        "existing-operator-secret-" + "b" * 64,
        'секрет с "кавычками", # и \\ обратной чертой ' + "c" * 32,
        "1234567890" * 6,
    ],
)
def test_existing_operator_issuer_and_secret_are_preserved(provisioner, tmp_path, secret):
    """Adopt operator-provided material without changing its bytes or secret."""
    directory, env, _, run = provisioner
    external = tmp_path / "external-issuer"
    tls_pair(external, ca=True)
    with env.open("a") as stream:
        stream.write(
            f'THRONG_POWERCELL_CA_CERTIFICATE="{external}/fullchain.pem"\nTHRONG_POWERCELL_CA_KEY="{external}/privkey.pem"\nTHRONG_POWERCELL_SECRET='
            + json.dumps(secret, ensure_ascii=False)
            + "\n"
        )
    before = (external / "privkey.pem").read_bytes()
    run("configure")
    values = dotenv_values(env)
    assert values["THRONG_POWERCELL_CA_CERTIFICATE"] == str(external / "fullchain.pem")
    assert values["THRONG_POWERCELL_SECRET"] == secret
    assert Settings(_env_file=env).powercell_secret == secret
    run("configure")
    assert (external / "privkey.pem").read_bytes() == before
    assert not (directory / "issuer").exists()
