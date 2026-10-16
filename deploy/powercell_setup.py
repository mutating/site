"""Explicit administrator provisioning; never called by API startup or ordinary CI."""

import argparse
import fcntl
import grp
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

PREFIX = "THRONG_POWERCELL_"
ASSIGNMENT = re.compile(r"^\s*(THRONG_POWERCELL_[A-Z_]+)\s*=(.*)$")


def openssl(*arguments):
    result = subprocess.run(["openssl", *map(str, arguments)], capture_output=True, check=False)
    if result.returncode:
        # Never forward tool diagnostics or private configuration to CI/journald.
        raise ValueError("Certificate validation/provisioning failed: openssl " + arguments[0])
    return result.stdout


def write_private(path, data, gid):
    if path.exists() and path.read_bytes() == data:
        os.chmod(path, 0o640)
        os.chown(path, -1, gid)
        return False
    descriptor, name = tempfile.mkstemp(prefix=".powercell-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            os.fchown(stream.fileno(), -1, gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def read_environment(path):
    text = path.read_text()
    values = {}
    for line in text.splitlines():
        if match := ASSIGNMENT.match(line):
            name, encoded = match.groups()
            if name in values:
                raise ValueError("Duplicate setting: " + name)
            # Managed values use JSON-compatible double quotes, including
            # escaped quotes/backslashes. Also accept existing simple .env values.
            try:
                pieces = (
                    [json.loads(encoded)]
                    if encoded.lstrip().startswith('"')
                    else shlex.split(encoded, comments=False)
                )
            except json.JSONDecodeError:
                pieces = shlex.split(encoded, comments=False)
            if (
                len(pieces) != 1
                or not isinstance(pieces[0], str)
                or any(c in pieces[0] for c in "\r\n\x00")
            ):
                raise ValueError("Expected one quoted or unquoted value: " + name)
            values[name] = pieces[0]
    return text, values


def update_environment(text, values):
    pending = dict(values)
    lines = []
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match and match[1] in pending:
            line = match[1] + "=" + json.dumps(pending.pop(match[1]), ensure_ascii=False)
        lines.append(line)
    lines.extend(
        name + "=" + json.dumps(value, ensure_ascii=False) for name, value in pending.items()
    )
    return "\n".join(lines) + "\n"


def endpoint(value, *, relay=False):
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or (relay and url.path not in ("", "/"))
    ):
        raise ValueError("Expected a public HTTPS " + ("relay origin" if relay else "ACME URL"))
    if relay and not 1024 <= (url.port or 443) <= 65535:
        raise ValueError("Use an unprivileged relay port, for example 8444")
    return url


def check_pair(certificate, key, *, ca=False, hostname=None):
    public = openssl("x509", "-in", certificate, "-pubkey", "-noout")
    if public != openssl("pkey", "-in", key, "-pubout", "-passin", "pass:"):
        raise ValueError("Certificate and private key do not match")
    openssl("x509", "-in", certificate, "-checkend", "0", "-noout")
    if ca:
        description = openssl("x509", "-in", certificate, "-text", "-noout")
        if b"CA:TRUE" not in description or b"id-ecPublicKey" not in description:
            raise ValueError("An EC certificate authority is required")
    if hostname:
        openssl("x509", "-in", certificate, "-checkhost", hostname, "-noout")


def issuer(directory, existing, gid, allow_create):
    certificate = existing.get(PREFIX + "CA_CERTIFICATE")
    key = existing.get(PREFIX + "CA_KEY")
    if bool(certificate) != bool(key):
        raise ValueError("Restore the complete existing CA configuration; refusing to replace it")
    if certificate:
        certificate, key = Path(certificate), Path(key)
        if not certificate.is_absolute() or not key.is_absolute():
            raise ValueError("Existing CA paths must be absolute")
    else:
        target = directory / "issuer"
        certificate, key = target / "ca.pem", target / "ca-key.pem"
        if not target.exists():
            if not allow_create:
                raise ValueError("Initial issuer provisioning requires --create-issuer")
            with tempfile.TemporaryDirectory(prefix=".issuer-", dir=directory) as temporary:
                temporary = Path(temporary)
                openssl(
                    "req",
                    "-new",
                    "-x509",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-nodes",
                    "-sha256",
                    "-days",
                    "3650",
                    "-subj",
                    "/CN=Power Cell Device CA/O=Mutating",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                    "-addext",
                    "keyUsage=critical,keyCertSign,cRLSign",
                    "-addext",
                    "subjectKeyIdentifier=hash",
                    "-addext",
                    "authorityKeyIdentifier=keyid:always",
                    "-keyout",
                    temporary / "ca-key.pem",
                    "-out",
                    temporary / "ca.pem",
                )
                for name in ("ca.pem", "ca-key.pem"):
                    write_private(temporary / name, (temporary / name).read_bytes(), gid)
                os.chown(temporary, -1, gid)
                os.chmod(temporary, 0o750)
                # Publish the pair together. A partial existing issuer is never replaced.
                temporary.rename(target)
    check_pair(certificate, key, ca=True)
    return str(certificate), str(key)


