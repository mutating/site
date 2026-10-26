"""Compare dashboard API requests through DNS, the origin and the TCP proxy."""

import argparse
import concurrent.futures
import json
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def probe(case, address):
    name, url, method = case
    parts = urlsplit(url)
    with tempfile.TemporaryDirectory() as directory:
        body = Path(directory) / "body"
        headers = Path(directory) / "headers"
        command = [
            "curl", "-4", "--noproxy", "*", "--silent", "--show-error",
            "--connect-timeout", "5", "--max-time", "12",
            "--output", str(body), "--dump-header", str(headers),
            "--write-out", "%{json}", "--request", method,
        ]
        if parts.hostname == "api.mutating.tech":
            command.extend(["--header", "Origin: https://platform.mutating.tech"])
            if method == "OPTIONS":
                command.extend([
                    "--header", "Access-Control-Request-Method: GET",
                    "--header", "Access-Control-Request-Headers: content-type,x-requested-with",
                ])
            else:
                command.extend([
                    "--header", "Content-Type: application/json",
                    "--header", "X-Requested-With: ThrongDashboard",
                ])
        if address != "dns":
            command.extend(["--resolve", f"{parts.hostname}:443:{address}"])
        command.append(url)
        response = subprocess.run(command, capture_output=True, text=True, timeout=18)
        metadata = json.loads(response.stdout) if response.stdout else {}
        response_headers = {}
        for line in headers.read_text(errors="replace").splitlines() if headers.exists() else []:
            key, separator, value = line.partition(":")
            if separator and key.lower() in {
                "access-control-allow-origin", "access-control-allow-credentials",
                "access-control-allow-headers", "access-control-allow-methods",
                "content-security-policy", "content-type", "vary",
            }:
                response_headers[key.lower()] = value.strip()
        result = {
            "check": name, "method": method, "url": url, "address": address,
            "exit_code": response.returncode, "error": response.stderr.strip(),
            "headers": response_headers,
            **{key: metadata.get(key) for key in (
                "remote_ip", "http_code", "ssl_verify_result", "time_connect",
                "time_appconnect", "time_total", "size_download",
            )},
        }
        if name == "dashboard_config" and body.exists():
            text = body.read_text(errors="replace")
            match = re.search(r"window\.__TMC_API_URL__\s*=\s*(\"[^\"]*\")", text)
            result["api_base"] = json.loads(match[1]) if match else None
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--proxy", required=True)
    arguments = parser.parse_args()
    cases = [
        ("website", "https://mutating.tech/", "GET"),
        ("dashboard", "https://platform.mutating.tech/", "GET"),
        ("dashboard_config", "https://platform.mutating.tech/config.js", "GET"),
        ("api_ready", "https://api.mutating.tech/health/ready", "GET"),
        ("api_public", "https://api.mutating.tech/v1/agent-capabilities", "GET"),
        ("browser_session", "https://api.mutating.tech/v1/auth/session", "GET"),
        ("github_status", "https://api.mutating.tech/v1/github/status", "GET"),
        ("github_preflight", "https://api.mutating.tech/v1/github/status", "OPTIONS"),
    ]
    dns = {}
    for host in ["mutating.tech", "api.mutating.tech", "platform.mutating.tech"]:
        try:
            dns[host] = list(dict.fromkeys(
                item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            ))
        except OSError as error:
            dns[host] = {"error": str(error)}
    work = [(case, address) for address in ["dns", arguments.origin, arguments.proxy] for case in cases]
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda item: probe(*item), work))
    print(json.dumps({"system_dns": dns, "probes": results}))


if __name__ == "__main__":
    main()
