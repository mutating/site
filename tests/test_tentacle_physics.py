"""Browser regression coverage for the interactive tentacle physics clock."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect


DriverFactory = Callable[..., Page]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_STATIC_ROOT = PROJECT_ROOT / "themes" / "mutating" / "static"
DIAGNOSTIC_QUERY = (
    "?max-tentacles=64&render-supersample=0.5"
    "&manual-animation-frame=1&interaction-diagnostics=1"
    "&generation-seed=12345"
)
pytestmark = [
    pytest.mark.interactive_visuals,
    pytest.mark.xdist_group(name="tentacle-webgl"),
]


def _wait_for_bounded_contact_tentacles_source(
    frame: Locator,
    *,
    manual_animation_frame: bool = False,
) -> None:
    """Wait until prewarming exposes the bounded real-WebGL iframe URL."""

    manual_frame_query = (
        r"&manual-animation-frame=1" if manual_animation_frame else ""
    )
    expect(frame).to_have_attribute(
        "src",
        re.compile(
            r"^/muta-tentacles\.html\?v=[0-9a-f]{64}&embedded=1"
            r"&max-tentacles=64&render-supersample=0\.5"
            r"&generation-seed=12345" + manual_frame_query + r"$"
        ),
        timeout=30_000,
    )


def test_tentacle_page_shows_accessible_fallback_without_webgl2(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Replace the Muta canvas with explanatory text when WebGL2 is unavailable."""

    page = driver(
        initial_path="/blog/",
        load_interactive_visuals=True,
        init_scripts=(
            """
            (() => {
              const nativeGetContext = HTMLCanvasElement.prototype.getContext;
              HTMLCanvasElement.prototype.getContext = function (kind, options) {
                if (kind === "webgl2") return null;
                return nativeGetContext.call(this, kind, options);
              };
            })();
            """,
        ),
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/muta-tentacles.html", wait_until="domcontentloaded")

    expect(page.locator("#muta")).to_have_count(0)
    fallback = page.get_by_text(
        "Для рендера Muta требуется браузер с поддержкой WebGL 2.",
        exact=True,
    )
    expect(fallback).to_be_visible()
    assert fallback.text_content().strip()
    assert errors == []


@pytest.mark.parametrize(
    ("requested_format", "expected_format", "color_bytes_per_pixel"),
    [
        ("rgba8", "rgba8", 4),
        ("rgba16f", "rgba16f", 8),
    ],
)
def test_color_render_target_format_is_selectable_for_ab_validation(
    driver: DriverFactory,
    requested_format: str,
    expected_format: str,
    color_bytes_per_pixel: int,
) -> None:
    """Keep the compact default measurable against the legacy float path."""

    page = driver(
        initial_path=(
            f"/muta-tentacles.html{DIAGNOSTIC_QUERY}"
            f"&color-target-format={requested_format}"
        ),
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    expect(canvas).to_have_attribute(
        "data-color-target-format",
        expected_format,
    )
    expect(canvas).to_have_attribute(
        "data-geometry-target-format",
        "rgba16f",
    )
    dimensions = canvas.evaluate("element => [element.width, element.height]")
    pixel_count = dimensions[0] * dimensions[1]
    assert int(canvas.get_attribute("data-color-target-bytes")) == (
        pixel_count * color_bytes_per_pixel * 3
    )
    assert int(canvas.get_attribute("data-geometry-target-bytes")) == (
        pixel_count * 8
    )


def test_default_color_targets_use_compact_rgba8_format(
    driver: DriverFactory,
) -> None:
    """Use the broadly optimized compact format on the production path."""

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        "() => document.querySelector('#muta')?.dataset.colorTargetFormat"
    )
    assert canvas.get_attribute("data-color-target-format") == "rgba8"
    dimensions = canvas.evaluate("element => [element.width, element.height]")
    pixel_count = dimensions[0] * dimensions[1]
    assert int(canvas.get_attribute("data-render-target-bytes")) == (
        pixel_count * (3 * 4 + 8)
    )


def test_compact_hdr_color_targets_fall_back_to_rgba8(
    driver: DriverFactory,
) -> None:
    """An unsupported packed-HDR framebuffer must not blank the hero."""

    page = driver(
        initial_path=(
            f"/muta-tentacles.html{DIAGNOSTIC_QUERY}"
            "&color-target-format=r11f-g11f-b10f"
        ),
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
        init_scripts=(
            """
            (() => {
              const textureFormats = new WeakMap();
              const framebufferColorFormats = new WeakMap();
              const nativeStorage =
                WebGL2RenderingContext.prototype.texStorage2D;
              const nativeAttach =
                WebGL2RenderingContext.prototype.framebufferTexture2D;
              const nativeStatus =
                WebGL2RenderingContext.prototype.checkFramebufferStatus;

              WebGL2RenderingContext.prototype.texStorage2D = function (
                target, levels, internalFormat, width, height
              ) {
                const texture = this.getParameter(this.TEXTURE_BINDING_2D);
                textureFormats.set(texture, internalFormat);
                return nativeStorage.call(
                  this, target, levels, internalFormat, width, height
                );
              };
              WebGL2RenderingContext.prototype.framebufferTexture2D =
                function (target, attachment, textarget, texture, level) {
                  const framebuffer = this.getParameter(
                    this.FRAMEBUFFER_BINDING
                  );
                  if (attachment === this.COLOR_ATTACHMENT0) {
                    framebufferColorFormats.set(
                      framebuffer,
                      textureFormats.get(texture),
                    );
                  }
                  return nativeAttach.call(
                    this, target, attachment, textarget, texture, level
                  );
                };
              WebGL2RenderingContext.prototype.checkFramebufferStatus =
                function (target) {
                  const framebuffer = this.getParameter(
                    this.FRAMEBUFFER_BINDING
                  );
                  if (
                    framebufferColorFormats.get(framebuffer) ===
                    this.R11F_G11F_B10F
                  ) {
                    return this.FRAMEBUFFER_UNSUPPORTED;
                  }
                  return nativeStatus.call(this, target);
                };
            })();
            """,
        ),
    )
    canvas = page.locator("#muta")
    expect(canvas).to_have_attribute("data-color-target-format", "rgba8")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    expect(canvas).to_have_attribute("data-rendered-frame-id", "1")


def test_gpu_timing_is_disabled_without_requesting_the_extension(
    driver: DriverFactory,
) -> None:
    """The production-default path does not even request the timer extension."""

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
        init_scripts=(
            """
            (() => {
              globalThis.__gpuTimerExtensionRequests = 0;
              const nativeGetExtension =
                WebGL2RenderingContext.prototype.getExtension;
              WebGL2RenderingContext.prototype.getExtension = function (name) {
                if (name === "EXT_disjoint_timer_query_webgl2") {
                  globalThis.__gpuTimerExtensionRequests += 1;
                }
                return nativeGetExtension.call(this, name);
              };
            })();
            """,
        ),
    )
    canvas = page.locator("#muta")
    expect(canvas).to_have_attribute("data-gpu-timing", "disabled")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )

    assert page.evaluate("globalThis.__gpuTimerExtensionRequests") == 0
    assert canvas.get_attribute("data-gpu-ms") is None


def test_gpu_timing_degrades_to_unsupported_without_affecting_rendering(
    driver: DriverFactory,
) -> None:
    """Missing timer support remains an ordinary, renderable runtime state."""

    page = driver(
        initial_path=(
            f"/muta-tentacles.html{DIAGNOSTIC_QUERY}&gpu-timing=1"
        ),
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
        init_scripts=(
            """
            (() => {
              const nativeGetExtension =
                WebGL2RenderingContext.prototype.getExtension;
              WebGL2RenderingContext.prototype.getExtension = function (name) {
                if (name === "EXT_disjoint_timer_query_webgl2") return null;
                return nativeGetExtension.call(this, name);
              };
            })();
            """,
        ),
    )
    canvas = page.locator("#muta")
    expect(canvas).to_have_attribute("data-gpu-timing", "unsupported")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    expect(canvas).to_have_attribute("data-rendered-frame-id", "1")
    assert canvas.get_attribute("data-gpu-ms") is None


def test_gpu_timing_publishes_an_asynchronous_result_when_supported(
    driver: DriverFactory,
) -> None:
    """A supported timer eventually reports finite GPU time without waiting."""

    page = driver(
        initial_path=(
            f"/muta-tentacles.html{DIAGNOSTIC_QUERY}"
            "&gpu-timing=1&gpu-timing-interval=1"
        ),
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    status = canvas.get_attribute("data-gpu-timing")
    assert status in {"enabled", "unsupported"}
    if status == "unsupported":
        pytest.skip("Browser GPU does not expose timer queries")

    for _index in range(180):
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
        if int(canvas.get_attribute("data-gpu-timing-samples") or "0") > 0:
            break
        page.wait_for_timeout(2)

    sample_count = int(
        canvas.get_attribute("data-gpu-timing-samples") or "0"
    )
    latest_gpu_ms = float(canvas.get_attribute("data-gpu-ms") or "nan")
    average_gpu_ms = float(
        canvas.get_attribute("data-gpu-ms-average") or "nan"
    )
    assert sample_count > 0
    assert math.isfinite(latest_gpu_ms) and latest_gpu_ms >= 0
    assert math.isfinite(average_gpu_ms) and average_gpu_ms >= 0


def test_tentacle_runtime_accepts_messages_only_from_its_same_origin_parent(
    driver: DriverFactory,
) -> None:
    """Tentacles accepts its parent but rejects self and wrong-origin commands."""

    page = driver(
        initial_path="/blog/",
        stored_theme="light",
        viewport=(320, 240),
        load_interactive_visuals=True,
    )
    page.evaluate(
        """
        () => {
          const frame = document.createElement("iframe");
          frame.id = "message-boundary-tentacles";
          frame.style.cssText = "width:240px;height:180px";
          frame.src = "/muta-tentacles.html?embedded=1&max-tentacles=64&render-supersample=0.5";
          document.body.append(frame);
        }
        """
    )
    canvas = page.frame_locator("#message-boundary-tentacles").locator("#muta")
    expect(canvas).to_be_attached(timeout=30_000)
    frame = page.locator("#message-boundary-tentacles")
    frame_root = page.frame_locator("#message-boundary-tentacles").locator("html")
    expect(frame_root).to_have_attribute("data-theme", "light")

    canvas.evaluate(
        """
        (element) => {
          const probe = "tentacles-self-message";
          element.dataset.selfMessageObserved = "false";
          const observeProbe = (event) => {
            if (event.data?.probe !== probe) return;
            removeEventListener("message", observeProbe);
            element.dataset.selfMessageObserved = "true";
          };
          addEventListener("message", observeProbe);
          window.postMessage({
            type: "muta-tentacles-theme",
            theme: "dark",
            probe,
          }, location.origin);
        }
        """
    )
    expect(canvas).to_have_attribute(
        "data-self-message-observed",
        "true",
        timeout=5_000,
    )
    expect(frame_root).to_have_attribute("data-theme", "light")

    canvas.evaluate(
        """
        () => dispatchEvent(new MessageEvent("message", {
          data: {
            type: "muta-tentacles-pointer-hover",
            x: 0.25,
            y: 0.75,
            inside: true,
          },
          origin: "https://attacker.example",
          source: window.parent,
        }))
        """
    )
    assert canvas.get_attribute("data-pointer-input-source") is None

    frame.evaluate(
        """
        (element) => {
          element.contentWindow.postMessage({
            type: "muta-tentacles-theme",
            theme: "dark",
          }, location.origin);
          element.contentWindow.postMessage({
            type: "muta-tentacles-pointer-hover",
            x: 0.25,
            y: 0.75,
            inside: true,
          }, location.origin);
        }
        """
    )
    expect(frame_root).to_have_attribute("data-theme", "dark")
    expect(canvas).to_have_attribute("data-pointer-input-source", "host-overlay")


def test_dense_surface_uses_the_bounded_spatial_shader(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep the production surface on the exact, bounded shader fast path.

    The renderer groups tentacle segments into spatial cells before drawing.
    Its production scene currently puts only about twenty candidates in the
    busiest cell, yet the former surface shader still carried a 900-iteration
    upper bound for every covered pixel. The optimized program has a 64-entry
    bound and is mathematically identical while the cell fits; if an unusual
    deformation exceeds that capacity, rendering must switch to the original
    full-scan program instead of dropping geometry.

    This regression loads the normal full-density rectangle rather than the
    reduced diagnostic fixture. After the first diagnostics interval it proves
    that the spatial lists stayed within the documented 64-entry safety margin
    and that WebGL selected the bounded program. A future change that silently
    disables the fast path, restores the oversized hot loop, or overfills the
    compact lookup texture therefore fails here before it can reintroduce the
    contact-section scroll hitch.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(1280, 720),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html?generation-seed=12345"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1100)")
    expect(canvas).to_have_attribute(
        "data-surface-shader-path",
        "fast-spatial",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-spatial-candidate-capacity",
        "64",
    )
    maximum_cell_load = int(
        canvas.get_attribute("data-maximum-spatial-cell-load") or "0"
    )
    assert 0 < maximum_cell_load < 64


def test_surface_grid_releases_cpu_upload_arrays(
    driver: DriverFactory,
) -> None:
    """Retain only the GPU buffers and immutable draw count after upload.

    ``WebGL2RenderingContext.bufferData`` copies the supplied typed-array
    contents into the currently bound GPU buffer before it returns. The CPU
    vertex and index arrays are therefore dead weight after initialization,
    but keeping the original ``grid`` object reachable used to retain both
    backing stores for the entire page lifetime. Rendering still needs the
    index count, so the implementation records that scalar before releasing
    the sole object reference.

    This regression derives both expected byte size and triangle index count
    from the runtime grid dimensions. It then advances a real manual WebGL
    frame to prove the saved count continues to drive ``drawElements`` after
    the arrays became collectable. A future context-restoration path must
    regenerate this deterministic grid rather than depending on the released
    arrays; documenting that constraint here prevents a memory regression from
    being introduced as an accidental recovery mechanism.
    """

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1100)")
    columns, rows = (
        canvas.get_attribute("data-surface-grid") or "0x0"
    ).split("x")
    grid_columns = int(columns)
    grid_rows = int(rows)
    expected_index_count = (grid_columns - 1) * (grid_rows - 1) * 6
    expected_released_bytes = (
        grid_columns * grid_rows * 2 * 4 + expected_index_count * 4
    )

    assert int(canvas.get_attribute("data-surface-index-count") or "0") == (
        expected_index_count
    )
    assert int(
        canvas.get_attribute("data-surface-grid-cpu-bytes-released") or "0"
    ) == expected_released_bytes
    expect(canvas).to_have_attribute(
        "data-rendered-frame-id",
        re.compile(r"^[1-9][0-9]*$"),
    )


def test_spatial_overflow_preserves_every_tentacle_via_full_fallback(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Prove the compact shader can never clip an overloaded spatial cell.

    The fast renderer is allowed to inspect at most its configured number of
    candidates per spatial cell. Visual fidelity would be lost if a denser
    future layout merely truncated the remaining tentacles. The production
    default leaves ample headroom, so this test deterministically lowers only
    the diagnostic capacity to one entry and forces an overflow without
    changing the real fallback algorithm.

    The scene must detect that overflow, mark spatial acceleration as a
    fallback, and draw with the original 900-skeleton shader. This specifically
    guards the no-visual-loss promise of the optimization: changing the compact
    buffer size can affect performance, but it must never make geometry vanish.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-spatial-candidates=1&generation-seed=12345"
            "&max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1100)")
    expect(canvas).to_have_attribute(
        "data-surface-shader-path",
        "full-fallback",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-spatial-acceleration",
        "fallback",
    )
    expect(canvas).to_have_attribute(
        "data-spatial-candidate-capacity",
        "1",
    )
    expect(canvas).to_have_attribute(
        "data-rendered-frame-id",
        re.compile(r"^[1-9][0-9]*$"),
    )