def install_tls(directory, lineage, relay_url, gid):
    host = endpoint(relay_url, relay=True).hostname
    certificate, key = lineage / "fullchain.pem", lineage / "privkey.pem"
    check_pair(certificate, key, hostname=host)
    content = (certificate.read_bytes(), key.read_bytes())
    digest = hashlib.sha256(b"\x00".join(content)).hexdigest()
    version = directory / ("tls-" + digest)
    if not version.exists():
        with tempfile.TemporaryDirectory(prefix=".tls-", dir=directory) as temporary:
            temporary = Path(temporary)
            for name, data in (("fullchain.pem", content[0]), ("privkey.pem", content[1])):
                write_private(temporary / name, data, gid)
            os.chown(temporary, -1, gid)
            os.chmod(temporary, 0o750)
            temporary.rename(version)
    current = directory / "tls"
    if current.is_symlink() and os.readlink(current) == version.name:
        return False
    if current.exists() and not current.is_symlink():
        raise ValueError("The managed TLS path must be a symlink; refusing to replace it")
    link = directory / (".tls-link-" + secrets.token_hex(8))
    try:
        link.symlink_to(version.name)
        os.replace(link, current)
    finally:
        link.unlink(missing_ok=True)
    return True


def provision(arguments, gid):
    text, existing = read_environment(arguments.env_file)
    endpoint(arguments.acme_url)
    endpoint(arguments.relay_url, relay=True)
    # Validate the externally issued server certificate before generating state.
    check_pair(
        arguments.tls_lineage / "fullchain.pem",
        arguments.tls_lineage / "privkey.pem",
        hostname=urlsplit(arguments.relay_url).hostname,
    )
    secret_path = arguments.directory / "recovery.secret"
    saved = secret_path.read_text().rstrip("\n") if secret_path.exists() else None
    had_issuer = (
        bool(existing.get(PREFIX + "CA_CERTIFICATE")) or (arguments.directory / "issuer").exists()
    )
    if had_issuer and not (existing.get(PREFIX + "SECRET") or saved):
        raise ValueError("Restore the existing recovery secret; refusing to generate a replacement")
    secret = existing.get(PREFIX + "SECRET") or saved or secrets.token_hex(32)
    if len(secret) < 32 or (saved is not None and saved != secret):
        raise ValueError("Restore the persistent recovery secret; refusing to rotate it")
    certificate, key = issuer(arguments.directory, existing, gid, arguments.create_issuer)
    changed = write_private(secret_path, (secret + "\n").encode(), gid)
    changed |= install_tls(arguments.directory, arguments.tls_lineage, arguments.relay_url, gid)
    values = {
        PREFIX + "ACME_URL": arguments.acme_url,
        PREFIX + "CA_CERTIFICATE": certificate,
        PREFIX + "CA_KEY": key,
        PREFIX + "SECRET": secret,
        PREFIX + "RELAY_URL": arguments.relay_url,
        PREFIX + "RELAY_CERTIFICATE": str(arguments.directory / "tls/fullchain.pem"),
        PREFIX + "RELAY_KEY": str(arguments.directory / "tls/privkey.pem"),
        PREFIX + "RELAY_HOST": arguments.relay_host,
    }
    changed |= write_private(arguments.env_file, update_environment(text, values).encode(), gid)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["configure", "renew"])
    parser.add_argument("--directory", type=Path, default=Path("/etc/throng/powercell"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/throng/throng.env"))
    parser.add_argument("--tls-lineage", type=Path, required=True)
    parser.add_argument("--group", default="throng")
    parser.add_argument("--acme-url", default="https://api.mutating.tech/acme/powercell")
    parser.add_argument("--relay-url", default="https://api.mutating.tech:8444")
    parser.add_argument("--relay-host", default="0.0.0.0")
    parser.add_argument("--create-issuer", action="store_true")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Try restarting throng.service after TLS renewal",
    )
    arguments = parser.parse_args()
    if not all(
        path.is_absolute()
        for path in (arguments.directory, arguments.env_file, arguments.tls_lineage)
    ):
        raise ValueError("Directory, environment and TLS lineage paths must be absolute")
    os.umask(0o077)
    gid = grp.getgrnam(arguments.group).gr_gid
    arguments.directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(arguments.directory, 0o750)
    os.chown(arguments.directory, -1, gid)
    with (arguments.directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if arguments.operation == "configure":
            changed = provision(arguments, gid)
        else:
            _, values = read_environment(arguments.env_file)
            changed = install_tls(
                arguments.directory,
                arguments.tls_lineage,
                values[PREFIX + "RELAY_URL"],
                gid,
            )
            # Retry the restart even if a previous renewal already published
            # the files but systemd failed. Certbot calls this only on renewal.
            if arguments.restart:
                subprocess.run(["systemctl", "try-restart", "throng.service"], check=True)
    print(json.dumps({"changed": changed}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit("Power Cell setup failed: " + str(error)) from None
