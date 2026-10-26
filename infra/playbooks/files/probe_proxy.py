"""Read-only HTTPS/content and HTTP redirect probes; never bypass TLS validation."""

import argparse
import concurrent.futures
import hashlib
import json
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def probe_relay(check, address):
    """Verify relay identity and closure without a client certificate."""
    parts = urlsplit(check["url"])
    target = parts.hostname if address == "dns" else address
    start = time.monotonic()
    result = {"url": check["url"], "address": address, "ok": False}
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    try:
        with socket.create_connection((target, parts.port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=parts.hostname) as connection:
                result.update({
                    "remote_ip": connection.getpeername()[0],
                    "tls_version": connection.version(),
                    "alpn": connection.selected_alpn_protocol(),
                    "certificate_sha256": hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest(),
                    "certificate_expires": connection.getpeercert()["notAfter"],
                    "ssl_verify_result": 0,
                })
                connection.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
                result["unauthenticated_peer_closed"] = not connection.recv(1)
                result["ok"] = result["unauthenticated_peer_closed"] and result["alpn"] == "h2"
                if not result["ok"]:
                    result["error"] = "Relay sent application data to an unauthenticated client"
    except ssl.SSLError as error:
        result["error"] = str(error)
        result["client_certificate_required"] = error.reason == "TLSV13_ALERT_CERTIFICATE_REQUIRED"
        result["ok"] = (
            result["client_certificate_required"]
            and result.get("ssl_verify_result") == 0
            and result.get("alpn") == "h2"
        )
    except OSError as error:
        result["error"] = str(error)
    result["time_total"] = round(time.monotonic() - start, 3)
    return result


def probe(check, address):
    if check.get("format") == "mtls":
        return probe_relay(check, address)
    url = check["url"]
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    with tempfile.TemporaryFile() as body:
        command = [
            "curl", "--ipv4", "--noproxy", "*", "--silent", "--show-error",
            "--connect-timeout", "5", "--max-time", "15", "--max-filesize", "2097152",
            "--output", "/dev/fd/" + str(body.fileno()), "--write-out", "%{json}",
        ]
        if address != "dns":
            command.extend(["--resolve", f"{parts.hostname}:{port}:{address}"])
        command.append(url)
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=20,
            pass_fds=(body.fileno(),),
        )
        try:
            metadata = json.loads(result.stdout)
        except json.JSONDecodeError:
            metadata = {}
        body.seek(0)
        content = body.read()
    content_ok = True
    if check.get("format") == "html":
        content_ok = b"<html" in content.lower()
    elif check.get("format") == "json":
        try:
            payload = json.loads(content)
            content_ok = isinstance(payload, dict) and all(
                payload.get(key) == value for key, value in check.get("fields", {}).items()
            )
        except (ValueError, UnicodeDecodeError):
            content_ok = False
    status_ok = metadata.get("http_code") == check["status"]
    if "location" in check:
        content_ok = content_ok and metadata.get("redirect_url") == check["location"]
    return {
        "url": url,
        "address": address,
        "ok": result.returncode == 0 and status_ok and content_ok,
        "exit_code": result.returncode,
        "content_ok": content_ok,
        **{key: metadata.get(key) for key in (
            "remote_ip", "http_code", "ssl_verify_result", "time_connect",
            "time_appconnect", "time_total", "content_type", "size_download", "redirect_url",
        )},
        "error": result.stderr.strip(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="A fixed IPv4 address, or dns")
    parser.add_argument("--checks", required=True, help="JSON list of public URL checks")
    options = parser.parse_args()
    checks = json.loads(options.checks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda check: probe(check, options.address), checks))
    print(json.dumps(results))
    return 0 if results and all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