def test_reduced_spatial_grid_matches_previous_grid_pixel_for_pixel(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep the smaller spatial lookup grid from changing visible pixels.

    Grid dimensions are a performance detail: they may change which bounded
    cell supplies a skeleton candidate, but not the final pixels. Render one
    deterministic manual frame through the production 56-row grid and the same
    frame through the previous 64-row grid. Exact canvas equality protects the
    visual contract while allowing the lookup grid to be tuned independently.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    common_query = (
        "max-tentacles=64&render-supersample=0.5"
        "&manual-animation-frame=1&generation-seed=12345"
        "&animation-time-origin=0"
    )

    def render_frame(
        extra_query: str = "",
    ) -> tuple[str, int, list[int]]:
        page.goto(
            f"{base_url}/muta-tentacles.html?{common_query}{extra_query}",
            wait_until="domcontentloaded",
        )
        canvas = page.locator("#muta")
        expect(canvas).to_be_visible(timeout=30_000)
        page.mouse.move(0, 0)
        page.mouse.move(180, 115)
        return canvas.evaluate(
            """
            element => {
              const gl = element.getContext("webgl2");
              gl.disable(gl.DITHER);
              window.__mutaTentaclesDebugFrameAfter(1100);
              const pixels = new Uint8Array(
                gl.drawingBufferWidth * gl.drawingBufferHeight * 4,
              );
              gl.readPixels(
                0,
                0,
                gl.drawingBufferWidth,
                gl.drawingBufferHeight,
                gl.RGBA,
                gl.UNSIGNED_BYTE,
                pixels,
              );
              return [
                element.dataset.surfaceShaderPath || "",
                window.__mutaTentaclesDebugSpatialGridRows,
                Array.from(pixels),
              ];
            }
            """
        )

    reduced_path, reduced_rows, reduced_pixels = render_frame()
    previous_path, previous_rows, previous_pixels = render_frame(
        "&spatial-grid-rows=64"
    )

    assert reduced_path == "fast-spatial"
    assert previous_path == "fast-spatial"
    assert reduced_rows == 56
    assert previous_rows == 64
    assert reduced_pixels == previous_pixels


@pytest.mark.parametrize(
    "target_viewport",
    [
        (1024, 650),
        (755, 620),
        (390, 610),
    ],
)
def test_resized_contact_click_cannot_leave_a_stale_impulse_frame(
    driver: DriverFactory,
    base_url: str,
    target_viewport: tuple[int, int],
) -> None:
    """Catch the intermittent long attraction pulse seen after narrowing.

    The reported defect does not appear on every click because it requires a
    resize-related pause between animation frames. The WebGL surface submits a
    strong impulse in the first animation frame after ``pointerdown``. If
    Chromium then postpones a later frame while recalculating the newly narrow
    layout, that strong image used to remain visible long after the mouse button
    had already been released. The force clock itself could already be expired,
    so tests that inspected only a later normal frame missed the visible bug.

    This regression opens the exact production iframe document at the contact
    block's desktop dimensions, then narrows that already-loaded surface to the
    parametrized desktop, tablet, or phone dimensions and waits for its
    aspect-aware reload to finish. It deliberately uses the runtime's manual
    animation-frame mode—the browser condition that makes the race
    deterministic—while preserving real pointer events and the production
    force clock. Three actual left clicks must each show the source-strength
    impulse on press and submit an ordinary hover frame immediately on release
    without waiting for another animation frame. The release frame must neither
    retain peak force nor introduce a synthetic zero-force recovery kick.
    Repeating the click cycle is intentional: it guards the formerly
    intermittent second-or-later click as well as the first click after a
    resize.

    The test reduces only tentacle count and supersampling so all three viewport
    cases finish quickly in headless Chromium; it uses the unchanged production
    event handlers, force envelope, resize reload, and render clock.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(1440, 650),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    initial_time_origin = canvas.evaluate(
        "element => element.ownerDocument.defaultView.performance.timeOrigin"
    )

    page.set_viewport_size(
        {
            "width": target_viewport[0],
            "height": target_viewport[1],
        }
    )
    # In manual-frame mode the resize is consumed by the next explicit render;
    # that render schedules the production aspect-aware document reload.
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(16)")
    page.wait_for_function(
        "origin => performance.timeOrigin !== origin",
        arg=initial_time_origin,
        timeout=30_000,
    )
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(16)")
    expect(canvas).to_have_attribute(
        "data-rendered-frame-id",
        re.compile(r"^[1-9][0-9]*$"),
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-press-impulse-total-ms",
        "41.5",
    )

    pointer_x = target_viewport[0] * 0.84
    pointer_y = target_viewport[1] * 0.72
    page.mouse.move(pointer_x, pointer_y)

    for _ in range(3):
        page.wait_for_timeout(35)
        page.mouse.down(button="left")
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
        expect(canvas).to_have_attribute(
            "data-pointer-interaction-phase",
            "impulse",
            timeout=1_000,
        )
        page.mouse.up(button="left")
        released_state = canvas.evaluate(
            """
            element => ({
              phase: element.dataset.renderedInteractionPhase,
              pressed: element.dataset.renderedPointerPressed,
              force: Number(
                element.dataset.pointerInteractionForceMultiplier,
              ),
            })
            """
        )
        assert released_state["phase"] == "hover"
        assert released_state["pressed"] == "false"
        assert released_state["force"] == 1


def test_impulse_deadline_does_not_submit_a_second_synchronous_frame(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep the 41.5 ms deadline from blocking a queued mouse release.

    A click and a held press share the same initial source impulse. After
    ``0.28 / 6.75`` seconds the next normal animation frame must use ordinary
    2× held attraction. A previous attempted fix scheduled a timer that rendered
    that transition synchronously. On the full rectangular WebGL scene the
    second render could occupy the main thread before a queued ``pointerup`` was
    handled, extending a quick click by hundreds of milliseconds or seconds.

    This test deliberately disables regular animation frames, verifies that
    ``pointerdown`` itself submits nothing, explicitly advances the queued
    impulse frame, and waits beyond the complete source envelope. No hidden
    timer may submit another frame. It then advances one explicit 50 ms frame
    and verifies that the wall-clock force equation naturally resolves to held
    mode. This preserves the original physics while proving that the deadline
    itself can never insert blocking work ahead of release.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.mouse.move(120, 90)
    baseline_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    page.mouse.down(button="left")
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == baseline_frame
    expect(canvas).to_have_attribute(
        "data-immediate-impulse-frame",
        "disabled",
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")

    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "impulse",
        timeout=1_000,
    )
    impulse_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    page.wait_for_timeout(100)
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == impulse_frame

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(50)")
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=1_000,
    )
    page.mouse.up(button="left")
    released_phase = canvas.get_attribute(
        "data-rendered-interaction-phase"
    )
    assert released_phase == "hover"


@pytest.mark.parametrize(
    "viewport",
    [
        (390, 610),
        (1024, 650),
    ],
)
def test_full_density_surface_never_queues_a_deadline_render(
    driver: DriverFactory,
    base_url: str,
    viewport: tuple[int, int],
) -> None:
    """Exercise the deadline race with the complete production workload.

    The earlier suite reduced the surface to 64 tentacles and half-resolution
    rendering, so the erroneous timer-triggered WebGL frame completed quickly
    and rarely delayed ``pointerup`` enough to resemble the reported defect.
    The live rectangle instead generates its normal aspect-dependent density
    (hundreds of 50-link chains) at full supersampling. Under that load an extra
    synchronous render at 41.5 ms could occupy the main thread for long enough
    that the initial attraction appeared not to end.

    At phone and desktop sizes this regression uses the unmodified production
    density and rendering resolution. One explicit full-density frame primes
    the complete render pipeline before input; further idle frames would only
    repeat that workload. Normal animation is manually clocked so any
    unexpected frame can only come from an input/deadline callback. After one
    real press, ``pointerdown`` itself must not render; one explicit RAF then
    submits the impulse. Its frame id and measured input latency must remain
    unchanged for 120 ms—almost three complete impulse windows. The test then
    advances another physics frame by nine milliseconds and verifies that the
    peak was not applied a second time. Release must be processed normally.
    This full-density assertion is the part the earlier lightweight tests
    lacked: it catches the long visible deformation even when the nominal force
    timer and low-density state tests look correct.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=viewport,
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?manual-animation-frame=1&generation-seed=12345"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(16)")
    baseline_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    assert baseline_frame == 1
    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(
        box["x"] + box["width"] * 0.77,
        box["y"] + box["height"] * 0.66,
    )
    page.mouse.down(button="left")
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == baseline_frame
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    submitted = canvas.evaluate(
        """
        element => ({
          frame: Number(element.dataset.renderedFrameId),
          latency: element.dataset.inputToImpulseSubmissionMs,
          applications: element.dataset.impulsePhysicsApplications,
        })
        """
    )
    assert submitted["frame"] == baseline_frame + 1
    assert submitted["latency"] is not None
    assert submitted["applications"] == "1"

    page.wait_for_timeout(120)
    after_deadline = canvas.evaluate(
        """
        element => ({
          frame: Number(element.dataset.renderedFrameId),
          latency: element.dataset.inputToImpulseSubmissionMs,
          applications: element.dataset.impulsePhysicsApplications,
        })
        """
    )
    assert after_deadline == submitted

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
    after_next_frame = canvas.evaluate(
        """
        element => ({
          phase: element.dataset.renderedInteractionPhase,
          force: element.dataset.pointerInteractionForceMultiplier,
          applications: element.dataset.impulsePhysicsApplications,
          animationImpulseFrames:
            element.dataset.animationImpulseFramesSincePress,
        })
        """
    )
    assert after_next_frame == {
        "phase": "held",
        "force": "2.000",
        "applications": "1",
        "animationImpulseFrames": "1",
    }

    page.mouse.up(button="left")
    released_phase = canvas.get_attribute(
        "data-rendered-interaction-phase"
    )
    assert released_phase == "hover"


