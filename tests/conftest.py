"""Shared pytest fixtures for browser-based site tests."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ColorScheme = Literal["dark", "light", "no-preference"]
ReducedMotion = Literal["reduce", "no-preference"]
DriverFactory = Callable[..., Page]


def _find_available_port() -> int:
    """Return a currently available local TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        return int(port_socket.getsockname()[1])


def _wait_for_site(base_url: str, process: subprocess.Popen[bytes]) -> None:
    """Wait until the development server answers or fail with a clear error."""

    deadline = time.monotonic() + 60

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"The site server exited early with code {process.returncode}."
            )

        try:
            with urlopen(base_url, timeout=1) as response:
                if response.status < 500:
                    return
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.2)

    raise RuntimeError(f"The site did not become available at {base_url}.")


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    """Provide the tested site URL and manage a local server when needed."""

    configured_url = os.environ.get("TEST_BASE_URL")
    if configured_url:
        yield configured_url.rstrip("/")
        return

    port = _find_available_port()
    local_url = f"http://localhost:{port}"
    command = [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )

    try:
        _wait_for_site(local_url, process)
        yield local_url
    finally:
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Launch one headless Chromium instance shared by the test session."""

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        yield chromium
        chromium.close()


@pytest.fixture
def driver(
    browser: Browser,
    base_url: str,
) -> Iterator[DriverFactory]:
    """Pass tests a factory that creates isolated, theme-aware web drivers."""

    contexts: list[BrowserContext] = []

    def create_driver(
        *,
        color_scheme: ColorScheme = "no-preference",
        stored_theme: str | None = None,
        storage_available: bool = True,
        system_theme_available: bool = True,
        viewport: tuple[int, int] | None = None,
        device_scale_factor: float | None = None,
        reduced_motion: ReducedMotion = "no-preference",
        load_interactive_visuals: bool = True,
        animation_frame_interval_ms: float | None = None,
        controllable_animation_frames: bool = False,
    ) -> Page:
        storage_state = {"cookies": [], "origins": []}

        if stored_theme is not None:
            storage_state["origins"].append(
                {
                    "origin": base_url,
                    "localStorage": [
                        {"name": "tmc-theme", "value": stored_theme},
                    ],
                }
            )

        context = browser.new_context(
            color_scheme=color_scheme,
            storage_state=storage_state,
            viewport=(
                {"width": viewport[0], "height": viewport[1]}
                if viewport is not None
                else None
            ),
            device_scale_factor=device_scale_factor,
            reduced_motion=reduced_motion,
        )

        if not storage_available:
            context.add_init_script(
                """
                (() => {
                  const originalGetItem = Storage.prototype.getItem;
                  Storage.prototype.getItem = function (key) {
                    if (key === "tmc-theme") {
                      throw new Error("Theme storage is unavailable");
                    }
                    return originalGetItem.call(this, key);
                  };
                })();
                """
            )

        if not system_theme_available:
            context.add_init_script(
                """
                (() => {
                  const originalMatchMedia = window.matchMedia.bind(window);
                  window.matchMedia = function (query) {
                    if (!query.includes("prefers-color-scheme")) {
                      return originalMatchMedia(query);
                    }

                    return {
                      matches: false,
                      media: query,
                      onchange: null,
                      addEventListener() {},
                      removeEventListener() {},
                      addListener() {},
                      removeListener() {},
                      dispatchEvent() {
                        return false;
                      },
                    };
                  };
                })();
                """
            )

        if not load_interactive_visuals:
            context.route("**/muta-tentacles.html*", lambda route: route.abort())
            context.route("**/tokamak-reactor.html*", lambda route: route.abort())

        if animation_frame_interval_ms is not None:
            frame_interval = float(animation_frame_interval_ms)
            assert frame_interval > 0
            context.add_init_script(
                f"""
                (() => {{
                  const frameInterval = {frame_interval!r};
                  const nativeRequestAnimationFrame =
                    window.requestAnimationFrame.bind(window);
                  let virtualTimestamp = 0;
                  let previousNativeTimestamp = null;

                  window.requestAnimationFrame = (callback) =>
                    nativeRequestAnimationFrame((timestamp) => {{
                      if (
                        previousNativeTimestamp === null ||
                        timestamp !== previousNativeTimestamp
                      ) {{
                        previousNativeTimestamp = timestamp;
                        virtualTimestamp += frameInterval;
                      }}
                      callback(virtualTimestamp);
                    }});
                }})();
                """
            )

        if controllable_animation_frames:
            context.add_init_script(
                """
                (() => {
                  const nativeRequestAnimationFrame =
                    window.requestAnimationFrame.bind(window);
                  let animationFramesPaused = false;
                  let queuedCallbacks = [];

                  window.__mutaTestSetAnimationFramesPaused = (paused) => {
                    animationFramesPaused = Boolean(paused);
                    if (animationFramesPaused || queuedCallbacks.length === 0) {
                      return;
                    }

                    const callbacks = queuedCallbacks;
                    queuedCallbacks = [];
                    for (const callback of callbacks) {
                      nativeRequestAnimationFrame(callback);
                    }
                  };

                  window.requestAnimationFrame = (callback) =>
                    nativeRequestAnimationFrame((timestamp) => {
                      if (animationFramesPaused) {
                        queuedCallbacks.push(callback);
                        return;
                      }
                      callback(timestamp);
                    });
                })();
                """
            )

        contexts.append(context)
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
        return page

    yield create_driver

    for context in reversed(contexts):
        context.close()
