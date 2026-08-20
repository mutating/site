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
    strong impulse immediately on ``pointerdown``; if Chromium then postpones
    its next ``requestAnimationFrame`` while recalculating the newly narrow
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

    This test deliberately disables regular animation frames, records the
    immediate impulse frame id, and waits beyond the complete source envelope.
    No hidden timer may submit another frame. It then advances one explicit
    50 ms frame and verifies that the wall-clock force equation naturally
    resolves to held mode. This preserves the original physics while proving
    that the deadline itself can never insert blocking work ahead of release.
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
    page.mouse.down(button="left")

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
    real press, the initially submitted frame id and measured input latency
    must remain unchanged for 120 ms—almost three complete impulse windows.
    It then advances the first non-redundant physics frame by nine milliseconds and
    verifies that the peak was not applied a second time. Release must then be
    processed normally. This full-density assertion is the part the earlier
    lightweight tests lacked: it catches the long visible deformation even
    when the nominal force timer and low-density state tests look correct.
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
        "animationImpulseFrames": "0",
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


def test_scroll_mode_holds_the_complete_webgl_frame_and_physics_state(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Prevent invisible tentacle physics from stealing page-scroll frames.

    The contact surface renders a supersampled 2400-pixel-wide WebGL frame on a
    typical desktop viewport. The first scroll optimization correctly held its
    last complete texture instead of submitting new surface, scatter, and
    composite passes, but it still advanced hundreds of 50-link Verlet chains
    in the iframe. Those invisible updates consumed roughly six milliseconds
    on the same main thread every animation tick and made the parent page move
    in steps even though the canvas appeared frozen.

    This test activates that exact iframe runtime mode and records the canvas
    backing dimensions, rendered frame id, and independent physics frame id.
    During active scrolling both counters must remain unchanged: Chromium can
    composite the existing full-quality texture without any hidden JavaScript
    physics or GPU work. Once scrolling ends, one ordinary frame must advance
    both counters at the original backing dimensions. Pointer-press tests cover
    the deliberate exception that keeps an active mouse interaction responsive.

    Reintroducing the former "render-only" suppression makes the physics id
    advance and fails this regression; lowering resolution changes the stored
    dimensions; and accidentally leaving the iframe paused after ``scrollend``
    prevents the final frame from advancing. Together those assertions protect
    smooth scrolling without allowing a visual-quality workaround.
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
    """Keep heavy tentacle work paused for the complete scroll quiet period.

    The bottom-of-page hitch was caused by the full-density WebGL simulation
    resuming between two events from one momentum-scroll gesture. The host now
    treats 320 ms without another scroll event as a fallback end boundary while
    native ``scrollend`` remains the preferred signal. This regression opens
    the real contact iframe at both desktop and phone widths, freezes browser
    time, dispatches the same top-level event as a physical page movement, and
    proves that suppression is immediate, still active at exactly 319 ms, and
    released at exactly 320 ms.

    No wall-clock sleep participates in the assertions: Playwright advances the
    page's production ``setTimeout`` deterministically. Restoring the former
    120 ms delay, rounding the boundary down, failing to start suppression, or
    leaving the renderer paused forever therefore fails identically on a fast
    workstation and a slow CI worker. The two viewport cases protect the
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
    tentacle renderer wakes 320 ms after the first event, performs an expensive
    frame while the page is still moving, and recreates the intermittent
    stutter even though the simple single-event boundary test still passes.

    This scenario freezes time on the real desktop and mobile layouts, begins a
    scroll, advances 240 ms, then emits a second scroll event. It requires the
    iframe to remain suppressed through 319 ms measured from that second event
    and to resume only on its following millisecond. Because no real waiting or
    measured FPS is involved, the test isolates timer replacement itself and
    fails reliably if ``clearTimeout`` is removed, the old handle survives, or
    the replacement deadline is calculated from the start of the gesture.
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
    simulation running during the gesture or paused after it. This test first
    proves that native ``scrollend`` resumes rendering immediately without
    advancing the 320 ms fallback clock. It then dispatches a wheel event on the
    real child canvas and requires the same immediate suppression, the same
    319 ms protected interval, and the same fallback release at 320 ms. Both
    halves also inspect the parent iframe's ``data-scroll-active`` state. That
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
    increment the suppression counter or pause rendering. It then sends one
    negative delta, which can genuinely move the page upward, and requires the
    normal compositor-friendly suppression to start. The final assertion is
    essential: the boundary fix must remove only the false freeze and preserve
    the existing smooth-scroll optimization whenever movement is possible.

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
    """End an existing render pause as soon as scrolling becomes impossible.

    The last real scroll event can put the iframe into suppression immediately
    before the document reaches its lower limit. If the following momentum
    wheel event is merely ignored, the already-running parent and child timers
    still hold the final tentacle texture for another 320 ms. Worse, a stale
    parent state can reapply that pause after the child has locally resumed.
    The visible result is a shorter version of the same bottom-of-page freeze.

    At both responsive widths this test starts from the exact page bottom and
    emits an inward iframe wheel delta. Synthetic wheel events do not move the
    document, but this one can scroll upward and therefore deterministically
    places the real child-to-parent protocol into active-scroll mode without
    racing a browser-generated ``scrollend``. It then emits an outward delta
    that cannot change ``scrollTop``. The parent state and child renderer must
    both become active immediately. Advancing the fake clock through the
    discarded deadline must not produce a late state reversal. This catches
    incomplete fixes that filter only new pauses, introduce another suppression
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
    the original square. Reapplying its 13.333× peak on both the synchronous
    input render and the following animation frame injects the strong force
    twice; the resulting deformation survives much longer than the nominal
    clock and is the user-visible defect reported repeatedly.

    This regression freezes normal animation, presses either mouse button, and
    verifies that the immediate input render is the sole peak-force physics
    application. It then advances the first non-redundant frame by only nine
    milliseconds—well inside the old envelope, where the bug definitely
    reproduced—and demands ordinary held force with the cumulative
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
    page.mouse.down(button=button)

    expect(canvas).to_have_attribute(
        "data-immediate-impulse-frame",
        "enabled",
        timeout=1_000,
    )
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
    """Assert that only the synchronous input frame receives peak force."""

    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(
        box["x"] + box["width"] * 0.77,
        box["y"] + box["height"] * 0.66,
    )
    page.mouse.down(button="left")
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
        "animationImpulseFrames": "0",
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
          energy: 0,
        };
        let renderedInteractionPhase = "hover";""",
            """          impulsePending: false,
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
            runImmediateInteractionFrame(true);
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
            pointer.impulsePending = false;
            settleReleasedPointer();""",
            """          if (wasPressed && !pointer.pressed) {
            pointer.impulsePending = false;
            startReleaseRecovery();
            settleReleasedPointer();""",
        ),
        (
            """          impulseAge,
          impulsePending,
        ) {
          if (!pressed) {
            return {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            };""",
            """          impulseAge,
          impulsePending,
          recovering,
        ) {
          if (!pressed) {
            return {
              forceMultiplier: recovering ? 0 : 1,
              radiusMultiplier: 1,
              phase: recovering ? "recovering" : "hover",
            };""",
        ),
        (
            """            impulsePending,
          );
        }

        if (interactionDiagnostics) {""",
            """            impulsePending,
            !pointer.pressed &&
              time < pointer.releaseRecoveryUntil,
          );
        }

        if (interactionDiagnostics) {""",
        ),
        (
            """              Boolean(impulsePending),
            );
        }

        function settleReleasedPointer() {
          runImmediateInteractionFrame(true);""",
            """              Boolean(impulsePending),
              false,
            );
        }

        function settleReleasedPointer() {
          runImmediateInteractionFrame(true, 0.033);""",
        ),
        (
            """          const velocityDamping = Math.exp(-delta * 10.2);
          const restResponse = 72.0 * delta * delta;""",
            """          const releaseRecoveryActive =
            pointerInteraction.phase === "recovering";
          const velocityDamping = Math.exp(
            -delta *
              (
                releaseRecoveryActive
                  ? RELEASE_RECOVERY_DAMPING
                  : 10.2
              ),
          );
          const restResponse = releaseRecoveryActive
            ? 1 - Math.exp(-delta * RELEASE_RECOVERY_RATE)
            : 72.0 * delta * delta;""",
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
        "          runImmediateInteractionFrame(true);",
        """          const releaseEnergy = pointer.energy;
          pointer.energy = -4;
          for (
            let recoveryFrame = 0;
            recoveryFrame < 8;
            recoveryFrame++
          ) {
            runImmediateInteractionFrame(true, 0.033);
          }
          pointer.energy = releaseEnergy;""",
        "release",
        id="inject-reverse-force-on-release",
    ),
    pytest.param(
        """            return {
              forceMultiplier: 1,
              radiusMultiplier: 1,
              phase: "hover",
            };""",
        """            return {
              forceMultiplier: 0,
              radiusMultiplier: 1,
              phase: "recovering",
            };""",
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