def test_slow_rendered_frame_matches_the_original_single_step_clock(
    driver: DriverFactory,
) -> None:
    """Preserve the source HTML's single capped physics step on slow frames.

    The contact surface can fall below 30 frames per second at wide resolutions.
    The original ``muta-tentacles.html`` intentionally advances physics only
    once per rendered frame and caps that step at 33 ms. The adapted surface
    previously split a 100 ms frame into four updates. That repeatedly applied
    the post-impulse held force before the next image appeared and made the
    initial click response look substantially longer than in the source file.

    This test supplies one 100 ms animation-frame timestamp and verifies that
    the embedded production surface performs exactly one 33 ms physics update,
    fixing both the number of force applications and their simulated duration.
    The frame is advanced through the runtime's manual clock so Linux software
    rendering cannot continuously monopolize Chromium between assertions.
    Only scene density and backing resolution are reduced; the authored iframe
    URL, embedded visibility lifecycle, production frame callback, and clock
    remain intact. Separate tests exercise the full-density workload.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        bounded_tentacle_rendering=True,
        manual_tentacle_rendering=True,
    )
    contact = page.locator("#contact")
    expect(contact).to_be_attached()
    frame = page.locator(".contact__tentacles")
    expect(frame).to_be_attached()
    _wait_for_bounded_contact_tentacles_source(
        frame,
        manual_animation_frame=True,
    )
    frame.evaluate(
        """
        (element) => {
          const source = new URL(element.src);
          if (source.searchParams.get("embedded") !== "1") {
            throw new Error("Expected the contact's embedded runtime");
          }
          source.searchParams.set("max-tentacles", "64");
          source.searchParams.set("render-supersample", "0.5");
          element.src = source.href;
        }
        """
    )
    contact.evaluate(
        """
        element => scrollTo({
          top: element.offsetTop,
          behavior: "instant",
        })
        """
    )
    page.wait_for_function(
        """
        (selector) => {
          const child = document.querySelector(selector)?.contentWindow;
          const source = child && new URL(child.location.href);
          const canvas = child?.document?.querySelector("#muta");
          return source?.searchParams.get("embedded") === "1" &&
            source.searchParams.get("max-tentacles") === "64" &&
            source.searchParams.get("render-supersample") === "0.5" &&
            canvas?.dataset.aspectReady === "true" &&
            canvas.dataset.aspectReloading === "false" &&
            canvas.dataset.runtimeVisible === "true";
        }
        """,
        arg=".contact__tentacles",
        timeout=30_000,
    )
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    frame.evaluate(
        "element => "
        "element.contentWindow.__mutaTentaclesDebugFrameAfter(100)"
    )

    expect(canvas).to_have_attribute(
        "data-physics-substeps",
        "1",
        timeout=30_000,
    )
    elapsed_milliseconds = float(
        canvas.get_attribute("data-physics-elapsed-ms") or "0"
    )

    assert elapsed_milliseconds == 33.0


def test_runtime_cancels_raf_while_offscreen_or_document_hidden(
    driver: DriverFactory,
) -> None:
    """Stop scheduling callbacks completely, then resume without a dead loop."""

    page = driver(
        initial_path=(
            "/muta-tentacles.html?max-tentacles=64"
            "&render-supersample=0.5&generation-seed=12345"
        ),
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
        init_scripts=(
            """
            (() => {
              const nativeRequestAnimationFrame =
                window.requestAnimationFrame.bind(window);
              const nativeCancelAnimationFrame =
                window.cancelAnimationFrame.bind(window);
              let syntheticHidden = false;
              let captureNextAnimationFrame = null;
              Object.defineProperty(Document.prototype, "hidden", {
                configurable: true,
                get: () => syntheticHidden,
              });
              globalThis.__mutaSetSyntheticHidden = (hidden) => {
                syntheticHidden = Boolean(hidden);
                document.dispatchEvent(new Event("visibilitychange"));
              };
              globalThis.__mutaRafProbe = {
                requests: 0,
                callbacks: 0,
                cancellations: 0,
              };
              globalThis.__mutaCaptureNextAnimationFrame = () =>
                new Promise((resolve) => {
                  captureNextAnimationFrame = resolve;
                });
              window.requestAnimationFrame = (callback) => {
                globalThis.__mutaRafProbe.requests += 1;
                return nativeRequestAnimationFrame((timestamp) => {
                  globalThis.__mutaRafProbe.callbacks += 1;
                  callback(timestamp);
                  if (captureNextAnimationFrame) {
                    const resolve = captureNextAnimationFrame;
                    captureNextAnimationFrame = null;
                    const data = document.querySelector("#muta").dataset;
                    resolve({
                      physicsElapsedMs: Number(data.physicsElapsedMs),
                      physicsFrameId: Number(data.physicsFrameId),
                    });
                  }
                });
              };
              window.cancelAnimationFrame = (request) => {
                globalThis.__mutaRafProbe.cancellations += 1;
                nativeCancelAnimationFrame(request);
              };
            })();
            """,
        ),
    )
    canvas = page.locator("#muta")
    page.wait_for_function(
        """
        () => {
          const data = document.querySelector("#muta")?.dataset;
          return data?.aspectReady === "true" &&
            data.animationLoop === "running" &&
            data.animationFrameScheduled === "true" &&
            Number(data.physicsFrameId) > 2;
        }
        """,
        timeout=30_000,
    )

    def animation_state() -> dict[str, int]:
        return canvas.evaluate(
            """
            (element) => ({
              requests: globalThis.__mutaRafProbe.requests,
              callbacks: globalThis.__mutaRafProbe.callbacks,
              cancellations: globalThis.__mutaRafProbe.cancellations,
              physics: Number(element.dataset.physicsFrameId || 0),
              rendered: Number(element.dataset.renderedFrameId || 0),
            })
            """
        )

    def assert_animation_stays_stopped() -> dict[str, int]:
        expect(canvas).to_have_attribute("data-animation-loop", "paused")
        expect(canvas).to_have_attribute(
            "data-animation-frame-scheduled",
            "false",
        )
        expect(canvas).to_have_attribute("data-runtime-paused", "true")
        stopped = animation_state()
        page.wait_for_timeout(250)
        assert animation_state() == stopped
        return stopped

    canvas.evaluate(
        """
        element => element.ownerDocument.defaultView
          .__mutaTentaclesSetRuntimeState({
            visible: false,
            scrollActive: false,
          })
        """
    )
    offscreen_state = assert_animation_stays_stopped()
    assert offscreen_state["cancellations"] >= 1

    canvas.evaluate(
        """
        element => element.ownerDocument.defaultView
          .__mutaTentaclesSetRuntimeState({
            visible: true,
            scrollActive: false,
          })
        """
    )
    expect(canvas).to_have_attribute("data-animation-loop", "running")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "true",
    )
    page.wait_for_function(
        """
        (previousPhysicsFrame) =>
          Number(document.querySelector("#muta").dataset.physicsFrameId) >
            previousPhysicsFrame
        """,
        arg=offscreen_state["physics"],
    )

    page.evaluate("globalThis.__mutaSetSyntheticHidden(true)")
    hidden_state = assert_animation_stays_stopped()
    assert hidden_state["cancellations"] > offscreen_state["cancellations"]

    resumed_frame = page.evaluate(
        """
        async () => {
          const firstFrame = globalThis.__mutaCaptureNextAnimationFrame();
          globalThis.__mutaSetSyntheticHidden(false);
          return firstFrame;
        }
        """
    )
    assert resumed_frame["physicsElapsedMs"] == 16.7
    assert resumed_frame["physicsFrameId"] > hidden_state["physics"]
    expect(canvas).to_have_attribute("data-animation-loop", "running")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "true",
    )


def test_first_visible_scroll_tick_primes_exactly_one_complete_frame(
    driver: DriverFactory,
) -> None:
    """Present the prewarmed surface once before scroll suppression takes over.

    The embedded contact runtime is initialized while its iframe is far below
    the viewport, but the offscreen lifecycle deliberately runs no RAF and
    therefore owns no completed texture yet. If the iframe first intersects
    the viewport in the middle of a wheel, trackpad, or touch scroll, the host
    sends ``visible: true`` and ``scrollActive: true`` together. Applying the
    ordinary scroll guard to that very first tick leaves the transparent canvas
    on screen until native ``scrollend`` or the 320 ms quiet-period fallback;
    with momentum still producing events, that startup blank can approach one
    second even though all WebGL resources were prewarmed successfully.

    This regression loads the real embedded WebGL runtime in its deterministic
    manual-frame mode. A tick while still invisible must leave both the physics
    and rendered-frame counters at zero. After the combined visible-and-scroll
    handoff, the first tick must advance both counters exactly once so the
    browser has a complete full-quality texture to composite. A second tick
    only one refresh interval later must advance neither counter: the primer
    bypasses the adaptive deadline once, but it does not restore continuous
    animation. Separate budget coverage advances through the later 30, 20, and
    12 FPS deadlines. Clearing ``scrollActive`` models the existing scroll-end
    handoff and must restore one ordinary physics-and-render tick immediately.

    Explicit ticks and counters make all three boundaries independent of wall
    time, refresh rate, GPU speed, and Playwright scheduling. Removing the
    primer leaves the first counters at zero; accidentally allowing animation
    throughout the gesture increments them twice; and failing to resume after
    scroll end leaves the final counters unchanged.
    """

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}&embedded=1",
        stored_theme="dark",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )

    def frame_counters() -> dict[str, int]:
        return canvas.evaluate(
            """
            element => ({
              rendered: Number(element.dataset.renderedFrameId || 0),
              physics: Number(element.dataset.physicsFrameId || 0),
            })
            """
        )

    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    assert frame_counters() == {"rendered": 0, "physics": 0}
    expect(canvas).to_have_attribute("data-runtime-visible", "false")

    page.evaluate(
        """
        () => window.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: true,
        })
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    primed = frame_counters()
    assert primed == {"rendered": 1, "physics": 1}

    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    assert frame_counters() == primed
    expect(canvas).to_have_attribute(
        "data-scroll-animation-tier",
        "conservative",
    )
    expect(canvas).to_have_attribute(
        "data-scroll-animation-interval-ms",
        "83.3",
    )

    page.evaluate(
        """
        () => window.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: false,
        })
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "active")
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    assert frame_counters() == {"rendered": 2, "physics": 2}


def test_scroll_animation_budget_adapts_and_never_catches_up(
    driver: DriverFactory,
) -> None:
    """Keep passive motion alive only inside the page-scroll frame budget.

    Freezing every physics and WebGL update made the contact surface look dead
    throughout a long scroll, while restoring its ordinary 60 FPS loop would
    return the main-thread and GPU contention that originally made the page
    move in steps. The runtime therefore measures two signals from work it is
    already doing: an exponential average of RAF cadence, which includes main-
    thread and compositor pressure, and an exponential average of a complete
    physics-plus-render submission. When optional GPU timer samples exist, the
    larger of CPU submission cost and real GPU time is used as frame cost.

    The passive-scroll tiers deliberately preserve every pixel of an admitted
    frame and reduce only temporal cadence. ``fast`` permits one frame every
    33.3 ms (30 FPS) when cadence is at most 20 ms and frame cost at most 7 ms;
    ``balanced`` permits one every 50 ms (20 FPS) through 28/12 ms;
    ``conservative`` permits one every 83.3 ms (12 FPS) through 45/20 ms. A
    runtime without enough history starts conservatively. Anything slower than
    the last boundary enters ``frozen``. A cadence-driven freeze may recover
    during the same gesture because cheap RAF callbacks continue refreshing
    that signal; a frame-cost-driven freeze intentionally remains until normal
    frames after scroll end supply safer cost evidence. Pointer presses and
    queued impulses retain their separate responsiveness exception and are not
    throttled by this passive budget.

    This deterministic regression injects each pair of performance signals
    into the real manual-frame runtime. For every finite tier, a transition
    tick establishes the scroll deadline, the millisecond immediately before
    that deadline must do no work, and the deadline itself must advance physics
    and rendering exactly once. Jumping forward by five intervals is then
    allowed to submit only one frame: missed animation is discarded instead of
    producing a catch-up burst that would stall scrolling. The frozen tier must
    remain still across a full second; an explicit recovered-signal injection
    then proves that no accumulated frame debt is released. Finally, clearing
    ``scrollActive`` must bypass every scroll deadline and restore one ordinary
    frame immediately.

    Explicit timestamps, injected signals, semantic tier diagnostics, and
    independent physics/render counters make the contract insensitive to the
    test machine's CPU, GPU, refresh rate, or scheduler. Changing a threshold,
    raising scroll cadence, accumulating debt, or forgetting the scroll-end
    handoff therefore breaks a precise assertion rather than a subjective
    animation check. Together with the adjacent full-density regression, the
    quality-state assertions also lock backing dimensions and render-target
    formats while this test changes tiers.
    """

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        stored_theme="dark",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        """
        () =>
          typeof window.__mutaTentaclesDebugFrameAfter === "function" &&
          typeof window.__mutaTentaclesDebugSetScrollBudgetSignals ===
            "function"
        """
    )

    def counters() -> dict[str, int]:
        return canvas.evaluate(
            """
            element => ({
              rendered: Number(element.dataset.renderedFrameId || 0),
              physics: Number(element.dataset.physicsFrameId || 0),
            })
            """
        )

    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    assert counters() == {"rendered": 1, "physics": 1}
    quality_baseline = canvas.evaluate(
        """
        element => ({
          width: element.width,
          height: element.height,
          colorTargetFormat: element.dataset.colorTargetFormat,
          geometryTargetFormat: element.dataset.geometryTargetFormat,
        })
        """
    )

    finite_tiers = [
        ("fast", 20, 7, 1000 / 30),
        ("balanced", 28, 12, 50.0),
        ("conservative", 45, 20, 1000 / 12),
    ]
    for tier, cadence_ms, frame_cost_ms, interval_ms in finite_tiers:
        page.evaluate(
            """
            signals =>
              window.__mutaTentaclesDebugSetScrollBudgetSignals(signals)
            """,
            {
                "cadenceMs": cadence_ms,
                "frameCostMs": frame_cost_ms,
            },
        )
        page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: true,
            })
            """
        )
        baseline = counters()
        page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
        assert counters() == baseline
        expect(canvas).to_have_attribute("data-scroll-animation-tier", tier)
        expect(canvas).to_have_attribute(
            "data-scroll-animation-interval-ms",
            f"{interval_ms:.1f}",
        )

        page.evaluate(
            "elapsed => window.__mutaTentaclesDebugFrameAfter(elapsed)",
            interval_ms - 1,
        )
        assert counters() == baseline
        page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
        admitted = counters()
        assert admitted == {
            "rendered": baseline["rendered"] + 1,
            "physics": baseline["physics"] + 1,
        }

        page.evaluate(
            "elapsed => window.__mutaTentaclesDebugFrameAfter(elapsed)",
            interval_ms * 5,
        )
        after_long_gap = counters()
        assert after_long_gap == {
            "rendered": admitted["rendered"] + 1,
            "physics": admitted["physics"] + 1,
        }
        page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
        assert counters() == after_long_gap

        page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: false,
            })
            """
        )
        page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
        resumed = counters()
        assert resumed == {
            "rendered": after_long_gap["rendered"] + 1,
            "physics": after_long_gap["physics"] + 1,
        }
        expect(canvas).to_have_attribute(
            "data-scroll-animation-tier",
            "inactive",
        )

    page.evaluate(
        """
        () => window.__mutaTentaclesDebugSetScrollBudgetSignals({
          cadenceMs: 45.1,
          frameCostMs: 20,
        })
        """
    )
    page.evaluate(
        """
        () => window.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: true,
        })
        """
    )
    frozen_baseline = counters()
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
    expect(canvas).to_have_attribute("data-scroll-animation-tier", "frozen")
    expect(canvas).to_have_attribute(
        "data-scroll-animation-interval-ms",
        "paused",
    )
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000)")
    assert counters() == frozen_baseline

    page.evaluate(
        """
        () => window.__mutaTentaclesDebugSetScrollBudgetSignals({
          cadenceMs: 20,
          frameCostMs: 7,
        })
        """
    )
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
    recovered = counters()
    assert recovered == {
        "rendered": frozen_baseline["rendered"] + 1,
        "physics": frozen_baseline["physics"] + 1,
    }
    expect(canvas).to_have_attribute("data-scroll-animation-tier", "fast")

    page.evaluate(
        """
        () => window.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: false,
        })
        """
    )
    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1)")
    assert counters() == {
        "rendered": recovered["rendered"] + 1,
        "physics": recovered["physics"] + 1,
    }
    assert canvas.evaluate(
        """
        element => ({
          width: element.width,
          height: element.height,
          colorTargetFormat: element.dataset.colorTargetFormat,
          geometryTargetFormat: element.dataset.geometryTargetFormat,
        })
        """
    ) == quality_baseline


def test_scroll_budget_reduces_frame_count_without_slowing_animation_time(
    driver: DriverFactory,
) -> None:
    """Throttle expensive frames without turning cadence into slow motion.

    The scroll budget intentionally admits only 30, 20, or 12 complete physics
    and WebGL frames per second. Originally the skipped RAF callbacks also
    discarded their elapsed animation time. Consequently the procedural rest
    targets advanced only on admitted frames: a nominal 20 FPS tier moved at
    roughly one third of normal speed, while 8 FPS looked practically frozen.
    Frame throttling and animation speed are separate concerns, so cheap
    skipped callbacks must advance only the autonomous animation clock while
    continuing to omit Verlet solving, texture uploads, and all render passes.
    The next admitted frame receives the bounded elapsed response time in one
    solver pass, with a time-equivalent rest response; otherwise the targets
    would move at the right rate while the visible chains still followed them
    three to eight times too slowly. The response is capped at 125 ms and never
    expands into multiple catch-up physics frames.

    For every finite tier this regression feeds sixty regular 60 Hz RAF ticks
    through the real manual-frame WebGL runtime. The animation clock must gain
    one full second (or 0.22 seconds under the existing reduced-motion policy),
    yet the independent physics and rendered counters must gain exactly 29,
    19, and 11 frames for the 33.3, 50, and 83.3 ms deadlines. The final admitted
    frame must report the corresponding bounded elapsed response. This proves
    that the fix does not secretly recover motion by running expensive hidden
    work while still preserving the visible chain response rate.
    The adjacent deadline test separately verifies that a genuine long gap is
    discarded rather than accumulated into a catch-up burst; this test models
    the ordinary case where lightweight RAF callbacks continue during scroll.

    The manual clock and exact counters make the assertion independent of wall
    time, display refresh rate, test-machine performance, and GPU scheduling.
    Removing the clock-only advancement recreates the reported slow motion;
    advancing physics or rendering on skipped ticks breaks the frame counts;
    changing the site's reduced-motion multiplier breaks the explicit policy
    calculation instead of weakening the temporal guarantee. A final 25 Hz
    callback sequence also exercises the slow end of the finite cadence range:
    its 40 ms ticks must retain their complete time rather than being truncated
    by the ordinary 33 ms physics-stability cap.
    """

    page = driver(
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        stored_theme="dark",
        load_interactive_visuals=True,
        wait_for_theme_ready=False,
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        """
        () =>
          typeof window.__mutaTentaclesDebugFrameAfter === "function" &&
          typeof window.__mutaTentaclesDebugSetScrollBudgetSignals ===
            "function" &&
          typeof window.__mutaTentaclesDebugAnimationTimeSeconds ===
            "function"
        """
    )

    def counters() -> dict[str, int]:
        return canvas.evaluate(
            """
            element => ({
              rendered: Number(element.dataset.renderedFrameId || 0),
              physics: Number(element.dataset.physicsFrameId || 0),
            })
            """
        )

    def animation_time() -> float:
        return page.evaluate(
            "() => window.__mutaTentaclesDebugAnimationTimeSeconds()"
        )

    page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    reduced_motion_multiplier = page.evaluate(
        """
        () => matchMedia("(prefers-reduced-motion: reduce)").matches
          ? 0.22
          : 1
        """
    )
    tiers = [
        ("fast", 20, 7, 29, 1000 / 30),
        ("balanced", 28, 12, 19, 50.0),
        ("conservative", 45, 20, 11, 1000 / 12),
    ]

    for (
        tier,
        cadence_ms,
        frame_cost_ms,
        expected_frames,
        expected_response_ms,
    ) in tiers:
        page.evaluate(
            """
            signals =>
              window.__mutaTentaclesDebugSetScrollBudgetSignals(signals)
            """,
            {
                "cadenceMs": cadence_ms,
                "frameCostMs": frame_cost_ms,
            },
        )
        page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: true,
            })
            """
        )
        baseline_counters = counters()
        baseline_time = animation_time()

        for _ in range(60):
            page.evaluate(
                "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
            )

        admitted_counters = counters()
        assert admitted_counters == {
            "rendered": baseline_counters["rendered"] + expected_frames,
            "physics": baseline_counters["physics"] + expected_frames,
        }
        assert animation_time() - baseline_time == pytest.approx(
            reduced_motion_multiplier,
            abs=1e-9,
        )
        assert float(
            canvas.get_attribute("data-physics-elapsed-ms") or "0"
        ) == pytest.approx(expected_response_ms, abs=0.1)
        expect(canvas).to_have_attribute("data-scroll-animation-tier", tier)

        page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: false,
            })
            """
        )
        page.evaluate(
            "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
        )

    page.evaluate(
        """
        () => window.__mutaTentaclesDebugSetScrollBudgetSignals({
          cadenceMs: 40,
          frameCostMs: 12,
        })
        """
    )
    page.evaluate(
        """
        () => window.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: true,
        })
        """
    )
    slow_cadence_baseline = counters()
    slow_cadence_time = animation_time()
    for _ in range(20):
        page.evaluate("() => window.__mutaTentaclesDebugFrameAfter(40)")
    assert animation_time() - slow_cadence_time == pytest.approx(
        0.8 * reduced_motion_multiplier,
        abs=1e-9,
    )
    assert counters() == {
        "rendered": slow_cadence_baseline["rendered"] + 6,
        "physics": slow_cadence_baseline["physics"] + 6,
    }
    assert float(
        canvas.get_attribute("data-physics-elapsed-ms") or "0"
    ) == pytest.approx(120.0, abs=0.1)
    expect(canvas).to_have_attribute(
        "data-scroll-animation-tier",
        "conservative",
    )


def test_scroll_budget_tracks_the_unthrottled_tentacle_trajectory(
    driver: DriverFactory,
) -> None:
    """Keep visible chain motion close to the original 60 FPS trajectory.

    Correct animation time alone is insufficient: a procedural rest target can
    reach the proper phase while a sparsely updated Verlet chain visibly lags
    behind it. This regression therefore compares actual point coordinates,
    not clocks or frame counters. For each 30, 20, and 12 FPS scroll tier it
    opens two independent real-WebGL runtimes with the same generation seed and
    a zero animation-time origin. One runtime advances at the ordinary 60 Hz;
    the other receives the production scroll budget and its single-pass
    temporal compensation.

    At every frame admitted by the scroll runtime, the test samples three
    joints—the first third, second third, and tip—on twelve evenly distributed
    skeletons. The corresponding reference coordinates are captured at the
    same virtual wall time. RMS trajectory error detects general shape lag;
    endpoint error catches a final phase offset; and the ratio of accumulated
    point-path lengths detects motion whose positions are plausible but whose
    visible travel is still unnaturally slow or over-amplified. Sampling only
    admitted frames intentionally measures what a user can actually see.

    The debug sampler exists only in manual-frame mode, allocates nothing in
    production, and returns raw solver coordinates before projection. Fixed
    time, seed, viewport profile, sample topology, and explicit RAF ticks make
    the comparison deterministic across CPUs and GPUs. Raising cadence without
    fixing chain response, weakening temporal compensation, adding hidden
    physics frames, changing the sampled solver topology, or introducing
    scroll-only jitter must therefore fail numerically rather than relying on a
    subjective visual judgement.

    The calibrated contract requires at least 85% and at most 110% of the
    reference visible travel, RMS error below 0.003 surface units, maximum
    sampled error below 0.005, and final offset below 0.003. Those bounds are
    tight enough to reject the former unboosted single-pass response
    (approximately 78/66/54% travel) while retaining small numerical freedom
    for the deliberately lower temporal sampling rate.

    Scroll exit has a separate velocity contract. Verlet stores displacement
    per simulation step, so the final compensated 33–83 ms displacement cannot
    be consumed unchanged by the next ordinary 16.7 ms step: that produced six
    post-scroll frames travelling 1.70×, 2.32×, and 3.58× farther than the
    continuous reference. Production rescales the incoming displacement by the
    represented frame count and applies a short 0.8 continuation damping for
    only the excess represented frames. Over the first six ordinary frames,
    total travel must stay within 0.85–1.2×; every individual step must remain
    within 0.6–1.25×. The lower bounds prevent replacing acceleration with a
    visible stop, while the upper bounds catch both the original burst and a
    subtler delayed overshoot.
    """

    def open_runtime() -> tuple[Page, Locator]:
        page = driver(
            initial_path=(
                f"/muta-tentacles.html{DIAGNOSTIC_QUERY}"
                "&animation-time-origin=0"
            ),
            stored_theme="dark",
            load_interactive_visuals=True,
            wait_for_theme_ready=False,
        )
        canvas = page.locator("#muta")
        expect(canvas).to_be_visible(timeout=30_000)
        page.wait_for_function(
            """
            () =>
              typeof window.__mutaTentaclesDebugFrameAfter === "function" &&
              typeof window.__mutaTentaclesDebugSetScrollBudgetSignals ===
                "function" &&
              typeof window.__mutaTentaclesDebugTrajectorySample ===
                "function"
            """
        )
        page.evaluate(
            "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
        )
        return page, canvas

    def sample(page: Page) -> list[float]:
        values = page.evaluate(
            "() => window.__mutaTentaclesDebugTrajectorySample()"
        )
        assert len(values) == 12 * 3 * 3
        return [float(value) for value in values]

    def point_rms(left: list[float], right: list[float]) -> float:
        assert len(left) == len(right)
        point_count = len(left) // 3
        squared_distance = 0.0
        for offset in range(0, len(left), 3):
            squared_distance += sum(
                (left[offset + axis] - right[offset + axis]) ** 2
                for axis in range(3)
            )
        return math.sqrt(squared_distance / point_count)

    def path_length(path: list[list[float]]) -> float:
        return sum(
            point_rms(previous, current)
            for previous, current in zip(path, path[1:])
        )

    tiers = [
        ("fast", 20, 7, 29),
        ("balanced", 28, 12, 19),
        ("conservative", 45, 20, 11),
    ]
    measured_metrics: dict[str, dict[str, float]] = {}

    for tier, cadence_ms, frame_cost_ms, expected_frames in tiers:
        reference_page, _ = open_runtime()
        scroll_page, scroll_canvas = open_runtime()
        scroll_page.evaluate(
            """
            signals =>
              window.__mutaTentaclesDebugSetScrollBudgetSignals(signals)
            """,
            {
                "cadenceMs": cadence_ms,
                "frameCostMs": frame_cost_ms,
            },
        )
        scroll_page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: true,
            })
            """
        )
        previous_rendered_frame = int(
            scroll_canvas.get_attribute("data-rendered-frame-id") or "0"
        )
        reference_path = [sample(reference_page)]
        scroll_path = [sample(scroll_page)]

        for _ in range(60):
            reference_page.evaluate(
                "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
            )
            scroll_page.evaluate(
                "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
            )
            rendered_frame = int(
                scroll_canvas.get_attribute("data-rendered-frame-id") or "0"
            )
            if rendered_frame == previous_rendered_frame:
                continue
            assert rendered_frame == previous_rendered_frame + 1
            previous_rendered_frame = rendered_frame
            reference_path.append(sample(reference_page))
            scroll_path.append(sample(scroll_page))

        assert len(scroll_path) == expected_frames + 1
        trajectory_errors = [
            point_rms(reference, throttled)
            for reference, throttled in zip(
                reference_path,
                scroll_path,
                strict=True,
            )
        ]
        reference_distance = path_length(reference_path)
        scroll_distance = path_length(scroll_path)
        reference_exit_path = [sample(reference_page)]
        scroll_exit_path = [sample(scroll_page)]
        scroll_page.evaluate(
            """
            () => window.__mutaTentaclesSetRuntimeState({
              visible: true,
              scrollActive: false,
            })
            """
        )
        for _ in range(6):
            reference_page.evaluate(
                "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
            )
            scroll_page.evaluate(
                "() => window.__mutaTentaclesDebugFrameAfter(1000 / 60)"
            )
            reference_exit_path.append(sample(reference_page))
            scroll_exit_path.append(sample(scroll_page))
        reference_exit_steps = [
            point_rms(previous, current)
            for previous, current in zip(
                reference_exit_path,
                reference_exit_path[1:],
            )
        ]
        scroll_exit_steps = [
            point_rms(previous, current)
            for previous, current in zip(
                scroll_exit_path,
                scroll_exit_path[1:],
            )
        ]
        measured_metrics[tier] = {
            "rms": math.sqrt(
                sum(error * error for error in trajectory_errors)
                / len(trajectory_errors)
            ),
            "maximum": max(trajectory_errors),
            "endpoint": trajectory_errors[-1],
            "path_ratio": scroll_distance / reference_distance,
            "exit_path_ratio": (
                path_length(scroll_exit_path) /
                path_length(reference_exit_path)
            ),
            "minimum_exit_step_ratio": min(
                scroll_step / reference_step
                for scroll_step, reference_step in zip(
                    scroll_exit_steps,
                    reference_exit_steps,
                    strict=True,
                )
            ),
            "maximum_exit_step_ratio": max(
                scroll_step / reference_step
                for scroll_step, reference_step in zip(
                    scroll_exit_steps,
                    reference_exit_steps,
                    strict=True,
                )
            ),
        }

    for metrics in measured_metrics.values():
        assert metrics["rms"] < 0.003
        assert metrics["maximum"] < 0.005
        assert metrics["endpoint"] < 0.003
        assert 0.85 < metrics["path_ratio"] < 1.1
        assert 0.85 < metrics["exit_path_ratio"] < 1.2
        assert metrics["minimum_exit_step_ratio"] > 0.6
        assert metrics["maximum_exit_step_ratio"] < 1.25


