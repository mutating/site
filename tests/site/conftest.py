"""Shared pytest fixtures for browser-based site tests."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
import tomllib
import fcntl
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPOSITORY_ROOT / "site"
ColorScheme = Literal["dark", "light", "no-preference"]
ReducedMotion = Literal["reduce", "no-preference"]
DriverFactory = Callable[..., Page]


def copy_controlled_test_content(target: Path) -> None:
    """Create a test content tree that is independent of live editorial posts.

    Browser and template-contract tests need stable documents to exercise, but
    they must not acquire expectations about whichever articles happen to be
    published in ``content/blog`` or ``content/news``. Keep the real section
    pages and author profiles, remove both editorial inventories from the copy,
    and replace them with versioned synthetic fixtures.
    """

    shutil.copytree(PROJECT_ROOT / "content", target)
    blog_directory = target / "blog"
    news_directory = target / "news"
    for section_directory in (blog_directory, news_directory):
        for content_entry in section_directory.iterdir():
            if content_entry.name.startswith("_index."):
                continue
            if content_entry.is_dir():
                shutil.rmtree(content_entry)
            else:
                content_entry.unlink()

    fixture_directory = Path(__file__).resolve().parent / "fixtures"
    for fixture_post in fixture_directory.glob("*-post.md"):
        shutil.copy2(fixture_post, blog_directory / fixture_post.name)
    for fixture_bundle in fixture_directory.glob("*-post"):
        if fixture_bundle.is_dir():
            shutil.copytree(
                fixture_bundle,
                blog_directory / fixture_bundle.name,
            )
    for fixture_news in fixture_directory.glob("*-news.md"):
        shutil.copy2(fixture_news, news_directory / fixture_news.name)
    for fixture_author in fixture_directory.glob("*-author.md"):
        shutil.copy2(fixture_author, target / "authors" / fixture_author.name)
    for fixture_bundle in fixture_directory.glob("*-author"):
        if fixture_bundle.is_dir():
            shutil.copytree(
                fixture_bundle,
                target / "authors" / fixture_bundle.name,
            )


def _write_paginated_tag_posts(temporary_content: Path) -> None:
    """Create a controlled tag collection that always spans two pages."""

    config = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
    pager_size = int(config["pagination"]["pagerSize"])
    blog_directory = temporary_content / "blog"

    for index in range(pager_size + 2):
        post_number = index + 1
        fixture_date = date(1998, 1, 1) + timedelta(days=index)
        title_period = "." if index == pager_size + 1 else ""
        (blog_directory / f"tag-pagination-{post_number:02d}.md").write_text(
            "\n".join(
                (
                    "---",
                    f'title: "Controlled tag signal {post_number:02d}{title_period}"',
                    f"date: {fixture_date.isoformat()}T00:00:00Z",
                    f'slug: "tag-pagination-{post_number:02d}"',
                    'description: "A controlled post for tag pagination tests."',
                    "tags:",
                    "  - controlled-pagination",
                    "---",
                    "",
                    "This fixture exists only inside the temporary test site.",
                    "",
                )
            ),
            encoding="utf-8",
        )


def _write_paginated_author_posts(temporary_content: Path) -> None:
    """Create an author archive with older posts spanning two pages."""

    config = tomllib.loads((PROJECT_ROOT / "hugo.toml").read_text())
    pager_size = int(config["pagination"]["pagerSize"])
    blog_directory = temporary_content / "blog"
    _write_author_archive(temporary_content, "test-author")

    for index in range(pager_size + 2):
        post_number = index + 1
        fixture_date = date(1997, 1, 1) + timedelta(days=index)
        (blog_directory / f"author-pagination-{post_number:02d}.md").write_text(
            "\n".join(
                (
                    "---",
                    f'title: "Controlled author signal {post_number:02d}"',
                    f"date: {fixture_date.isoformat()}T00:00:00Z",
                    f'slug: "author-pagination-{post_number:02d}"',
                    'description: "A controlled post for author pagination tests."',
                    'author: "test-author"',
                    "tags: []",
                    "---",
                    "",
                    "This fixture exists only inside the temporary test site.",
                    "",
                )
            ),
            encoding="utf-8",
        )


def _write_author_archive(temporary_content: Path, author_slug: str) -> None:
    """Create the content node that renders one controlled author archive."""

    archive_directory = temporary_content / "author-posts" / author_slug
    archive_directory.mkdir(parents=True)
    (archive_directory / "_index.md").write_text(
        "\n".join(
            (
                "---",
                'title: "Controlled author archive"',
                f'author: "{author_slug}"',
                'type: "author-posts"',
                "---",
                "",
                "Controlled archive content.",
                "",
            )
        ),
        encoding="utf-8",
    )


def _find_available_port() -> int:
    """Return a currently available local TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        return int(port_socket.getsockname()[1])


