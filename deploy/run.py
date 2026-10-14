"""Run the API/controller and dashboard as one supervised systemd service."""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from throng_api.cli import migrate
from throng_api.config import Settings

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "platform/api"
WEB_ROOT = ROOT / "platform/web"
logger = logging.getLogger("throng.platform")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "migrate"], default="serve", nargs="?")
    parser.add_argument("--env-file", type=Path, default=API_ROOT / ".env")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--dashboard-port", type=int, default=8001)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    arguments.env_file = arguments.env_file.resolve()
    os.chdir(API_ROOT)
    migrate(Settings(_env_file=arguments.env_file))
    if arguments.command == "migrate":
        return 0
    if not (WEB_ROOT / "dist/index.html").is_file():
        parser.error("Build the dashboard first: npm ci && npm run build:web")

    # Systemd loads the production EnvironmentFile. For manual/local starts, load it too.
    from dotenv import dotenv_values

    environment = {
        **{
            key: value
            for key, value in dotenv_values(arguments.env_file).items()
            if value is not None
        },
        **os.environ,
        "DASHBOARD_API_URL": f"http://127.0.0.1:{arguments.api_port}",
    }
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    children = []

    def launch(application, port, extra=()):
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                application,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
                *extra,
            ],
            cwd=API_ROOT,
            env=environment,
        )
        children.append(child)
        return child

    try:
        api = launch("throng_api.app:app", arguments.api_port)
        deadline = time.monotonic() + 60
        while not stopping.is_set():
            if api.poll() is not None or time.monotonic() >= deadline:
                logger.error("API failed to become ready")
                return 1
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{arguments.api_port}/health/ready", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                pass
            stopping.wait(0.2)
        if stopping.is_set():
            return 0
        launch("server.app:app", arguments.dashboard_port, ("--app-dir", str(WEB_ROOT)))
        while not stopping.wait(0.2):
            if any(child.poll() is not None for child in children):
                logger.error("A component exited; stopping the entire platform")
                return 1
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 35
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