def test_scroll_mode_holds_the_complete_webgl_frame_between_budgeted_updates(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Prevent unbudgeted tentacle work between passive scroll updates.

    The contact surface renders a supersampled 2400-pixel-wide WebGL frame on a
    typical desktop viewport. The first scroll optimization correctly held its
    last complete texture instead of submitting new surface, scatter, and
    composite passes, but it still advanced hundreds of 50-link Verlet chains
    in the iframe. Those invisible updates consumed roughly six milliseconds
    on the same main thread every animation tick and made the parent page move
    in steps even though the canvas appeared frozen.

    This test activates that exact iframe runtime mode and records the canvas
    backing dimensions, rendered frame id, and independent physics frame id.
    Two ticks spanning only 68 ms remain below the conservative 83.3 ms startup
    budget, so both counters must remain unchanged while Chromium composites
    the existing full-quality texture. The adaptive-budget regression covers
    the eventual sparse update. Once scrolling ends, one ordinary frame must
    advance both counters at the original backing dimensions. Pointer-press
    tests cover the deliberate responsiveness exception.

    Reintroducing full-rate or "render-only" work advances one of the counters
    before its budget and fails this regression; lowering resolution changes
    the stored dimensions; and accidentally leaving the iframe throttled after
    ``scrollend`` prevents the final frame from advancing. Together those
    assertions protect scrolling without allowing a visual-quality workaround.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?manual-animation-frame=1&generation-seed=12345"
        )
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    canvas.evaluate(
        """
        element => element.ownerDocument.defaultView.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: false,
        })
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "active")
    page.evaluate(
        "() => window.__mutaTentaclesDebugFrameAfter(34)"
    )

    baseline = canvas.evaluate(
        """
        element => ({
          width: element.width,
          height: element.height,
          rendered: Number(element.dataset.renderedFrameId),
          physics: Number(element.dataset.physicsFrameId),
        })
        """
    )
    canvas.evaluate(
        """
        element => element.ownerDocument.defaultView.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: true,
        })
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    page.evaluate(
        """
        () => {
          window.__mutaTentaclesDebugFrameAfter(34);
          window.__mutaTentaclesDebugFrameAfter(34);
        }
        """
    )
    during_scroll = canvas.evaluate(
        """
        element => ({
          width: element.width,
          height: element.height,
          rendered: Number(element.dataset.renderedFrameId),
          physics: Number(element.dataset.physicsFrameId),
        })
        """
    )

    assert during_scroll["width"] == baseline["width"]
    assert during_scroll["height"] == baseline["height"]
    assert during_scroll["rendered"] == baseline["rendered"]
    assert during_scroll["physics"] == baseline["physics"]

    canvas.evaluate(
        """
        element => element.ownerDocument.defaultView.__mutaTentaclesSetRuntimeState({
          visible: true,
          scrollActive: false,
        })
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "active")
    page.evaluate(
        "() => window.__mutaTentaclesDebugFrameAfter(34)"
    )
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) > during_scroll["rendered"]
    assert int(
        canvas.get_attribute("data-physics-frame-id") or "0"
    ) > during_scroll["physics"]
    resumed = canvas.evaluate(
        "element => ({width: element.width, height: element.height})"
    )
    assert resumed == {
        "width": baseline["width"],
        "height": baseline["height"],
    }


def test_contact_surface_waits_for_idle_before_exposing_its_iframe_source(
    driver: DriverFactory,
) -> None:
    """Promote Contact Tentacles only when the browser runs its idle callback.

    The runtime URL starts in ``data-prewarm-src`` while the real ``src`` is
    absent, so the browser cannot request WebGL work during critical rendering.
    Holding ``requestIdleCallback`` proves that initialization keeps it there.
    Releasing that exact callback must move the URL to ``src`` with eager
    loading, and its two-second deadline prevents an idle callback from being
    postponed indefinitely on a busy page.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=False,
        init_scripts=(
            """
            (() => {
              if (window !== window.top) return;
              window.__mutaPrewarmIdleCalls = [];
              window.requestIdleCallback = (callback, options) => {
                window.__mutaPrewarmIdleCalls.push({callback, options});
                return window.__mutaPrewarmIdleCalls.length;
              };
            })();
            """,
        ),
    )
    frame = page.locator(".contact__tentacles")

    page.wait_for_function(
        "window.__mutaPrewarmIdleCalls.length === 1",
    )
    prewarm_source = frame.get_attribute("data-prewarm-src")
    assert prewarm_source is not None
    assert frame.get_attribute("src") is None
    assert page.evaluate(
        "window.__mutaPrewarmIdleCalls[0].options.timeout",
    ) == 2000

    page.evaluate(
        "window.__mutaPrewarmIdleCalls.shift().callback()",
    )

    expect(frame).to_have_attribute("loading", "eager")
    expect(frame).to_have_attribute("src", prewarm_source)
    expect(frame).not_to_have_attribute("data-prewarm-src", re.compile(".+"))
    assert page.evaluate("window.__mutaPrewarmIdleCalls.length") == 0


def test_contact_surface_uses_zero_delay_prewarm_without_idle_callback(
    driver: DriverFactory,
) -> None:
    """Use a controlled zero-delay timer when requestIdleCallback is absent.

    Browsers without the Idle Callback API must still move expensive WebGL
    startup away from parsing and the first render. The harness captures only
    zero-delay timers in the top document, leaving every unrelated timer real.
    Until the captured fallback runs the iframe has no requestable ``src``;
    invoking it must move the authored URL into an eager iframe without
    scrolling the page.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=False,
        init_scripts=(
            """
            (() => {
              if (window !== window.top) return;
              delete window.requestIdleCallback;
              const nativeSetTimeout = window.setTimeout.bind(window);
              window.__mutaPrewarmTimerCalls = [];
              window.setTimeout = (callback, delay, ...args) => {
                if (delay === 0) {
                  window.__mutaPrewarmTimerCalls.push({callback, delay, args});
                  return 1000 + window.__mutaPrewarmTimerCalls.length;
                }
                return nativeSetTimeout(callback, delay, ...args);
              };
            })();
            """,
        ),
    )
    frame = page.locator(".contact__tentacles")

    page.wait_for_function(
        "window.__mutaPrewarmTimerCalls.length === 1",
    )
    prewarm_source = frame.get_attribute("data-prewarm-src")
    assert prewarm_source is not None
    assert frame.get_attribute("src") is None
    assert page.evaluate(
        "window.__mutaPrewarmTimerCalls[0].delay",
    ) == 0

    page.evaluate(
        """
        () => {
          const timer = window.__mutaPrewarmTimerCalls.shift();
          timer.callback(...timer.args);
        }
        """
    )

    expect(frame).to_have_attribute("loading", "eager")
    expect(frame).to_have_attribute("src", prewarm_source)
    expect(frame).not_to_have_attribute("data-prewarm-src", re.compile(".+"))
    assert page.evaluate("window.__mutaPrewarmTimerCalls.length") == 0


def test_contact_surface_prewarm_does_not_wait_when_contact_becomes_visible(
    driver: DriverFactory,
) -> None:
    """Start prewarming immediately if Contact arrives before the idle phase.

    A fast scroll can bring the unrequested iframe near the viewport while its
    idle callback is still queued. Holding that callback and scrolling the real
    section into view proves the IntersectionObserver path exposes its source
    immediately. Running the stale callback afterward must be harmless, so the
    runtime is never reloaded or reinitialized.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=False,
        init_scripts=(
            """
            (() => {
              if (window !== window.top) return;
              window.__mutaPrewarmIdleCalls = [];
              window.requestIdleCallback = (callback, options) => {
                window.__mutaPrewarmIdleCalls.push({callback, options});
                return window.__mutaPrewarmIdleCalls.length;
              };
            })();
            """,
        ),
    )
    frame = page.locator(".contact__tentacles")

    page.wait_for_function(
        "window.__mutaPrewarmIdleCalls.length === 1",
    )
    prewarm_source = frame.get_attribute("data-prewarm-src")
    assert prewarm_source is not None
    assert frame.get_attribute("src") is None

    frame.evaluate(
        "element => element.scrollIntoView({block: 'center', behavior: 'instant'})"
    )

    expect(frame).to_have_attribute("loading", "eager")
    expect(frame).to_have_attribute("src", prewarm_source)
    expect(frame).not_to_have_attribute("data-prewarm-src", re.compile(".+"))
    assert page.evaluate("window.__mutaPrewarmIdleCalls.length") == 1
    page.evaluate(
        "window.__mutaPrewarmIdleCalls.shift().callback()",
    )
    expect(frame).to_have_attribute("loading", "eager")