def _wait_for_site(base_url: str, process: subprocess.Popen[bytes]) -> bool:
    """Return when Hugo answers, or report an early process exit."""

    deadline = time.monotonic() + 60
    retry_delay = 0.01

    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False

        try:
            with urlopen(base_url, timeout=1) as response:
                if response.status < 500:
                    return process.poll() is None
        except (URLError, TimeoutError, ConnectionError):
            pass

        time.sleep(retry_delay)
        retry_delay = min(0.1, retry_delay * 2)

    raise RuntimeError(f"The site did not become available at {base_url}.")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a complete Hugo process group without leaving child work."""

    if process.poll() is not None:
        return

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


def _start_hugo_server(
    temporary_content: Path,
    cache_directory: Path,
) -> tuple[subprocess.Popen[bytes], str]:
    """Start a deterministic Hugo test server on an available local port.

    Hugo's development server injects a LiveReload client by default. That
    client is useful while editing, but a reconnect or rebuild can navigate a
    browser page in the middle of a long Playwright assertion and erase the
    DOM being inspected. Browser tests exercise the generated site, not this
    development-only transport, so the server keeps file watching/building but
    disables browser reload injection.
    """

    for _attempt in range(10):
        port = _find_available_port()
        local_url = f"http://localhost:{port}"
        command = [
            "hugo",
            "server",
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--baseURL",
            f"{local_url}/",
            "--contentDir",
            str(temporary_content),
            "--cacheDir",
            str(cache_directory),
            "--disableFastRender",
            "--disableLiveReload",
            "--noBuildLock",
            "--renderToMemory",
        ]
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )

        try:
            if _wait_for_site(local_url, process):
                return process, local_url
        except BaseException:
            _stop_process(process)
            raise

        process.wait(timeout=5)

    raise RuntimeError("Hugo could not acquire a live port after 10 attempts.")


def _validate_hugo_build_once(
    temporary_content: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Validate one disk build per pytest run, shared by every xdist worker.

    Every worker needs its own live Hugo process because pytest may distribute
    browser sessions arbitrarily, but the preflight disk build is immutable and
    identical for all of them. Building it once avoids multiplying the complete
    static tree by the worker count at peak usage while retaining the same Hugo
    compilation check before any browser test starts.
    """

    worker_base = tmp_path_factory.getbasetemp()
    shared_base = (
        worker_base.parent
        if os.environ.get("PYTEST_XDIST_WORKER")
        else worker_base
    )
    coordination_directory = shared_base / "mutating-hugo-preflight"
    coordination_directory.mkdir(exist_ok=True)
    lock_path = coordination_directory / "build.lock"
    success_marker = coordination_directory / "build.complete"

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if success_marker.exists():
            return

        build_destination = coordination_directory / "build"
        cache_directory = coordination_directory / "cache"
        try:
            build = subprocess.run(
                [
                    "hugo",
                    "--cleanDestinationDir",
                    "--contentDir",
                    str(temporary_content),
                    "--destination",
                    str(build_destination),
                    "--cacheDir",
                    str(cache_directory),
                    "--noBuildLock",
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if build.returncode != 0:
                output = build.stdout.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Hugo build failed before tests started:\n{output}"
                )
            success_marker.write_text("ok\n", encoding="utf-8")
        finally:
            shutil.rmtree(build_destination, ignore_errors=True)


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Build Hugo, run it on a temporary nonstandard port, then stop it."""

    configured_url = os.environ.get("TEST_BASE_URL")
    if configured_url:
        yield configured_url.rstrip("/")
        return

    temporary_root = tmp_path_factory.mktemp("mutating-hugo")
    temporary_content = temporary_root / "content"
    cache_directory = temporary_root / "cache"
    copy_controlled_test_content(temporary_content)
    _write_paginated_tag_posts(temporary_content)
    _write_paginated_author_posts(temporary_content)
    _write_author_archive(temporary_content, "test-robot-author")

    _validate_hugo_build_once(temporary_content, tmp_path_factory)

    process, local_url = _start_hugo_server(
        temporary_content,
        cache_directory,
    )

    try:
        yield local_url
    finally:
        _stop_process(process)


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
    request: pytest.FixtureRequest,
) -> Iterator[DriverFactory]:
    """Pass tests a factory that creates isolated, theme-aware web drivers."""

    contexts: list[BrowserContext] = []

    def create_driver(
        *,
        color_scheme: ColorScheme = "no-preference",
        stored_theme: str | None = None,
        stored_language: str | None = "en",
        device_locale: str | None = None,
        storage_available: bool = True,
        system_theme_available: bool = True,
        viewport: tuple[int, int] | None = None,
        device_scale_factor: float | None = None,
        reduced_motion: ReducedMotion = "no-preference",
        load_interactive_visuals: bool | None = None,
        bounded_tentacle_rendering: bool = False,
        manual_tentacle_rendering: bool = False,
        animation_frame_interval_ms: float | None = None,
        controllable_animation_frames: bool = False,
        steppable_animation_frames: bool = False,
        initial_path: str = "",
        wait_for_theme_ready: bool = True,
        init_scripts: tuple[str, ...] = (),
    ) -> Page:
        assert not initial_path or initial_path.startswith("/")
        if controllable_animation_frames and steppable_animation_frames:
            raise ValueError(
                "controllable_animation_frames and "
                "steppable_animation_frames are mutually exclusive"
            )
        storage_state = {"cookies": [], "origins": []}

        local_storage: list[dict[str, str]] = []
        if stored_theme is not None:
            local_storage.append(
                {"name": "tmc-theme", "value": stored_theme}
            )
        if stored_language is not None:
            if stored_language not in {"en", "ru"}:
                raise ValueError("stored_language must be 'en', 'ru', or None")
            local_storage.append(
                {"name": "tmc-language", "value": stored_language}
            )
        if local_storage:
            storage_state["origins"].append(
                {
                    "origin": base_url,
                    "localStorage": local_storage,
                }
            )

        context = browser.new_context(
            color_scheme=color_scheme,
            locale=device_locale,
            storage_state=storage_state,
            viewport=(
                {"width": viewport[0], "height": viewport[1]}
                if viewport is not None
                else None
            ),
            device_scale_factor=device_scale_factor,
            reduced_motion=reduced_motion,
        )

        # Context init scripts run before the first document's page scripts.
        # Callers must provide self-contained scripts: Playwright does not
        # define ordering between multiple context/page init scripts.
        for script in init_scripts:
            if not isinstance(script, str):
                raise TypeError("init_scripts entries must be strings")
            context.add_init_script(script)

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

        should_load_interactive_visuals = (
            request.node.get_closest_marker("interactive_visuals") is not None
            if load_interactive_visuals is None
            else load_interactive_visuals
        )

        if not should_load_interactive_visuals:
            context.route("**/muta-tentacles.html*", lambda route: route.abort())
            context.route("**/tokamak-reactor.html*", lambda route: route.abort())

        if manual_tentacle_rendering and not bounded_tentacle_rendering:
            raise ValueError(
                "manual_tentacle_rendering requires "
                "bounded_tentacle_rendering"
            )

        if bounded_tentacle_rendering:
            # The production tentacle density is intentionally expensive. On
            # GitHub's Linux software renderer it can monopolize Chromium
            # before a test gets a chance to rewrite the already-started
            # iframe. Install the profile at document start, while the parser
            # is still constructing the authored data-prewarm-src element.
            # This keeps the real WebGL runtime and iframe protocol under test
            # while bounding scene density and backing resolution. Tests that
            # inspect only the iframe protocol may additionally opt into its
            # existing manual-frame mode so continuous software rendering
            # cannot monopolize the browser between assertions.
            bounded_profile_script = """
                (() => {
                  const useManualAnimationFrame =
                    __MANUAL_TENTACLE_RENDERING__;
                  const boundTentacleSource = (root) => {
                    for (const frame of root.querySelectorAll(
                      'iframe[data-prewarm-src*="/muta-tentacles.html"]',
                    )) {
                      const source = new URL(
                        frame.dataset.prewarmSrc,
                        location.href,
                      );
                      source.searchParams.set("max-tentacles", "64");
                      source.searchParams.set("render-supersample", "0.5");
                      source.searchParams.set("generation-seed", "12345");
                      if (useManualAnimationFrame) {
                        source.searchParams.set(
                          "manual-animation-frame",
                          "1",
                        );
                      }
                      frame.dataset.prewarmSrc =
                        `${source.pathname}${source.search}${source.hash}`;
                    }
                  };
                  const observer = new MutationObserver(() => {
                    boundTentacleSource(document);
                  });
                  observer.observe(document, {
                    childList: true,
                    subtree: true,
                  });
                  addEventListener("DOMContentLoaded", () => {
                    boundTentacleSource(document);
                    observer.disconnect();
                  }, { once: true });
                })();
                """.replace(
                    "__MANUAL_TENTACLE_RENDERING__",
                    "true" if manual_tentacle_rendering else "false",
                )
            context.add_init_script(bounded_profile_script)

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

        if steppable_animation_frames:
            context.add_init_script(
                """
                (() => {
                  const nativeRequestAnimationFrame =
                    window.requestAnimationFrame.bind(window);
                  const nativeCancelAnimationFrame =
                    window.cancelAnimationFrame.bind(window);
                  let animationFramesPaused = false;
                  let nextRequestId = 1;
                  let virtualTimestamp = performance.now();
                  const requests = new Map();

                  const scheduleNativeRequest = (requestId, request) => {
                    request.queued = false;
                    request.nativeId = nativeRequestAnimationFrame(
                      (timestamp) => {
                        request.nativeId = 0;
                        if (!requests.has(requestId)) return;
                        if (animationFramesPaused) {
                          request.queued = true;
                          request.timestamp = timestamp;
                          return;
                        }
                        requests.delete(requestId);
                        virtualTimestamp = Math.max(
                          virtualTimestamp,
                          timestamp,
                        );
                        request.callback(timestamp);
                      },
                    );
                  };

                  window.requestAnimationFrame = (callback) => {
                    const requestId = nextRequestId;
                    nextRequestId += 1;
                    const request = {
                      callback,
                      nativeId: 0,
                      queued: animationFramesPaused,
                      timestamp: null,
                    };
                    requests.set(requestId, request);
                    if (!animationFramesPaused) {
                      scheduleNativeRequest(requestId, request);
                    }
                    return requestId;
                  };

                  window.cancelAnimationFrame = (requestId) => {
                    const request = requests.get(requestId);
                    if (!request) return;
                    requests.delete(requestId);
                    if (request.nativeId) {
                      nativeCancelAnimationFrame(request.nativeId);
                    }
                  };

                  window.__mutaTestSetAnimationFramesPaused = (paused) => {
                    animationFramesPaused = Boolean(paused);
                    if (animationFramesPaused) return;
                    for (const [requestId, request] of requests) {
                      if (request.queued && !request.nativeId) {
                        scheduleNativeRequest(requestId, request);
                      }
                    }
                  };

                  window.__mutaTestFlushAnimationFrames = (
                    count = 1,
                    intervalMilliseconds = 1000 / 60,
                  ) => {
                    if (!animationFramesPaused) {
                      throw new Error(
                        'Animation frames must be paused before flushing',
                      );
                    }
                    const requestedCount = Math.max(
                      0,
                      Math.floor(Number(count)),
                    );
                    const interval = Number(intervalMilliseconds);
                    if (!Number.isFinite(interval) || interval <= 0) {
                      throw new TypeError(
                        'Animation frame interval must be positive',
                      );
                    }
                    let executed = 0;
                    while (executed < requestedCount) {
                      const queuedRequest = [...requests].find(
                        ([, request]) => request.queued,
                      );
                      if (!queuedRequest) break;
                      const [requestId, request] = queuedRequest;
                      requests.delete(requestId);
                      virtualTimestamp = Math.max(
                        virtualTimestamp + interval,
                        request.timestamp || 0,
                        performance.now(),
                      );
                      request.callback(virtualTimestamp);
                      executed += 1;
                    }
                    return executed;
                  };

                  window.__mutaTestPendingAnimationFrameCount = () =>
                    requests.size;
                })();
                """
            )

        contexts.append(context)
        page = context.new_page()
        page.goto(f"{base_url}{initial_path}", wait_until="domcontentloaded")
        if wait_for_theme_ready:
            page.locator(
                'html[data-theme-ready="true"]'
            ).wait_for(state="attached")
        return page

    yield create_driver

    for context in reversed(contexts):
        context.close()