def test_contact_surface_prewarms_before_the_user_reaches_the_footer(
    driver: DriverFactory,
) -> None:
    """Keep expensive WebGL startup out of the contact-section scroll.

    The tentacle iframe sits several screens below the initial viewport. With
    native lazy loading, Chromium postponed downloading the document, creating
    its WebGL context, compiling all shader programs, generating the noise
    field, and building hundreds of tentacle chains until the user was already
    scrolling toward the footer. That one-time burst occurred in precisely the
    area reported as jerky, independently of the steady-state FPS improvements.

    This integration regression remains at ``scrollY == 0`` and requires a
    real WebGL iframe canvas to initialize while the contact section is still
    far below the viewport. It uses the suite's bounded rendering profile so
    Linux software rendering cannot obscure the prewarm protocol being tested;
    separate standalone regressions exercise full production density. The
    runtime must then be paused by the existing visibility protocol, so
    prewarming consumes no ongoing animation work.
    Leaving the runtime URL permanently in ``data-prewarm-src`` makes the
    canvas absent and fails; forgetting the visibility handoff leaves the
    offscreen simulation running and also fails. The test therefore moves only
    startup cost away from scrolling without trading it for permanent
    background rendering.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
        bounded_tentacle_rendering=True,
    )
    assert page.evaluate("() => window.scrollY") == 0
    assert page.locator("#contact").evaluate(
        "element => element.getBoundingClientRect().top"
    ) > 5 * 720

    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    expect(canvas).to_have_attribute(
        "data-runtime-visible",
        "false",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-runtime-paused",
        "true",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute("data-animation-loop", "paused")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "false",
    )


def test_contact_runtime_stays_paused_inside_only_the_prewarm_margin(
    driver: DriverFactory,
) -> None:
    """Prewarm at 200 px, but run RAF only after a real viewport intersection."""

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
        bounded_tentacle_rendering=True,
    )
    frame = page.locator(".contact__tentacles")
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    expect(canvas).to_be_attached(timeout=30_000)

    scroll_positions = frame.evaluate(
        """
        (element) => {
          const documentTop =
            element.getBoundingClientRect().top + scrollY;
          return {
            prewarmOnly: documentTop - innerHeight - 100,
            onePixelVisible: documentTop - innerHeight + 1,
          };
        }
        """
    )
    page.evaluate(
        "position => scrollTo({top: position, behavior: 'instant'})",
        scroll_positions["prewarmOnly"],
    )
    frame_top = frame.evaluate("element => element.getBoundingClientRect().top")
    assert 720 <= frame_top < 920
    expect(canvas).to_have_attribute("data-runtime-visible", "false")
    expect(canvas).to_have_attribute("data-animation-loop", "paused")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "false",
    )

    page.evaluate(
        "position => scrollTo({top: position, behavior: 'instant'})",
        scroll_positions["onePixelVisible"],
    )
    expect(canvas).to_have_attribute("data-runtime-visible", "true")
    expect(canvas).to_have_attribute("data-animation-loop", "running")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "true",
    )
    page.wait_for_function(
        """
        () => Number(
          document.querySelector(".contact__tentacles")
            ?.contentWindow?.document?.querySelector("#muta")
            ?.dataset?.physicsFrameId || 0
        ) > 2
        """,
        timeout=30_000,
    )
    page.evaluate(
        "position => scrollTo({top: position, behavior: 'instant'})",
        scroll_positions["prewarmOnly"],
    )
    expect(canvas).to_have_attribute("data-runtime-visible", "false")
    expect(canvas).to_have_attribute("data-animation-loop", "paused")
    expect(canvas).to_have_attribute(
        "data-animation-frame-scheduled",
        "false",
    )
    paused_physics_frame = int(
        canvas.get_attribute("data-physics-frame-id") or "0"
    )
    page.wait_for_timeout(250)
    assert int(
        canvas.get_attribute("data-physics-frame-id") or "0"
    ) == paused_physics_frame


def _open_contact_with_active_tentacle_runtime(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> tuple[Page, Locator]:
    """Return the active manual-frame canvas after its aspect document is ready.

    The embedded runtime intentionally reloads itself when its first measured
    rectangle differs from the aspect used to allocate the WebGL simulation.
    A visible canvas can therefore belong to a short-lived document whose
    replacement has already been scheduled. Waiting for ``aspectReady`` makes
    callers synchronize with the runtime's geometry decision instead of an
    arbitrary quiet delay. This is especially important before Playwright's
    virtual clock is installed: freezing timers while that zero-delay reload is
    pending can strand a frame locator between the old and new documents.

    These callers test the scroll protocol and its production timers, not the
    renderer's density or frame cadence. The helper therefore loads the real
    iframe with the suite's bounded rendering profile and adds its existing
    manual-frame diagnostic flag before bringing the offscreen iframe into
    view. Real wheel events, postMessage traffic, and setTimeout deadlines
    continue to run, while unrelated continuous WebGL frames cannot dominate
    the deterministic timer assertions. Separate tests keep full production
    density, the real frame path, and physics-counter lifecycle covered.
    """

    page = driver(
        stored_theme="dark",
        viewport=viewport,
        load_interactive_visuals=True,
        bounded_tentacle_rendering=True,
    )
    frame = page.locator(".contact__tentacles")
    expect(frame).to_be_attached(timeout=30_000)
    _wait_for_bounded_contact_tentacles_source(frame)
    frame.evaluate(
        """
        (element) => {
          const source = new URL(element.src);
          source.searchParams.set("manual-animation-frame", "1");
          element.src = source.href;
        }
        """
    )
    frame.evaluate(
        "element => element.scrollIntoView({block: 'center', behavior: 'instant'})"
    )
    page.wait_for_function(
        """
        (selector) => {
          const child = document.querySelector(selector)?.contentWindow;
          const canvas = child?.document?.querySelector("#muta");
          if (!child || !canvas) return false;
          const source = new URL(child.location.href);
          return source.searchParams.get("manual-animation-frame") === "1" &&
            typeof child.__mutaTentaclesDebugFrameAfter === "function" &&
            canvas.dataset.aspectReady === "true" &&
            canvas.dataset.aspectReloading === "false";
        }
        """,
        arg=".contact__tentacles",
        timeout=30_000,
    )
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    expect(canvas).to_be_visible()
    expect(canvas).to_have_attribute(
        "data-aspect-ready",
        "true",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-aspect-reloading",
        "false",
        timeout=30_000,
    )
    frame.evaluate(
        "element => element.scrollIntoView({block: 'center', behavior: 'instant'})"
    )
    expect(frame).to_be_in_viewport()
    expect(canvas).to_have_attribute(
        "data-runtime-visible",
        "true",
        timeout=30_000,
    )
    page.evaluate(
        "() => window.dispatchEvent(new Event('scrollend'))"
    )
    expect(canvas).to_have_attribute(
        "data-scroll-rendering",
        "active",
        timeout=30_000,
    )
    page.clock.install()
    page.clock.pause_at(int(page.evaluate("() => Date.now()")) + 1)
    return page, canvas


def _settle_page_at_bottom(page: Page, canvas: Locator) -> Locator:
    """Move to the lower boundary and settle the native scroll lifecycle."""

    frame = page.locator(".contact__tentacles")
    page.evaluate(
        """
        async () => {
          const root = document.scrollingElement;
          const maximumScrollTop =
            root.scrollHeight - root.clientHeight;
          const waitForScrollEnd = () => new Promise((resolve) => {
            window.addEventListener("scrollend", resolve, {once: true});
          });
          const retreatScrollTop = Math.max(0, maximumScrollTop - 2);

          if (Math.abs(retreatScrollTop - root.scrollTop) > 0.5) {
            const retreatFinished = waitForScrollEnd();
            window.scrollTo({
              top: retreatScrollTop,
              behavior: "instant",
            });
            await retreatFinished;
          }

          const boundaryFinished = waitForScrollEnd();
          window.scrollTo({
            top: maximumScrollTop,
            behavior: "instant",
          });
          await boundaryFinished;
        }
        """
    )
    assert page.evaluate(
        """
        () => {
          const root = document.scrollingElement;
          return Math.abs(
            root.scrollHeight - root.clientHeight - root.scrollTop
          ) <= 1;
        }
        """
    )
    expect(frame).to_have_attribute("data-scroll-active", "false")
    expect(canvas).to_have_attribute("data-runtime-visible", "true")
    expect(canvas).to_have_attribute("data-scroll-rendering", "active")
    return frame


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 610),
    ],
)
def test_contact_scroll_quiet_period_has_exact_deterministic_boundaries(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep adaptive scroll mode active for the complete quiet period.

    The bottom-of-page hitch was caused by the full-density WebGL simulation
    returning to its full-rate loop between two events from one momentum-scroll
    gesture. The host now treats 320 ms without another scroll event as a
    fallback end boundary while native ``scrollend`` remains the preferred
    signal. While this state is marked ``suppressed``, the child may submit only
    the sparse frames admitted by its independent adaptive budget; the marker
    no longer means that every physics/render update is forbidden. This
    regression opens the real contact iframe at desktop and phone widths,
    freezes browser time, dispatches the same top-level event as physical page
    movement, and proves that budget mode begins immediately, remains active at
    exactly 319 ms, and returns to ordinary animation at exactly 320 ms.

    No wall-clock sleep participates in the assertions: Playwright advances the
    page's production ``setTimeout`` deterministically. Restoring the former
    120 ms delay, rounding the boundary down, failing to start budget mode, or
    leaving the renderer throttled forever therefore fails identically on a
    fast workstation and a slow CI worker. The two viewport cases protect the
    responsive layout where the intermittent regression was most noticeable.
    """

    page, canvas = _open_contact_with_active_tentacle_runtime(
        driver,
        viewport,
    )
    baseline_suppressions = int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    )

    page.evaluate(
        "() => window.dispatchEvent(new Event('scroll'))"
    )
    page.wait_for_function(
        """
        ([frameSelector, baseline]) => {
          const frame = document.querySelector(frameSelector);
          const canvas = frame?.contentWindow?.document.querySelector("#muta");
          return Number(canvas?.dataset.scrollSuppressionCount || 0) >
            baseline;
        }
        """,
        arg=[".contact__tentacles", baseline_suppressions],
    )
    page.clock.fast_forward(319)
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"
    page.clock.fast_forward(1)
    assert canvas.get_attribute("data-scroll-rendering") == "active"


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 610),
    ],
)
def test_each_scroll_event_restarts_the_complete_quiet_period(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Prevent a stale timer from waking WebGL during momentum scrolling.

    A trackpad or touch gesture emits a burst of scroll events rather than one
    continuous event. The fallback deadline must be measured from the newest
    event in that burst. If the host forgets to cancel the earlier timer, the
    tentacle renderer returns to unrestricted animation 320 ms after the first
    event while the page is still moving. Adaptive scroll frames are bounded;
    a mistakenly restored 60 FPS loop is not, so the intermittent stutter can
    return even though the simple single-event boundary test still passes.

    This scenario freezes time on the real desktop and mobile layouts, begins a
    scroll, advances 240 ms, then emits a second scroll event. It requires the
    iframe to remain in budget mode through 319 ms measured from that second
    event and to resume full-rate animation only on its following millisecond.
    Because no real waiting or measured FPS is involved, the test isolates
    timer replacement itself and fails reliably if ``clearTimeout`` is removed,
    the old handle survives, or the replacement deadline is calculated from
    the start of the gesture.
    """

    page, canvas = _open_contact_with_active_tentacle_runtime(
        driver,
        viewport,
    )
    page.evaluate(
        "() => window.dispatchEvent(new Event('scroll'))"
    )
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"

    page.clock.fast_forward(240)
    page.evaluate(
        "() => window.dispatchEvent(new Event('scroll'))"
    )
    page.clock.fast_forward(80)
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"
    page.clock.fast_forward(239)
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"
    page.clock.fast_forward(1)
    assert canvas.get_attribute("data-scroll-rendering") == "active"


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 610),
    ],
)
def test_scrollend_and_iframe_wheel_share_the_scroll_lifecycle(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Cover both native completion and wheel input over the WebGL iframe.

    When the cursor is outside the contact iframe, the parent receives normal
    page ``scroll`` and ``scrollend`` events. When the cursor is over the
    iframe, its own document sees the wheel first and must notify the parent by
    ``postMessage``. A regression in either half can leave the high-cost
    simulation unrestricted during the gesture or throttled after it. This test
    first proves that native ``scrollend`` resumes ordinary rendering
    immediately without advancing the 320 ms fallback clock. It then dispatches
    a wheel event on the real child canvas and requires the same immediate entry
    into adaptive budget mode, the same 319 ms protected interval, and the same
    fallback release at 320 ms. Both halves also inspect the parent iframe's
    ``data-scroll-active`` state. That
    parent-side assertion is essential: the child has its own defensive idle
    timer, so observing only the canvas could pass even if its ``postMessage``
    relay had silently stopped reaching the page.

    Desktop and narrow layouts are both exercised because iframe geometry and
    event routing change responsively. The test observes explicit runtime state
    rather than frame duration: CPU speed, GPU availability, CI load, and
    display refresh rate cannot make it flaky. Removing the child forwarding
    listener, changing the message contract, ignoring ``scrollend``, or giving
    wheel input a shorter quiet period each breaks a distinct assertion.
    """

    page, canvas = _open_contact_with_active_tentacle_runtime(
        driver,
        viewport,
    )
    frame = page.locator(".contact__tentacles")
    page.evaluate(
        "() => window.dispatchEvent(new Event('scroll'))"
    )
    expect(frame).to_have_attribute("data-scroll-active", "true")
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"
    page.evaluate(
        "() => window.dispatchEvent(new Event('scrollend'))"
    )
    expect(frame).to_have_attribute("data-scroll-active", "false")
    assert canvas.get_attribute("data-scroll-rendering") == "active"

    wheel_baseline = int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    )
    canvas.evaluate(
        """
        element => element.dispatchEvent(new WheelEvent("wheel", {
          bubbles: true,
          deltaY: 120,
        }))
        """
    )
    expect(frame).to_have_attribute("data-scroll-active", "true")
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) > wheel_baseline
    page.clock.fast_forward(319)
    assert frame.get_attribute("data-scroll-active") == "true"
    assert canvas.get_attribute("data-scroll-rendering") == "suppressed"
    page.clock.fast_forward(1)
    assert frame.get_attribute("data-scroll-active") == "false"
    assert canvas.get_attribute("data-scroll-rendering") == "active"


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 610),
    ],
)
def test_outward_momentum_at_page_bottom_never_pauses_tentacles(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the contact animation alive after the page reaches its lower edge.

    A trackpad continues emitting positive wheel deltas for a while after the
    document has already reached its maximum scroll position. Those events
    arrive inside the contact iframe even though they cannot move the parent
    page. Treating every one as active scrolling repeatedly restarted the
    320 ms render-suppression timer, so a long momentum tail froze the
    tentacles for roughly two seconds at the exact moment the user arrived at
    the bottom.

    This regression loads the real WebGL surface on desktop and phone layouts,
    moves the document to its exact lower boundary, and sends sixteen outward
    wheel events over 2.4 seconds of deterministic browser time. No event may
    increment the suppression counter or enter adaptive budget mode. It then
    sends one negative delta, which can genuinely move the page upward, and
    requires the compositor-friendly budget mode to start. The final assertion
    is essential: the boundary fix must remove only the false throttle and
    preserve the existing smooth-scroll optimization whenever movement is
    possible.

    Playwright's frozen clock replaces all real waiting, so display refresh
    rate, WebGL speed, CPU load, and CI scheduling cannot hide or fabricate the
    defect.
    """

    page, canvas = _open_contact_with_active_tentacle_runtime(
        driver,
        viewport,
    )
    _settle_page_at_bottom(page, canvas)
    baseline_suppressions = int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    )

    for _ in range(16):
        canvas.evaluate(
            """
            element => element.dispatchEvent(new WheelEvent("wheel", {
              bubbles: true,
              deltaY: 120,
            }))
            """
        )
        page.clock.fast_forward(150)
        assert canvas.get_attribute("data-scroll-rendering") == "active"

    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions

    canvas.evaluate(
        """
        element => element.dispatchEvent(new WheelEvent("wheel", {
          bubbles: true,
          deltaY: -120,
        }))
        """
    )
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions + 1


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 610),
    ],
)
def test_bottom_boundary_input_cancels_an_inflight_scroll_pause(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """End adaptive scroll throttling as soon as movement becomes impossible.

    The last real scroll event can put the iframe into suppression immediately
    before the document reaches its lower limit. If the following momentum
    wheel event is merely ignored, the already-running parent and child timers
    keep the iframe in reduced-cadence budget mode for another 320 ms. Worse, a
    stale parent state can reapply throttling after the child has locally
    resumed. Historically this held one texture completely still; with adaptive
    animation it would still make the bottom unnecessarily less responsive.

    At both responsive widths this test starts from the exact page bottom and
    emits an inward iframe wheel delta. Synthetic wheel events do not move the
    document, but this one can scroll upward and therefore deterministically
    places the real child-to-parent protocol into active-scroll mode without
    racing a browser-generated ``scrollend``. It then emits an outward delta
    that cannot change ``scrollTop``. The parent state and child renderer must
    both return to ordinary mode immediately. Advancing the fake clock through
    the discarded deadline must not produce a late state reversal. This catches
    incomplete fixes that filter only new throttles, introduce another budget
    transition at the discarded deadline, or update the iframe without
    synchronizing the host lifecycle.
    """

    page, canvas = _open_contact_with_active_tentacle_runtime(
        driver,
        viewport,
    )
    frame = _settle_page_at_bottom(page, canvas)
    baseline_suppressions = int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    )
    canvas.evaluate(
        """
        element => element.dispatchEvent(new WheelEvent("wheel", {
          bubbles: true,
          deltaY: -120,
        }))
        """
    )
    expect(frame).to_have_attribute("data-scroll-active", "true")
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions + 1

    canvas.evaluate(
        """
        element => element.dispatchEvent(new WheelEvent("wheel", {
          bubbles: true,
          deltaY: 120,
        }))
        """
    )
    expect(frame).to_have_attribute("data-scroll-active", "false")
    expect(canvas).to_have_attribute("data-scroll-rendering", "active")
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions + 1

    page.clock.fast_forward(320)
    assert frame.get_attribute("data-scroll-active") == "false"
    assert canvas.get_attribute("data-scroll-rendering") == "active"
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions + 1
    page.clock.fast_forward(320)
    assert frame.get_attribute("data-scroll-active") == "false"
    assert canvas.get_attribute("data-scroll-rendering") == "active"
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) == baseline_suppressions + 1


@pytest.mark.parametrize(
    ("button", "direction", "held_multiplier"),
    [
        ("left", "1", "2.000"),
        ("right", "-1", "2.000"),
    ],
)
def test_click_impulse_is_applied_to_exactly_one_physics_frame(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
    held_multiplier: str,
) -> None:
    """Prevent the short click peak from leaking into a second physics frame.

    The source interaction describes a roughly 41.5 ms force envelope, but the
    rectangular production surface contains substantially more tentacles than
    the original square. Reapplying its 13.333× peak on two consecutive
    animation frames injects the strong force twice; the resulting deformation
    survives much longer than the nominal clock and is the user-visible defect
    reported repeatedly.

    This regression freezes normal animation, presses either mouse button, and
    verifies that ``pointerdown`` renders nothing and that the next animation
    frame is the sole peak-force physics application. It then advances another
    frame by only nine milliseconds—well inside the old envelope, where the bug
    definitely reproduced—and demands ordinary held force with the cumulative
    impulse-application counter still equal to one. A duration-only or 100 ms
    sample would miss this exact double-kick.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")

    expect(canvas).to_be_visible(timeout=30_000)
    expect(canvas).to_have_attribute(
        "data-press-impulse-total-ms",
        "41.5",
    )

    page.mouse.move(120, 90)
    baseline_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    page.mouse.down(button=button)

    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == baseline_frame
    expect(canvas).to_have_attribute(
        "data-immediate-impulse-frame",
        "disabled",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-impulse-frame-scheduling",
        "animation-frame",
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    expect(canvas).to_have_attribute(
        "data-impulse-physics-applications",
        "1",
    )
    page.evaluate(
        """
        () => {
          const canvas = document.querySelector("#muta");
          canvas.dataset.animationImpulseFramesSincePress = "0";
          window.__mutaTentaclesDebugFrameAfter(9);
        }
        """
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-animation-impulse-frames-since-press",
        "0",
    )
    expect(canvas).to_have_attribute(
        "data-impulse-physics-applications",
        "1",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        held_multiplier,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        direction,
    )

    page.mouse.up(button=button)


@pytest.mark.parametrize(
    ("button", "direction"),
    [("left", "1"), ("right", "-1")],
)
def test_release_before_the_next_raf_does_not_drop_the_queued_impulse(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
) -> None:
    """Keep one complete impulse even when down/up fit between two RAFs."""

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.mouse.move(120, 90)
    baseline_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )

    page.mouse.down(button=button)
    page.mouse.up(button=button)
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == baseline_frame

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    queued_impulse = canvas.evaluate(
        """
        element => ({
          phase: element.dataset.renderedInteractionPhase,
          pressed: element.dataset.renderedPointerPressed,
          direction: element.dataset.lastImpulseDirection,
          applications: element.dataset.impulsePhysicsApplications,
          submissionMs: Number(element.dataset.inputToImpulseSubmissionMs),
        })
        """
    )
    assert queued_impulse["phase"] == "impulse"
    assert queued_impulse["pressed"] == "false"
    assert queued_impulse["direction"] == direction
    assert queued_impulse["applications"] == "1"
    assert math.isfinite(queued_impulse["submissionMs"])

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    expect(canvas).to_have_attribute(
        "data-rendered-interaction-phase",
        "hover",
    )
    expect(canvas).to_have_attribute(
        "data-impulse-physics-applications",
        "1",
    )


@pytest.mark.parametrize(
    ("button", "direction", "impulse_multiplier", "held_multiplier"),
    [
        ("left", "1", "13.333", "2.000"),
        ("right", "-1", "13.333", "2.000"),
    ],
)
def test_mouse_impulse_transitions_to_the_correct_held_force(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
    impulse_multiplier: str,
    held_multiplier: str,
) -> None:
    """Keep the original impulse and double only held right-button repulsion.

    Every press must first submit a short, wide impulse and then settle into its
    held mode. Both buttons retain the original 13.333× impulse. The left button
    continues with 2× attraction, while only the right button's held repulsion
    is strengthened from 1× to 2×. Releasing either button must submit an
    ordinary hover frame immediately, without adding a zero-force recovery kick
    or changing the shared timing and radius.

    Rendering density is reduced only for headless test speed; the production
    pointer and physics paths under test are unchanged.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")

    expect(canvas).to_be_visible(timeout=30_000)
    page.mouse.move(120, 90)
    page.mouse.down(button=button)
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")

    expect(canvas).to_have_attribute(
        "data-last-impulse-peak-force-multiplier",
        impulse_multiplier,
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-last-impulse-direction",
        direction,
        timeout=1_000,
    )
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(50)")
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        held_multiplier,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-radius-multiplier",
        "1.000",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        direction,
    )

    page.mouse.up(button=button)

    released_state = canvas.evaluate(
        """
        element => ({
          phase: element.dataset.renderedInteractionPhase,
          pressed: element.dataset.renderedPointerPressed,
          force: Number(
            element.dataset.pointerInteractionForceMultiplier,
          ),
        })
        """
    )
    assert released_state["phase"] == "hover"
    assert released_state["pressed"] == "false"
    assert released_state["force"] == 1
    expect(canvas).to_have_attribute(
        "data-rendered-interaction-phase",
        "hover",
        timeout=300,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        "1.000",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        "0",
    )


@pytest.mark.parametrize("early_exit", [True, False])
def test_solver_uses_conservative_adaptive_iteration_limits(
    driver: DriverFactory,
    base_url: str,
    early_exit: bool,
) -> None:
    """Expose adaptive work while retaining full grabbed-tip convergence."""

    disabled_query = "" if early_exit else "&solver-early-exit=0"
    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(640, 360),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=128&render-supersample=0.5"
            "&manual-animation-frame=1&interaction-diagnostics=1"
            "&generation-seed=12345&debug-targets=1"
            "&debug-x=0.58&debug-y=0.52"
            f"{disabled_query}"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugPhysicsSnapshot === 'function'"
    )
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 60; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """
    )
    page.mouse.move(640 * 0.58, 360 * 0.52)
    page.mouse.down(button="left")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 90; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """
    )
    solver = page.evaluate(
        "window.__mutaTentaclesDebugPhysicsSnapshot().solver"
    )
    page.mouse.up(button="left")

    assert solver["pbdChains"] == 128
    assert solver["fabrikGrabbedSolves"] == 4
    assert solver["fabrikGrabbedIterations"] == 4 * 20
    assert solver["fabrikOrdinarySolves"] > 0
    if early_exit:
        expect(canvas).to_have_attribute(
            "data-solver-error-tolerance-pixels",
            "0.003",
        )
        assert 4 * 128 <= solver["pbdIterations"] < 8 * 128
        assert solver["pbdEarlyExits"] > 0
        assert (
            2 * solver["fabrikOrdinarySolves"]
            <= solver["fabrikOrdinaryIterations"]
            < 7 * solver["fabrikOrdinarySolves"]
        )
        assert solver["fabrikEarlyExits"] > 0
    else:
        assert solver["pbdIterations"] == 8 * 128
        assert solver["pbdEarlyExits"] == 0
        assert (
            solver["fabrikOrdinaryIterations"]
            == 7 * solver["fabrikOrdinarySolves"]
        )
        assert solver["fabrikEarlyExits"] == 0


def test_stationary_grabbed_tips_do_not_jitter_between_frames(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Bound high-frequency tip motion before adapting solver iterations.

    The cursor remains stationary while four deterministic tentacles stay
    grabbed. Their procedural rest targets may move smoothly, so this contract
    measures first and second finite differences rather than demanding a
    frozen image. Link-error bounds independently prevent a quiet but stretched
    chain from passing the trajectory checks.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(640, 360),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=128&render-supersample=0.5"
            "&manual-animation-frame=1&interaction-diagnostics=1"
            "&generation-seed=12345&debug-targets=1"
            "&debug-x=0.58&debug-y=0.52"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugPhysicsSnapshot === 'function'"
    )
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 60; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """
    )
    page.mouse.move(640 * 0.58, 360 * 0.52)
    page.mouse.down(button="left")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 90; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """
    )

    metrics = page.evaluate(
        """
        () => {
          const sampleCount = 180;
          let previousTips = null;
          const previousVelocities = new Map();
          const grabbedIds = new Set();
          let squaredStep = 0;
          let squaredAcceleration = 0;
          let stepSamples = 0;
          let accelerationSamples = 0;
          let maximumStep = 0;
          let maximumAcceleration = 0;
          let reversals = 0;
          let maximumLinkError = 0;
          let maximumRmsLinkError = 0;

          for (let frame = 0; frame < sampleCount; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
            const snapshot =
              window.__mutaTentaclesDebugPhysicsSnapshot();
            maximumLinkError = Math.max(
              maximumLinkError,
              snapshot.maximumLinkErrorPixels,
            );
            maximumRmsLinkError = Math.max(
              maximumRmsLinkError,
              snapshot.rmsLinkErrorPixels,
            );
            const currentTips = new Map();
            for (const tip of snapshot.tips) {
              if (!tip.grabbed) continue;
              grabbedIds.add(tip.index);
              currentTips.set(tip.index, tip);
              const previousTip = previousTips?.get(tip.index);
              if (!previousTip) continue;
              const velocityX = tip.x - previousTip.x;
              const velocityY = tip.y - previousTip.y;
              const step = Math.hypot(velocityX, velocityY);
              squaredStep += step * step;
              stepSamples += 1;
              maximumStep = Math.max(maximumStep, step);

              const previousVelocity =
                previousVelocities.get(tip.index);
              if (previousVelocity) {
                const acceleration = Math.hypot(
                  velocityX - previousVelocity.x,
                  velocityY - previousVelocity.y,
                );
                squaredAcceleration += acceleration * acceleration;
                accelerationSamples += 1;
                maximumAcceleration = Math.max(
                  maximumAcceleration,
                  acceleration,
                );
                if (
                  step > 0.002 &&
                  previousVelocity.length > 0.002 &&
                  velocityX * previousVelocity.x +
                    velocityY * previousVelocity.y < 0
                ) {
                  reversals += 1;
                }
              }
              previousVelocities.set(tip.index, {
                x: velocityX,
                y: velocityY,
                length: step,
              });
            }
            previousTips = currentTips;
          }

          return {
            grabbedIds: [...grabbedIds],
            stepSamples,
            rmsStep: Math.sqrt(squaredStep / stepSamples),
            maximumStep,
            rmsAcceleration: Math.sqrt(
              squaredAcceleration / accelerationSamples,
            ),
            maximumAcceleration,
            reversals,
            maximumLinkError,
            maximumRmsLinkError,
          };
        }
        """
    )
    page.mouse.up(button="left")

    assert metrics["grabbedIds"] == [36, 86, 87, 106]
    assert metrics["stepSamples"] == 4 * 179
    assert metrics["reversals"] == 0
    assert metrics["rmsStep"] < 0.001
    assert metrics["maximumStep"] < 0.002
    assert metrics["rmsAcceleration"] < 0.00002
    assert metrics["maximumAcceleration"] < 0.00005
    assert metrics["maximumLinkError"] < 1
    assert metrics["maximumRmsLinkError"] < 0.03


def _open_diagnostic_surface(
    page: Page,
    base_url: str,
    *,
    path: str = "muta-tentacles.html",
    warmup_frames: int = 8,
    navigate: bool = True,
) -> Page:
    """Open a deterministic manual surface and optionally warm its geometry.

    Geometry and release contracts keep the original eight-frame warm-up.
    Pure force-envelope contracts call a source-derived debug function that is
    initialized at DOM load and does not read rendered or physics state, so
    they explicitly request zero frames instead of paying for unrelated WebGL
    work. They may also ask the shared driver to open this standalone document
    directly and disable only this helper's redundant navigation. Keeping both
    existing behaviors as defaults makes those optimizations explicit opt-ins.
    """

    assert warmup_frames >= 0

    if navigate:
        page.goto(
            f"{base_url}/{path}{DIAGNOSTIC_QUERY}",
            wait_until="domcontentloaded",
        )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugInteractionAt === 'function'"
    )
    page.evaluate(
        """
        (warmupFrames) => {
          for (let frame = 0; frame < warmupFrames; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(16);
          }
        }
        """,
        warmup_frames,
    )
    return canvas


def _assert_exact_impulse_envelope(page: Page) -> None:
    """Assert the complete source-derived force and radius envelope."""

    samples = page.evaluate(
        """
        () =>
          [0, 10, 15, 25, 41.4, 41.6, 80].flatMap((age) =>
            [1, -1].map((direction) => ({
              age,
              direction,
              ...window.__mutaTentaclesDebugInteractionAt(
                age,
                direction,
                false,
              ),
            })),
          )
        """
    )
    hold_seconds = 0.10 / 6.75
    decay_rate = 26 * 6.75
    total_seconds = 0.28 / 6.75
    held_multiplier = 2
    peak_multiplier = 20 / 1.5
    radius_multiplier = 1.5

    for sample in samples:
        age_seconds = sample["age"] * 0.001
        if age_seconds < total_seconds:
            decay_age = max(0, age_seconds - hold_seconds)
            envelope = math.exp(-decay_age * decay_rate)
            expected_force = (
                held_multiplier
                + (peak_multiplier - held_multiplier) * envelope
            )
            expected_radius = 1 + (radius_multiplier - 1) * envelope
            expected_phase = "impulse"
        else:
            expected_force = held_multiplier
            expected_radius = 1
            expected_phase = "held"

        assert sample["phase"] == expected_phase
        assert math.isclose(
            sample["forceMultiplier"],
            expected_force,
            rel_tol=0,
            abs_tol=1e-9,
        )
        assert math.isclose(
            sample["radiusMultiplier"],
            expected_radius,
            rel_tol=0,
            abs_tol=1e-9,
        )


def _read_complete_interaction_state(canvas: Locator) -> dict[str, object]:
    """Read every field that a reused interaction scratch must overwrite."""

    return canvas.evaluate(
        """
        element => ({
          phase: element.dataset.renderedInteractionPhase,
          livePhase: element.dataset.pointerInteractionPhase,
          direction: Number(element.dataset.pointerInteractionDirection),
          force: Number(element.dataset.pointerInteractionForceMultiplier),
          radius: Number(element.dataset.pointerInteractionRadiusMultiplier),
          pressed: element.dataset.renderedPointerPressed === "true",
          applications: Number(
            element.dataset.impulsePhysicsApplications || 0,
          ),
        })
        """
    )


def test_interaction_state_overwrites_every_field_across_reused_transitions(
    driver: DriverFactory,
) -> None:
    """Forbid stale scratch fields across hover, attraction, and repulsion.

    The allocation optimization is expected to reuse one mutable interaction
    result for every physics frame. That is safe only when every branch writes
    every observable field: otherwise the right-button direction, enlarged
    impulse radius, pressed flag, or phase from an earlier frame can leak into
    a later hover. Separate one-shot tests can miss this because each starts
    with a newly loaded document and therefore a freshly initialized scratch.

    This regression deliberately drives one runtime through the complete state
    cycle twice—left impulse/hold/release followed by right
    impulse/hold/release—and checks phase, direction, force, radius, press state,
    and cumulative impulse applications after every transition. Reordering the
    consume-and-reset operation, failing to overwrite any scratch field, or
    replaying one queued impulse makes a precise checkpoint fail.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        wait_for_theme_ready=False,
    )
    canvas = _open_diagnostic_surface(
        page,
        "",
        navigate=False,
    )
    page.mouse.move(1, 1)
    page.mouse.move(139, 94)
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    checkpoints = [_read_complete_interaction_state(canvas)]

    page.mouse.down(button="left")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    checkpoints.append(_read_complete_interaction_state(canvas))
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
    checkpoints.append(_read_complete_interaction_state(canvas))
    page.mouse.up(button="left")
    checkpoints.append(_read_complete_interaction_state(canvas))

    page.mouse.down(button="right")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    checkpoints.append(_read_complete_interaction_state(canvas))
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
    checkpoints.append(_read_complete_interaction_state(canvas))
    page.mouse.up(button="right")
    checkpoints.append(_read_complete_interaction_state(canvas))

    assert checkpoints == [
        {
            "phase": "hover",
            "livePhase": "hover",
            "direction": 0,
            "force": 1,
            "radius": 1,
            "pressed": False,
            "applications": 0,
        },
        {
            "phase": "impulse",
            "livePhase": "impulse",
            "direction": 1,
            "force": 13.333,
            "radius": 1.5,
            "pressed": True,
            "applications": 1,
        },
        {
            "phase": "held",
            "livePhase": "held",
            "direction": 1,
            "force": 2,
            "radius": 1,
            "pressed": True,
            "applications": 1,
        },
        {
            "phase": "hover",
            "livePhase": "hover",
            "direction": 0,
            "force": 1,
            "radius": 1,
            "pressed": False,
            "applications": 1,
        },
        {
            "phase": "impulse",
            "livePhase": "impulse",
            "direction": -1,
            "force": 13.333,
            "radius": 1.5,
            "pressed": True,
            "applications": 2,
        },
        {
            "phase": "held",
            "livePhase": "held",
            "direction": -1,
            "force": 2,
            "radius": 1,
            "pressed": True,
            "applications": 2,
        },
        {
            "phase": "hover",
            "livePhase": "hover",
            "direction": 0,
            "force": 1,
            "radius": 1,
            "pressed": False,
            "applications": 2,
        },
    ]


def test_debug_interaction_samples_never_alias_or_mutate_production_state(
    driver: DriverFactory,
) -> None:
    """Keep diagnostic samples independent from the production scratch.

    Reusing the production result in ``__mutaTentaclesDebugInteractionAt``
    would create two subtle regressions. A retained object in DevTools would
    mutate when another sample is requested, and an opposite-direction debug
    query could contaminate the next real physics frame. The optimized hot path
    may reuse storage, but this explicitly invoked diagnostic API must continue
    to return stable, independently owned snapshots and remain observational.

    The test retains an impulse sample, requests a conflicting held-repulsion
    sample, and verifies identity plus the complete first value. It also checks
    that no published production field changes synchronously and that the next
    real frame is still ordinary hover with zero direction.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        wait_for_theme_ready=False,
    )
    canvas = _open_diagnostic_surface(
        page,
        "",
        navigate=False,
    )
    result = page.evaluate(
        """
        () => {
          const canvas = document.querySelector("#muta");
          const productionBefore = {
            phase: canvas.dataset.pointerInteractionPhase,
            direction: canvas.dataset.pointerInteractionDirection,
            force: canvas.dataset.pointerInteractionForceMultiplier,
            radius: canvas.dataset.pointerInteractionRadiusMultiplier,
          };
          const first = window.__mutaTentaclesDebugInteractionAt(
            0,
            1,
            true,
          );
          const retainedFirst = { ...first };
          const second = window.__mutaTentaclesDebugInteractionAt(
            80,
            -1,
            false,
          );
          const productionAfter = {
            phase: canvas.dataset.pointerInteractionPhase,
            direction: canvas.dataset.pointerInteractionDirection,
            force: canvas.dataset.pointerInteractionForceMultiplier,
            radius: canvas.dataset.pointerInteractionRadiusMultiplier,
          };
          return {
            sameReference: first === second,
            first,
            retainedFirst,
            second,
            productionBefore,
            productionAfter,
          };
        }
        """
    )

    assert result["sameReference"] is False
    assert result["first"] == result["retainedFirst"] == {
        "forceMultiplier": 13.333333333333334,
        "radiusMultiplier": 1.5,
        "phase": "impulse",
    }
    assert result["second"] == {
        "forceMultiplier": 2,
        "radiusMultiplier": 1,
        "phase": "held",
    }
    assert result["productionAfter"] == result["productionBefore"]

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    state = _read_complete_interaction_state(canvas)
    assert state["phase"] == "hover"
    assert state["livePhase"] == "hover"
    assert state["direction"] == 0
    assert state["force"] == 1
    assert state["radius"] == 1


LEGACY_POINTER_INTERACTION_HELPER = """        function pointerInteractionAtAge(
          pressed,
          direction,
          impulseAge,
          impulsePending,
        ) {
          if (!pressed) {
            return {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            };
          }
          const heldMultiplier = heldPointerMultiplier(direction);
          let impulseEnvelope = 0;
          if (impulsePending) {
            impulseEnvelope = 1;
          } else if (impulseAge < PRESS_IMPULSE_TOTAL_SECONDS) {
            const decayAge = Math.max(
              0,
              impulseAge - PRESS_IMPULSE_HOLD_SECONDS,
            );
            impulseEnvelope = Math.exp(
              -decayAge * PRESS_IMPULSE_DECAY_RATE,
            );
          }
          return {
            forceMultiplier:
              heldMultiplier +
              (PRESS_IMPULSE_MULTIPLIER - heldMultiplier) *
                impulseEnvelope,
            radiusMultiplier:
              1 +
              (PRESS_IMPULSE_RADIUS_MULTIPLIER - 1) *
                impulseEnvelope,
            phase: impulseEnvelope > 0 ? "impulse" : "held",
          };
        }
"""

SCRATCH_POINTER_INTERACTION_HELPER = """        const pointerInteractionScratch = {
          forceMultiplier: 1,
          radiusMultiplier: 1,
          phase: "hover",
          direction: 0,
        };

        function writePointerInteractionAtAge(
          result,
          pressed,
          direction,
          impulseAge,
          impulsePending,
        ) {
          if (!pressed) {
            result.forceMultiplier = 1;
            result.radiusMultiplier = 1;
            result.phase = "hover";
            return result;
          }
          const heldMultiplier = heldPointerMultiplier(direction);
          let impulseEnvelope = 0;
          if (impulsePending) {
            impulseEnvelope = 1;
          } else if (impulseAge < PRESS_IMPULSE_TOTAL_SECONDS) {
            const decayAge = Math.max(
              0,
              impulseAge - PRESS_IMPULSE_HOLD_SECONDS,
            );
            impulseEnvelope = Math.exp(
              -decayAge * PRESS_IMPULSE_DECAY_RATE,
            );
          }
          result.forceMultiplier =
            heldMultiplier +
            (PRESS_IMPULSE_MULTIPLIER - heldMultiplier) *
              impulseEnvelope;
          result.radiusMultiplier =
            1 +
            (PRESS_IMPULSE_RADIUS_MULTIPLIER - 1) *
              impulseEnvelope;
          result.phase = impulseEnvelope > 0 ? "impulse" : "held";
          return result;
        }

        function pointerInteractionAtAge(
          pressed,
          direction,
          impulseAge,
          impulsePending,
        ) {
          return writePointerInteractionAtAge(
            {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            },
            pressed,
            direction,
            impulseAge,
            impulsePending,
          );
        }
"""

LEGACY_CURRENT_INTERACTION_CALL = """          const interaction = pointerInteractionAtAge(
            pointer.pressed || impulsePending,"""

SCRATCH_CURRENT_INTERACTION_CALL = """          const interaction = writePointerInteractionAtAge(
            pointerInteractionScratch,
            pointer.pressed || impulsePending,"""


def _toggle_pointer_interaction_allocation_strategy(source: str) -> str:
    """Return the same renderer with only the hot allocation strategy flipped."""

    if LEGACY_POINTER_INTERACTION_HELPER in source:
        assert source.count(LEGACY_POINTER_INTERACTION_HELPER) == 1
        assert source.count(LEGACY_CURRENT_INTERACTION_CALL) == 1
        transformed = source.replace(
            LEGACY_POINTER_INTERACTION_HELPER,
            SCRATCH_POINTER_INTERACTION_HELPER,
            1,
        )
        return transformed.replace(
            LEGACY_CURRENT_INTERACTION_CALL,
            SCRATCH_CURRENT_INTERACTION_CALL,
            1,
        )

    assert source.count(SCRATCH_POINTER_INTERACTION_HELPER) == 1
    assert source.count(SCRATCH_CURRENT_INTERACTION_CALL) == 1
    transformed = source.replace(
        SCRATCH_POINTER_INTERACTION_HELPER,
        LEGACY_POINTER_INTERACTION_HELPER,
        1,
    )
    return transformed.replace(
        SCRATCH_CURRENT_INTERACTION_CALL,
        LEGACY_CURRENT_INTERACTION_CALL,
        1,
    )


def test_preallocated_interaction_state_matches_allocating_reference_exactly(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Require allocation strategy to preserve trajectory and final pixels.

    Avoiding one object per physics frame is an implementation detail, not a
    license to approximate the simulation. This test reads the checked-in
    renderer and serves a temporary same-origin document with only the
    interaction allocation strategy toggled. The helper is deliberately
    bidirectional: before the optimization production is the allocating
    reference and the temporary page uses a reusable scratch; after the change
    production becomes scratch and the temporary page reconstructs the legacy
    allocating reference.

    Both documents receive the same seed, clock, pointer coordinates, left and
    right impulse/hold/release sequence, and manual frame deltas. Every
    checkpoint compares all published interaction fields and a representative
    Float64 trajectory sample. Release uses ``performance.now()``, so the two
    serial page loads may differ by machine epsilon; trajectories therefore
    use a strict ``1e-12`` absolute bound while state remains exactly equal.
    The final WebGL buffers are read with dithering disabled and hashed in the
    page, requiring byte-identical pixels without transferring a large image
    through the test protocol. Any meaningful precision change, stale scratch
    field, debug/production alias, or changed consume order therefore produces
    a behavioral, geometric, or pixel mismatch.
    """

    source = (
        THEME_STATIC_ROOT / "muta-tentacles.html"
    ).read_text(encoding="utf-8")
    alternate_source = _toggle_pointer_interaction_allocation_strategy(
        source
    )
    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.context.route(
        "**/muta-tentacles-alternate-allocation.html*",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=alternate_source,
        ),
    )

    def capture(path: str) -> dict[str, object]:
        page.goto(
            (
                f"{base_url}/{path}{DIAGNOSTIC_QUERY}"
                "&animation-time-origin=0"
            ),
            wait_until="domcontentloaded",
        )
        canvas = page.locator("#muta")
        expect(canvas).to_be_visible(timeout=30_000)
        page.wait_for_function(
            """
            () =>
              typeof window.__mutaTentaclesDebugFrameAfter === "function" &&
              typeof window.__mutaTentaclesDebugTrajectorySample === "function"
            """
        )
        page.mouse.move(1, 1)
        page.mouse.move(139, 94)
        page.evaluate(
            """
            () => {
              for (let frame = 0; frame < 8; frame += 1) {
                window.__mutaTentaclesDebugFrameAfter(16);
              }
            }
            """
        )

        checkpoints = []

        def checkpoint() -> None:
            checkpoints.append(
                page.evaluate(
                    """
                    () => {
                      const canvas = document.querySelector("#muta");
                      return {
                        state: {
                          phase: canvas.dataset.renderedInteractionPhase,
                          livePhase: canvas.dataset.pointerInteractionPhase,
                          direction: canvas.dataset.pointerInteractionDirection,
                          force:
                            canvas.dataset.pointerInteractionForceMultiplier,
                          radius:
                            canvas.dataset.pointerInteractionRadiusMultiplier,
                          pressed: canvas.dataset.renderedPointerPressed,
                          applications:
                            canvas.dataset.impulsePhysicsApplications || "0",
                        },
                        trajectory:
                          window.__mutaTentaclesDebugTrajectorySample(),
                      };
                    }
                    """
                )
            )

        checkpoint()
        page.mouse.down(button="left")
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
        checkpoint()
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
        checkpoint()
        page.mouse.up(button="left")
        checkpoint()
        page.evaluate(
            """
            () => {
              for (let frame = 0; frame < 3; frame += 1) {
                window.__mutaTentaclesDebugFrameAfter(16);
              }
            }
            """
        )
        page.mouse.down(button="right")
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
        checkpoint()
        page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
        checkpoint()
        page.mouse.up(button="right")
        checkpoint()
        page.evaluate(
            """
            () => {
              for (let frame = 0; frame < 4; frame += 1) {
                window.__mutaTentaclesDebugFrameAfter(16);
              }
            }
            """
        )
        checkpoint()

        pixel_hash = page.evaluate(
            """
            async () => {
              const canvas = document.querySelector("#muta");
              const gl = canvas.getContext("webgl2");
              gl.disable(gl.DITHER);
              window.__mutaTentaclesDebugFrameAfter(16);
              const pixels = new Uint8Array(
                gl.drawingBufferWidth * gl.drawingBufferHeight * 4,
              );
              gl.readPixels(
                0,
                0,
                gl.drawingBufferWidth,
                gl.drawingBufferHeight,
                gl.RGBA,
                gl.UNSIGNED_BYTE,
                pixels,
              );
              const digest = new Uint8Array(
                await crypto.subtle.digest("SHA-256", pixels),
              );
              return Array.from(
                digest,
                byte => byte.toString(16).padStart(2, "0"),
              ).join("");
            }
            """
        )
        return {
            "checkpoints": checkpoints,
            "pixelHash": pixel_hash,
        }

    allocating_or_current = capture("muta-tentacles.html")
    alternate = capture("muta-tentacles-alternate-allocation.html")

    assert len(allocating_or_current["checkpoints"]) == 8
    assert len(allocating_or_current["pixelHash"]) == 64
    assert alternate["pixelHash"] == allocating_or_current["pixelHash"]
    for current_checkpoint, alternate_checkpoint in zip(
        allocating_or_current["checkpoints"],
        alternate["checkpoints"],
        strict=True,
    ):
        assert alternate_checkpoint["state"] == current_checkpoint["state"]
        current_trajectory = current_checkpoint["trajectory"]
        alternate_trajectory = alternate_checkpoint["trajectory"]
        assert len(alternate_trajectory) == len(current_trajectory)
        for current_value, alternate_value in zip(
            current_trajectory,
            alternate_trajectory,
            strict=True,
        ):
            assert math.isclose(
                alternate_value,
                current_value,
                rel_tol=0,
                abs_tol=1e-12,
            )


def _read_release_metrics(canvas: Page) -> dict[str, float | str]:
    """Return the rendered release state used by behavior and mutation tests."""

    return canvas.evaluate(
        """
        (element) => ({
          phase: element.dataset.renderedInteractionPhase,
          pressed: element.dataset.renderedPointerPressed,
          force: Number(
            element.dataset.pointerInteractionForceMultiplier,
          ),
          frame: Number(element.dataset.renderedFrameId),
          displacement: Number(
            element.dataset.renderedMaximumRestDisplacement,
          ),
        })
        """
    )


def _press_surface(page: Page, canvas: Page) -> tuple[float, float, float]:
    """Submit one left impulse and return baseline, peak, and frame id."""

    baseline = float(
        canvas.get_attribute(
            "data-rendered-maximum-rest-displacement"
        )
        or "0"
    )
    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(
        box["x"] + box["width"] * 0.77,
        box["y"] + box["height"] * 0.66,
    )
    page.mouse.down(button="left")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    peak = float(
        canvas.get_attribute(
            "data-rendered-maximum-rest-displacement"
        )
        or "0"
    )
    pressed_frame = float(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    assert peak > baseline
    return baseline, peak, pressed_frame


def _assert_release_continuity(
    page: Page,
    canvas: Page,
    *,
    release: str,
) -> None:
    """Assert release clears peak force without snapping geometry backward."""

    _baseline, peak, pressed_frame = _press_surface(page, canvas)
    if release == "pointerup":
        page.mouse.up(button="left")
    elif release == "implicit-buttons-zero":
        canvas.dispatch_event(
            "pointermove",
            {
                "pointerId": 91,
                "pointerType": "mouse",
                "buttons": 0,
                "clientX": 10,
                "clientY": 10,
            },
        )
    elif release == "window-blur":
        page.evaluate("window.dispatchEvent(new Event('blur'))")
    else:
        raise AssertionError(f"Unknown release path: {release}")

    released = _read_release_metrics(canvas)
    assert released["pressed"] == "false"
    assert released["phase"] == "hover"
    assert released["frame"] > pressed_frame
    assert released["force"] == 1
    assert released["displacement"] >= peak * 0.8
    assert released["displacement"] <= peak * 1.5

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(200)")
    settled = _read_release_metrics(canvas)
    assert settled["phase"] == "hover"
    assert settled["pressed"] == "false"
    assert settled["force"] == 1


def _assert_no_deadline_render(page: Page, canvas: Page) -> None:
    """Assert elapsed impulse time never schedules synchronous rendering."""

    baseline_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(
        box["x"] + box["width"] * 0.77,
        box["y"] + box["height"] * 0.66,
    )
    page.mouse.down(button="left")
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == baseline_frame
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    submitted_frame = int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    )
    assert submitted_frame == baseline_frame + 1
    page.wait_for_timeout(120)
    assert int(
        canvas.get_attribute("data-rendered-frame-id") or "0"
    ) == submitted_frame
    page.mouse.up(button="left")


def _assert_single_peak_application(page: Page, canvas: Page) -> None:
    """Assert that only the first animation frame receives peak force."""

    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(
        box["x"] + box["width"] * 0.77,
        box["y"] + box["height"] * 0.66,
    )
    page.mouse.down(button="left")
    page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
    assert canvas.get_attribute(
        "data-impulse-physics-applications"
    ) == "1"

    page.evaluate("window.__mutaTentaclesDebugFrameAfter(9)")
    state = canvas.evaluate(
        """
        element => ({
          phase: element.dataset.renderedInteractionPhase,
          force: element.dataset.pointerInteractionForceMultiplier,
          applications: element.dataset.impulsePhysicsApplications,
          animationImpulseFrames:
            element.dataset.animationImpulseFramesSincePress,
        })
        """
    )
    assert state == {
        "phase": "held",
        "force": "2.000",
        "applications": "1",
        "animationImpulseFrames": "1",
    }
    page.mouse.up(button="left")


def test_impulse_uses_the_exact_original_41_5_ms_envelope(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Pin every part of the original short mouse-impulse formula.

    The regression visible in the supplied recording can be reintroduced
    without changing the advertised ``data-press-impulse-total-ms`` value:
    extending the hold plateau, slowing exponential decay, increasing peak
    force or radius, or accidentally applying the held multiplier all make the
    attraction look longer while a duration-only assertion remains green.

    This test samples both attraction and repulsion before, across, and after
    the exact ``0.28 / 6.75``-second boundary. It compares phase, force, and
    radius to the source HTML's complete equation with a tight numerical
    tolerance. It therefore protects the 41.5 ms clock and the shape of the
    envelope rather than merely checking a copied label in ``dataset``.
    """

    page = driver(
        stored_theme="dark",
        viewport=(390, 610),
        initial_path=f"/muta-tentacles.html{DIAGNOSTIC_QUERY}",
        wait_for_theme_ready=False,
    )
    _open_diagnostic_surface(
        page,
        base_url,
        warmup_frames=0,
        navigate=False,
    )
    _assert_exact_impulse_envelope(page)


@pytest.mark.parametrize(
    "viewport",
    [
        (390, 610),
        (755, 620),
        (1024, 650),
    ],
)
def test_release_keeps_tentacle_geometry_continuous_at_every_width(
    driver: DriverFactory,
    base_url: str,
    viewport: tuple[int, int],
) -> None:
    """Prevent a synthetic reverse impulse when the mouse button is released.

    A previous attempt to clear a stale attraction frame performed an immediate
    33 ms zero-force recovery step with much stronger damping and rest response
    than the normal simulation. That made the original attraction end, but it
    also pulled the geometry sharply in the opposite direction as soon as the
    button was released—the distinct reverse pulse reported by the user.

    At phone, tablet, and desktop widths this test pauses the normal animation
    loop, uses a fixed tentacle generation seed, records the actual maximum
    rendered tip displacement, presses, and releases. Release must submit a
    newer ordinary-hover frame and clear peak force, while displacement remains
    continuous—within 20% below and 50% above the last pressed frame instead of
    snapping below half its value. A later manual frame must remain ordinary
    hover. The test therefore detects both a stale pressed frame and the
    artificial reverse kick that replaced it.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=viewport,
    )
    canvas = _open_diagnostic_surface(page, base_url)
    _assert_release_continuity(page, canvas, release="pointerup")


@pytest.mark.parametrize(
    "release",
    [
        "implicit-buttons-zero",
        "window-blur",
    ],
)
def test_missing_pointerup_still_ends_and_redraws_the_impulse(
    driver: DriverFactory,
    base_url: str,
    release: str,
) -> None:
    """Recover when Chromium never delivers the canvas ``pointerup`` event.

    Pointer capture can be interrupted by iframe boundaries, rapid resize,
    focus loss, visibility changes, or browser compositor work. In that case a
    later pointer move with ``buttons == 0`` or a window blur is the only proof
    that the physical button is no longer held. The previous implementation
    cleared part of its logical state on one of those paths but did not force a
    replacement WebGL frame, leaving the last strong image on screen.

    For each independent fallback this test starts a real impulse, deliberately
    omits ``pointerup``, and demands the same force clearing and continuous
    hover transition as a normal release. It exercises the exact two lifecycle
    paths that make the user's intermittent report reproducible without
    permitting a reverse recovery kick.
    """

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(755, 620),
    )
    canvas = _open_diagnostic_surface(page, base_url)
    _assert_release_continuity(page, canvas, release=release)


def _restore_legacy_release_recovery(source: str) -> str:
    """Return a temporary copy containing the removed reverse-release logic."""

    replacements = [
        (
            """        const PRESS_IMPULSE_TOTAL_SECONDS = 0.28 / 6.75;
        canvas.dataset.pressImpulseTotalMs =""",
            """        const PRESS_IMPULSE_TOTAL_SECONDS = 0.28 / 6.75;
        const RELEASE_RECOVERY_SECONDS = 0.18;
        const RELEASE_RECOVERY_RATE = 34;
        const RELEASE_RECOVERY_DAMPING = 42;
        canvas.dataset.pressImpulseTotalMs =""",
        ),
        (
            """          impulsePending: false,
          impulseDirection: 1,
          energy: 0,
        };
        let renderedInteractionPhase = "hover";""",
            """          impulsePending: false,
          impulseDirection: 1,
          releaseRecoveryUntil: -Infinity,
          energy: 0,
        };
        let releaseRecoveryTimer = 0;
        let releaseRecoverySequence = 0;
        let renderedInteractionPhase = "hover";""",
        ),
        (
            """        function releasePointerInteraction() {""",
            """        function startReleaseRecovery() {
          if (releaseRecoveryTimer) {
            clearTimeout(releaseRecoveryTimer);
          }
          const recoverySequence = ++releaseRecoverySequence;
          pointer.releaseRecoveryUntil =
            performance.now() * 0.001 + RELEASE_RECOVERY_SECONDS;
          pointer.energy = 0;
          releaseRecoveryTimer = setTimeout(() => {
            releaseRecoveryTimer = 0;
            if (
              pointer.pressed ||
              recoverySequence !== releaseRecoverySequence
            ) {
              return;
            }
            runImmediateReleaseFrame();
          }, RELEASE_RECOVERY_SECONDS * 1000);
        }

        function releasePointerInteraction() {""",
        ),
        (
            """          settleReleasedPointer();
          return true;
        }

        function updatePointerPress(buttons) {""",
            """          startReleaseRecovery();
          settleReleasedPointer();
          return true;
        }

        function updatePointerPress(buttons) {""",
        ),
        (
            """          if (wasPressed && !pointer.pressed) {
            if (!pointer.impulsePending) {
              settleReleasedPointer();
            }
            return true;
          }""",
            """          if (wasPressed && !pointer.pressed) {
            pointer.impulsePending = false;
            startReleaseRecovery();
            settleReleasedPointer();
            return true;
          }""",
        ),
        (
            """          impulseAge,
          impulsePending,
        ) {
          if (!pressed) {
            result.forceMultiplier = 1;
            result.radiusMultiplier = 1;
            result.phase = "hover";
            return result;""",
            """          impulseAge,
          impulsePending,
          recovering,
        ) {
          if (!pressed) {
            result.forceMultiplier = recovering ? 0 : 1;
            result.radiusMultiplier = 1;
            result.phase = recovering ? "recovering" : "hover";
            return result;""",
        ),
        (
            """        function pointerInteractionAtAge(
          pressed,
          direction,
          impulseAge,
          impulsePending,
        ) {
          return writePointerInteractionAtAge(
            {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            },
            pressed,
            direction,
            impulseAge,
            impulsePending,
          );
        }""",
            """        function pointerInteractionAtAge(
          pressed,
          direction,
          impulseAge,
          impulsePending,
          recovering,
        ) {
          return writePointerInteractionAtAge(
            {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            },
            pressed,
            direction,
            impulseAge,
            impulsePending,
            recovering,
          );
        }""",
        ),
        (
            """            impulsePending,
          );
          interaction.direction =
            pointer.pressed || impulsePending
              ? interactionDirection
              : 0;
          return interaction;
        }

        if (interactionDiagnostics) {""",
            """            impulsePending,
            !pointer.pressed &&
              time < pointer.releaseRecoveryUntil,
          );
          interaction.direction =
            pointer.pressed || impulsePending
              ? interactionDirection
              : 0;
          return interaction;
        }

        if (interactionDiagnostics) {""",
        ),
        (
            """              Boolean(impulsePending),
            );
        }

        function settleReleasedPointer() {
          runImmediateReleaseFrame();""",
            """              Boolean(impulsePending),
              false,
            );
        }

        function settleReleasedPointer() {
          runImmediateReleaseFrame(0.033);""",
        ),
        (
            """          const velocityDamping = Math.exp(-temporalDelta * 10.2);
          // Ordinary frames retain the original integration exactly. A
          // budgeted scroll frame represents several omitted RAF intervals.
          // Start with the equivalent accumulated 60 Hz rest response rather
          // than squaring a large delta (which would overshoot), then apply a
          // bounded cadence compensation calibrated against sampled 60 FPS
          // trajectories. Without it, one solver pass travels only about
          // 54-78% as far as the visible reference path.
          const restResponse =
            temporalDelta === delta
              ? 72.0 * delta * delta
              : Math.min(
                  MAXIMUM_SCROLL_REST_RESPONSE,
                  (1 - Math.pow(0.98, temporalDelta * 60)) *
                    (
                      1 +
                      SCROLL_RESPONSE_BOOST_PER_OMITTED_FRAME *
                        Math.max(0, temporalDelta * 60 - 1)
                    ),
                );""",
            """          const releaseRecoveryActive =
            pointerInteraction.phase === "recovering";
          const velocityDamping = Math.exp(
            -temporalDelta *
              (
                releaseRecoveryActive
                  ? RELEASE_RECOVERY_DAMPING
                  : 10.2
              ),
          );
          const restResponse = releaseRecoveryActive
            ? 1 - Math.exp(-temporalDelta * RELEASE_RECOVERY_RATE)
            : temporalDelta === delta
              ? 72.0 * delta * delta
              : Math.min(
                  MAXIMUM_SCROLL_REST_RESPONSE,
                  (1 - Math.pow(0.98, temporalDelta * 60)) *
                    (
                      1 +
                      SCROLL_RESPONSE_BOOST_PER_OMITTED_FRAME *
                        Math.max(0, temporalDelta * 60 - 1)
                    ),
                );""",
        ),
    ]

    legacy_source = source
    for current, legacy in replacements:
        assert legacy_source.count(current) == 1
        legacy_source = legacy_source.replace(current, legacy, 1)
    return legacy_source


def test_old_release_recovery_fails_the_reverse_impulse_guard(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Prove the regression test rejects the exact former release algorithm.

    The reverse kick was created by a now-removed 180 ms ``recovering`` phase.
    On release that implementation zeroed the cursor force, forced an immediate
    33 ms simulation step, increased velocity damping from 10.2 to 42, and
    replaced the normal quadratic rest response with a much stronger
    exponential response of rate 34. Together those operations snapped the
    tentacle geometry back toward rest in the frame produced by ``pointerup``;
    visually this appeared as a second impulse in the opposite direction.

    This is deliberately a mutation test, not another assertion over the
    current source. It serves a temporary browser document in which the exact
    former recovery constants, timer, force phase, forced 33 ms step, damping,
    and rest response have all been restored. The production continuity
    contract must raise on that document because the released frame is no
    longer ordinary hover and its rendered displacement jumps backward. The
    temporary mutation never touches the working tree. If somebody restores
    the old algorithm in production, the direct phone/tablet/desktop test above
    will fail; if somebody weakens that direct test until the old algorithm can
    slip through, this meta-test will fail because the expected rejection will
    no longer occur.
    """

    source = (
        THEME_STATIC_ROOT / "muta-tentacles.html"
    ).read_text(encoding="utf-8")
    legacy_source = _restore_legacy_release_recovery(source)

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(755, 620),
    )
    page.context.route(
        "**/muta-tentacles-legacy-release.html*",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=legacy_source,
        ),
    )
    canvas = _open_diagnostic_surface(
        page,
        base_url,
        path="muta-tentacles-legacy-release.html",
    )

    with pytest.raises(AssertionError):
        _assert_release_continuity(
            page,
            canvas,
            release="pointerup",
        )


TIMING_MUTATIONS = [
    pytest.param(
        """            impulsePending
              ? 0
              : PRESS_IMPULSE_TOTAL_SECONDS,""",
        """            Math.max(
              0,
              time - pointer.impulseStartedAt,
            ),""",
        "single-application",
        id="reapply-peak-on-next-animation-frame",
    ),
    pytest.param(
        "const PRESS_IMPULSE_TOTAL_SECONDS = 0.28 / 6.75;",
        "const PRESS_IMPULSE_TOTAL_SECONDS = 0.28;",
        "envelope",
        id="extend-total-window",
    ),
    pytest.param(
        "const PRESS_IMPULSE_DECAY_RATE = 26 * 6.75;",
        "const PRESS_IMPULSE_DECAY_RATE = 26;",
        "envelope",
        id="slow-decay",
    ),
    pytest.param(
        "const PRESS_IMPULSE_HOLD_SECONDS = 0.10 / 6.75;",
        "const PRESS_IMPULSE_HOLD_SECONDS = 0.10;",
        "envelope",
        id="extend-peak-hold",
    ),
    pytest.param(
        "const PRESS_IMPULSE_MULTIPLIER = 20 / 1.5;",
        "const PRESS_IMPULSE_MULTIPLIER = 20;",
        "envelope",
        id="raise-peak-force",
    ),
    pytest.param(
        "const PRESS_IMPULSE_RADIUS_MULTIPLIER = 1.5;",
        "const PRESS_IMPULSE_RADIUS_MULTIPLIER = 3;",
        "envelope",
        id="raise-impulse-radius",
    ),
    pytest.param(
        "const LEFT_HELD_FORCE_MULTIPLIER = 2;",
        "const LEFT_HELD_FORCE_MULTIPLIER = 4;",
        "envelope",
        id="raise-held-attraction",
    ),
    pytest.param(
        "          runImmediateReleaseFrame();",
        """          const releaseEnergy = pointer.energy;
          pointer.energy = -4;
          for (
            let recoveryFrame = 0;
            recoveryFrame < 8;
            recoveryFrame++
          ) {
            runImmediateReleaseFrame(0.033);
          }
          pointer.energy = releaseEnergy;""",
        "release",
        id="inject-reverse-force-on-release",
    ),
    pytest.param(
        """            result.forceMultiplier = 1;
            result.radiusMultiplier = 1;
            result.phase = "hover";
            return result;""",
        """            result.forceMultiplier = 0;
            result.radiusMultiplier = 1;
            result.phase = "recovering";
            return result;""",
        "release",
        id="restore-zero-force-release-phase",
    ),
    pytest.param(
        """canvas.addEventListener("pointermove", (event) => {
          updatePointer(event);
          updatePointerPress(event.buttons);
        });""",
        """canvas.addEventListener("pointermove", (event) => {
          updatePointer(event);
        });""",
        "implicit-buttons-zero",
        id="lose-pointerup-reconciliation",
    ),
    pytest.param(
        'addEventListener("blur", releaseFromOutsideCanvas);',
        'addEventListener("muta-disabled-blur", releaseFromOutsideCanvas);',
        "window-blur",
        id="lose-window-blur-cleanup",
    ),
]


@pytest.mark.parametrize(
    ("original", "replacement", "contract"),
    TIMING_MUTATIONS,
)
def test_timing_regressions_are_detected_by_eleven_independent_mutations(
    driver: DriverFactory,
    base_url: str,
    original: str,
    replacement: str,
    contract: str,
) -> None:
    """Prove the timing tests fail for eleven plausible silent regressions.

    Three independent audits were asked to invent ways to break the interaction
    after earlier state-only tests missed the visible defect. This mutation
    test implements eleven of those attacks one at a time against the real HTML:

    1. reapply the peak on the next animation frame;
    2. extend the total impulse window;
    3. slow exponential decay;
    4. extend the full-strength hold;
    5. raise peak force;
    6. widen the strong radius;
    7. strengthen the post-impulse held attraction;
    8. inject a multi-frame force in the opposite direction on release;
    9. restore the synthetic zero-force release phase;
    10. lose the ``buttons == 0`` fallback when ``pointerup`` disappears;
    11. lose the blur cleanup used during focus/iframe lifecycle changes.

    A temporary route serves each mutated document in an isolated browser
    context. The appropriate production behavior contract must raise an
    assertion for every mutation. This is intentionally a test of test
    sensitivity: if a future refactor weakens the assertions so any sabotaged
    version passes silently, this meta-regression fails and identifies the
    precise blind spot by its parametrized id.
    """

    source = (
        THEME_STATIC_ROOT / "muta-tentacles.html"
    ).read_text(encoding="utf-8")
    assert source.count(original) == 1
    mutated = source.replace(original, replacement, 1)
    assert mutated != source

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(755, 620),
    )
    page.context.route(
        "**/muta-tentacles-mutated.html*",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=mutated,
        ),
    )
    canvas = _open_diagnostic_surface(
        page,
        base_url,
        path="muta-tentacles-mutated.html",
        warmup_frames=0 if contract == "envelope" else 8,
    )

    with pytest.raises(AssertionError):
        if contract == "single-application":
            _assert_single_peak_application(page, canvas)
        elif contract == "envelope":
            _assert_exact_impulse_envelope(page)
        elif contract == "deadline-render":
            _assert_no_deadline_render(page, canvas)
        elif contract == "release":
            _assert_release_continuity(
                page,
                canvas,
                release="pointerup",
            )
        elif contract == "implicit-buttons-zero":
            _assert_release_continuity(
                page,
                canvas,
                release=contract,
            )
        elif contract == "window-blur":
            _assert_release_continuity(
                page,
                canvas,
                release=contract,
            )
        else:
            raise AssertionError(f"Unknown mutation contract: {contract}")
