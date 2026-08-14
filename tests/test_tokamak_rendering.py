"""Browser tests for theme-aware rendering of the tokamak wireframe."""

from __future__ import annotations

from collections.abc import Callable
import io
from pathlib import Path
import re

from PIL import Image, ImageChops, ImageStat
import pytest
from playwright.sync_api import Locator, Page, expect


DriverFactory = Callable[..., Page]
pytestmark = [
    pytest.mark.interactive_visuals,
    pytest.mark.xdist_group(name="tokamak-webgl"),
]

TOKAMAK_RESPONSIVE_WIDTHS = tuple(
    sorted(
        set(range(280, 1921, 20))
        | set(range(840, 1025))
        | {579, 580, 581, 859, 860, 861, 1119, 1120, 1121}
    )
)
TOKAMAK_VISUAL_WIDTHS = (280, 580, 860, 861, 898, 960, 1120, 1440, 1920)


def _reactor_canvas(page: Page) -> Locator:
    """Return the rendered canvas inside the self-contained tokamak frame."""

    canvas = page.frame_locator(".tokamak-model__frame").locator("canvas")
    expect(canvas).to_be_visible()
    return canvas


def _show_reactor(page: Page) -> Locator:
    """Center the reactor in its active rendering range and return its canvas.

    ``scroll_into_view_if_needed`` may stop as soon as the large model merely
    touches a viewport edge. At that boundary the page's IntersectionObserver
    can legitimately keep the iframe paused, making otherwise unrelated render
    tests depend on a race between scrolling and intersection delivery. An
    explicit centered scroll gives those tests one deterministic visible state.
    """

    model = page.locator(".tokamak-model")
    expect(model).to_have_attribute(
        "data-scroll-progress",
        re.compile(r"\d+\.\d+"),
    )
    model.evaluate(
        """
        (element) => element.scrollIntoView({
          behavior: "instant",
          block: "center",
        })
        """
    )
    page.evaluate(
        """
        () => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })
        """
    )
    page.evaluate("() => window.dispatchEvent(new Event('scroll'))")
    canvas = _reactor_canvas(page)
    expect(canvas).to_have_attribute("data-render-visible", "true")
    expect(canvas).to_have_attribute("data-render-frames", re.compile(r"[1-9]\d*"))
    return canvas


def _pink_content_bounds(image_bytes: bytes) -> tuple[int, int, int, int]:
    """Return the bounding box of saturated reactor pixels in a canvas shot."""

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    pixels = image.load()
    matching_pixels: list[tuple[int, int]] = []

    for y_coordinate in range(image.height):
        for x_coordinate in range(image.width):
            red, green, blue = pixels[x_coordinate, y_coordinate]
            if (
                red > 70
                and red > green * 1.45
                and blue > green * 1.15
            ):
                matching_pixels.append((x_coordinate, y_coordinate))

    assert matching_pixels, "The reactor screenshot contained no pink pixels."
    x_coordinates = [point[0] for point in matching_pixels]
    y_coordinates = [point[1] for point in matching_pixels]
    return (
        min(x_coordinates),
        min(y_coordinates),
        max(x_coordinates),
        max(y_coordinates),
    )


def _image_difference_statistics(
    expected: Image.Image,
    actual: Image.Image,
) -> tuple[float, float, float]:
    """Return the unchanged mean/high/extreme visual-reference metrics."""

    difference = ImageChops.difference(expected, actual)
    pixels = list(difference.getdata())
    total_pixels = len(pixels)
    return (
        max(ImageStat.Stat(difference).mean),
        sum(max(pixel) > 8 for pixel in pixels) / total_pixels,
        sum(max(pixel) > 24 for pixel in pixels) / total_pixels,
    )


def test_tokamak_shows_accessible_fallback_without_webgl(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Replace the standalone reactor with an accessible fallback without WebGL."""

    page = driver(
        initial_path="/blog/",
        load_interactive_visuals=True,
        init_scripts=(
            """
            (() => {
              const nativeGetContext = HTMLCanvasElement.prototype.getContext;
              HTMLCanvasElement.prototype.getContext = function (kind, options) {
                if (kind === "webgl" || kind === "webgl2") return null;
                return nativeGetContext.call(this, kind, options);
              };
            })();
            """,
        ),
    )
    errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.goto(
        f"{base_url}/tokamak-reactor.html",
        wait_until="domcontentloaded",
    )

    fallback = page.get_by_role("status")
    expect(fallback).to_be_visible()
    expect(fallback).to_have_attribute("lang", "en")
    expect(fallback.locator(".tokamak-fallback__title")).to_have_text(
        "3D reactor unavailable"
    )
    expect(fallback.locator(".tokamak-fallback__copy")).to_contain_text(
        "cannot display the WebGL visualization"
    )
    expect(page.locator("html")).to_have_attribute(
        "data-tokamak-fallback", "webgl-unavailable"
    )
    expect(page.locator("canvas")).to_have_count(0)
    expect(page.locator("#root .tokamak-fallback")).to_have_count(1)
    assert errors == []
    assert console_errors == []


def test_parent_page_reveals_embedded_tokamak_fallback_without_webgl(
    driver: DriverFactory,
) -> None:
    """Reveal the embedded fallback after its readiness handshake with the parent."""

    page = driver(
        load_interactive_visuals=True,
        init_scripts=(
            """
            (() => {
              const nativeGetContext = HTMLCanvasElement.prototype.getContext;
              HTMLCanvasElement.prototype.getContext = function (kind, options) {
                if (kind === "webgl" || kind === "webgl2") return null;
                return nativeGetContext.call(this, kind, options);
              };
            })();
            """,
        ),
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    model = page.locator(".tokamak-model")
    model.scroll_into_view_if_needed()

    expect(model).to_have_class(re.compile(r"\bis-ready\b"))
    fallback = page.frame_locator(".tokamak-model__frame").get_by_role("status")
    expect(fallback).to_be_visible()
    expect(fallback).to_contain_text("3D reactor unavailable")
    expect(page.locator(".tokamak-model__frame")).to_have_css("opacity", "1")
    assert errors == []


def test_tokamak_runtime_currently_accepts_state_from_non_parent_windows(
    driver: DriverFactory,
) -> None:
    """Characterize the embedded reactor accepting a state message from itself."""

    page = driver(
        stored_theme="light",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)
    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    canvas.evaluate(
        """
        () => window.postMessage({
          type: "tokamak-state",
          theme: "dark",
          rotationX: -0.3,
          modelScale: 1,
          visible: true,
        }, "*")
        """
    )
    expect(canvas).to_have_attribute("data-reactor-theme", "dark")


def test_tokamak_exposes_no_direct_camera_controls_and_ignores_user_input(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Standalone Tokamak exposes no controls and ignores every direct input."""

    page = driver(
        initial_path="/blog/",
        stored_theme="dark",
        viewport=(480, 420),
        load_interactive_visuals=True,
    )
    page.goto(f"{base_url}/tokamak-reactor.html", wait_until="domcontentloaded")
    canvas = page.locator("canvas")
    expect(canvas).to_have_attribute(
        "data-shader-compilation", "complete", timeout=30_000
    )
    expect(canvas).to_have_attribute("data-render-frames", re.compile(r"[1-9]\d*"))
    expect(canvas).not_to_have_attribute("tabindex", re.compile(r".+"))
    expect(canvas).to_have_attribute(
        "aria-label",
        "Scroll-linked three-dimensional wireframe toroidal field reactor",
    )
    expect(page.locator(".interaction-hint")).to_have_count(0)
    expect(page.locator(".reactor-scene")).to_have_css("cursor", "default")
    expect(page.locator(".reactor-scene")).to_have_css("touch-action", "auto")
    expect(page.locator(".reactor-scene")).to_have_css("pointer-events", "none")

    unchanged_frames = int(canvas.get_attribute("data-render-frames") or "0")
    canvas.dispatch_event(
        "pointerdown",
        {"pointerId": 1, "pointerType": "mouse", "clientX": 120, "clientY": 120},
    )
    canvas.dispatch_event(
        "pointermove",
        {"pointerId": 1, "pointerType": "mouse", "clientX": 220, "clientY": 180},
    )
    canvas.dispatch_event("pointerup", {"pointerId": 1, "pointerType": "mouse"})
    canvas.dispatch_event("wheel", {"deltaY": 120})
    page.evaluate(
        """
        () => window.dispatchEvent(new KeyboardEvent("keydown", {
          key: "r",
          bubbles: true,
        }))
        """
    )
    canvas.dispatch_event("dblclick")
    page.evaluate(
        """
        () => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })
        """
    )

    assert int(canvas.get_attribute("data-render-frames") or "0") == unchanged_frames


def test_light_tokamak_uses_invisible_depth_occluders(
    driver: DriverFactory,
) -> None:
    """Keep responsive pixels clear and light-theme rear edges occluded.

    The light theme previously hid every solid reactor mesh while leaving all
    line objects visible. Removing those surfaces also removed their depth
    information, so edges on the back and inside of the reactor showed through
    the front and formed dense, illegible clumps that did not exist in the dark
    theme. The light renderer must therefore keep a nonzero set of solid meshes
    as colorless depth occluders: they may write only depth, never color, while
    the ordinary page background and the pink wireframe remain visible. The
    same live WebGL context is also checked at the key responsive widths where
    the host's analytical scale invariant changes regimes. Reusing this
    established renderer test verifies the real camera projection without
    leaking an extra graphics context into the rest of the suite.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 738),
        load_interactive_visuals=True,
    )
    model = page.locator(".tokamak-model")
    canvas = _show_reactor(page)

    for viewport_width in TOKAMAK_VISUAL_WIDTHS:
        page.set_viewport_size({"width": viewport_width, "height": 738})
        model.evaluate(
            """
            (element) => element.scrollIntoView({
              behavior: "instant",
              block: "center",
            })
            """
        )
        page.evaluate(
            """
            () => new Promise((resolve) => {
              requestAnimationFrame(() => requestAnimationFrame(resolve));
            })
            """
        )
        page.evaluate("() => window.dispatchEvent(new Event('scroll'))")
        image_bytes = canvas.screenshot()
        image = Image.open(io.BytesIO(image_bytes))
        left, top, right, bottom = _pink_content_bounds(image_bytes)
        margins = {
            "left": left,
            "right": image.width - 1 - right,
            "top": top,
            "bottom": image.height - 1 - bottom,
        }
        required_horizontal_margin = max(6, round(image.width * 0.04))
        required_vertical_margin = max(6, round(image.height * 0.04))

        for edge in ("left", "right"):
            assert margins[edge] >= required_horizontal_margin, (
                f"At {viewport_width}px the reactor has only "
                f"{margins[edge]}px of {edge} clearance; expected at least "
                f"{required_horizontal_margin}px."
            )
        for edge in ("top", "bottom"):
            assert margins[edge] >= required_vertical_margin, (
                f"At {viewport_width}px the reactor has only "
                f"{margins[edge]}px of {edge} clearance; expected at least "
                f"{required_vertical_margin}px."
            )

    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    diagnostics = canvas.evaluate(
        """
        (element) => ({
          wireObjects: Number(element.dataset.wireObjects),
          depthOccluders: Number(element.dataset.lightDepthOccluders),
        })
        """
    )

    assert diagnostics["wireObjects"] > 0
    assert diagnostics["depthOccluders"] > 0


def test_tokamak_scale_reserves_clearance_across_responsive_widths(
    driver: DriverFactory,
) -> None:
    """Reserve enough horizontal space for every supported responsive width.

    The containment section remains two-column just above its 860 px layout
    breakpoint. In that narrow desktop interval the iframe used to stay
    600 px tall while becoming only about 400 px wide. The camera therefore
    clipped both sides of the reactor, and the clipped plasma bloom exposed
    the rectangular iframe boundary on some GPU compositors. This
    timing-independent sweep checks every CSS pixel from 840 through 1024,
    both sides of the other relevant breakpoints, and 20 px samples through
    the rest of the supported 280–1920 px range. WebGL is intentionally not
    loaded: the host geometry itself must constrain the posted model scale to
    the measured frame aspect ratio. Restoring the old height-only scale makes
    the 861–940 px checks fail immediately, without depending on GPU speed.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 738),
        load_interactive_visuals=False,
    )
    model = page.locator(".tokamak-model")

    for viewport_width in TOKAMAK_RESPONSIVE_WIDTHS:
        page.set_viewport_size({"width": viewport_width, "height": 738})
        page.wait_for_function(
            """
            (selector) => {
              const element = document.querySelector(selector);
              if (!element) return false;
              const rect = element.getBoundingClientRect();
              const renderedHeight = Math.min(600, Math.max(1, rect.height));
              const heightScale =
                1 / (1 + (600 - renderedHeight) * 0.00125);
              const aspectScale = Math.min(
                1,
                (Math.max(1, rect.width) / renderedHeight) * 1.1,
              );
              const expected = Math.min(heightScale, aspectScale);
              const actual = Number(element.dataset.modelScale);
              return Number.isFinite(actual) &&
                Math.abs(actual - expected) <= 0.002;
            }
            """,
            arg=".tokamak-model",
        )
        geometry = model.evaluate(
            """
            (element) => {
              const rect = element.getBoundingClientRect();
              return {
                width: rect.width,
                height: rect.height,
                scale: Number(element.dataset.modelScale),
              };
            }
            """
        )
        rendered_height = min(600, max(1, geometry["height"]))
        height_scale = 1 / (1 + (600 - rendered_height) * 0.00125)
        aspect_scale = min(
            1,
            (max(1, geometry["width"]) / rendered_height) * 1.1,
        )
        maximum_safe_scale = min(height_scale, aspect_scale)

        assert abs(geometry["scale"] - maximum_safe_scale) <= 0.002, (
            f"At {viewport_width}px the host posted model scale "
            f"{geometry['scale']:.3f}, but its measured "
            f"{geometry['width']:.1f}×{geometry['height']:.1f}px frame "
            f"requires {maximum_safe_scale:.3f}."
        )


def test_tokamak_frame_edges_remain_transparent_and_feathered(
    driver: DriverFactory,
) -> None:
    """Prevent a rectangular reactor frame from reappearing on any theme.

    Fitting the geometry prevents ordinary clipping, but a wide bloom can
    still produce tiny platform-specific color differences where the iframe
    ends. The iframe therefore supplies a small transparent mask on every
    edge as a final GPU-independent guard, while both the iframe document and
    its host remain transparent beneath the renderer. This assertion reads
    the actual computed styles on both sides of the frame, so removing the
    feathering or accidentally restoring an HTML background causes a
    deterministic failure without relying on screenshot timing or a
    particular graphics driver.
    """

    page = driver(
        stored_theme="dark",
        viewport=(898, 738),
        load_interactive_visuals=False,
    )
    frame = page.locator(".tokamak-model__frame")

    frame_styles = frame.evaluate(
        """
        (element) => {
          const styles = getComputedStyle(element);
          return {
            backgroundColor: styles.backgroundColor,
            maskImage: styles.maskImage,
            webkitMaskImage: styles.webkitMaskImage,
          };
        }
        """
    )
    effective_mask = (
        frame_styles["maskImage"] or frame_styles["webkitMaskImage"]
    )

    assert frame_styles["backgroundColor"] == "rgba(0, 0, 0, 0)"
    assert "linear-gradient" in effective_mask
    assert effective_mask.count("linear-gradient") == 2
    embedded_source = (
        Path(__file__).resolve().parents[1]
        / "themes"
        / "mutating"
        / "static"
        / "tokamak-reactor.html"
    ).read_text(encoding="utf-8")
    assert (
        "html, body, #root, .scene-shell, .reactor-scene {"
        in embedded_source
    )
    assert "background: transparent !important;" in embedded_source


def test_tokamak_bloom_radius_does_not_reveal_frame_edges(
    driver: DriverFactory,
) -> None:
    """Keep the plasma glow tight enough that the iframe boundary stays hidden.

    The former shared bloom profile spread a faint pink wash across the
    light-theme reactor in some sizes and camera angles. Besides reaching the
    rectangular canvas edge, that wash looked like translucent shading over the
    vessel instead of light emitted by the plasma. The light renderer therefore
    uses a compact 0.025 radius and controlled 0.11 strength, while the dark
    theme retains its established 0.18/1.15 profile. Bloom contributes RGB but
    no alpha in the light compositor, so raising the ring's visible intensity
    cannot recreate the translucent dim band over the vessel. This test fixes
    the live profile and compositor diagnostics so either regression is caught.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)

    expect(canvas).to_have_attribute("data-bloom-radius", "0.025")
    expect(canvas).to_have_attribute("data-bloom-strength", "0.11")
    expect(canvas).to_have_attribute(
        "data-light-compositing",
        "premultiplied-additive-v3",
    )
    expect(canvas).to_have_attribute("data-light-bloom-alpha", "rgb-only")
    assert float(canvas.get_attribute("data-bloom-radius") or "1") < 0.1


def test_theme_switches_do_not_add_wireframe_geometry(
    driver: DriverFactory,
) -> None:
    """Preserve one wireframe and switch only its theme-specific occlusion.

    The visible light-theme defect looked as though extra geometry had been
    added, but it was caused by rear edges becoming visible through hidden
    surfaces. This regression test records the number of line objects, toggles
    repeatedly between light and dark, and proves that the count never changes.
    It also requires depth-only occluders in every light pass and none in every
    dark pass, catching both duplicated lines and a return of the original
    see-through rendering bug after a repeated theme change.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)
    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )

    expect(canvas).to_have_attribute("data-reactor-theme", "dark")
    initial_wire_count = int(canvas.get_attribute("data-wire-objects") or "0")
    assert initial_wire_count > 0
    assert canvas.get_attribute("data-light-depth-occluders") == "0"

    for expected_theme in ("light", "dark", "light", "dark"):
        toggle.click()
        expect(page.locator("html")).to_have_attribute(
            "data-theme",
            expected_theme,
        )
        expect(canvas).to_have_attribute(
            "data-reactor-theme",
            expected_theme,
        )

        wire_count = int(canvas.get_attribute("data-wire-objects") or "0")
        depth_occluders = int(
            canvas.get_attribute("data-light-depth-occluders") or "0"
        )
        assert wire_count == initial_wire_count

        if expected_theme == "light":
            assert depth_occluders > 0
            expect(canvas).to_have_attribute("data-bloom-radius", "0.025")
            expect(canvas).to_have_attribute("data-bloom-strength", "0.11")
        else:
            assert depth_occluders == 0
            expect(canvas).to_have_attribute("data-bloom-radius", "0.18")
            expect(canvas).to_have_attribute("data-bloom-strength", "1.15")


def test_tokamak_wireframe_uses_theme_specific_site_pink_palettes(
    driver: DriverFactory,
) -> None:
    """Keep every reactor edge visually aligned with the site's accent pink.

    The standalone WebGL illustration previously retained its own coral
    palette, and a later attempt fed the page's literal light ``--hot`` value
    into the transparent additive renderer. The latter value was technically
    identical before compositing but became visibly violet once antialiasing,
    transparency, and the light postprocessing pass were applied. Light mode
    therefore uses the deliberately warmer render input ``#ff2c58`` so the
    pixels seen by the user resolve to the site's pink family. Dark mode does
    not have that white-background shift and must retain the exact live
    ``--hot`` value ``#ff3e9d`` that already looked correct.

    This regression requires every batched and unbatched line group to resolve
    to the one intended input color for its theme and repeats the check after
    switching to dark and back to light. Thus a partial recolor, a stale iframe
    palette, an accidental change to the approved dark appearance, or failure
    to restore the correct materials after a toggle all fail deterministically.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)
    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )

    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    expect(canvas).to_have_attribute(
        "data-light-palette",
        "warm-site-pink-v2",
    )
    expect(canvas).to_have_attribute(
        "data-light-wire-color",
        "#ff2c58",
    )
    light_material_color = canvas.get_attribute("data-wire-colors") or ""
    assert light_material_color == "#ff2c58"
    assert "," not in light_material_color

    toggle.click()
    expect(canvas).to_have_attribute("data-reactor-theme", "dark")
    dark_hot = page.locator("html").evaluate(
        "(element) => getComputedStyle(element).getPropertyValue('--hot').trim()"
    )
    expect(canvas).to_have_attribute(
        "data-dark-wire-color",
        dark_hot.lower(),
    )
    expect(canvas).to_have_attribute(
        "data-wire-colors",
        dark_hot.lower(),
    )

    toggle.click()
    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    expect(canvas).to_have_attribute(
        "data-wire-colors",
        light_material_color,
    )


def test_tokamak_demand_render_lifecycle_stays_idle_active_and_offscreen(
    driver: DriverFactory,
) -> None:
    """Preserve the complete demand-render lifecycle in one ordered scene.

    The reactor has no autonomous motion: its angle changes only when scrolling,
    resizing, or switching theme. The former implementation nevertheless called
    requestAnimationFrame forever and reran the full scene, bloom, SMAA, and
    output pipeline while every visible pixel remained unchanged. This test
    scrolls the model into view, waits for the state-driven frame to settle, and
    proves that its frame counter remains exactly constant during an idle
    interval. It then scrolls the page slightly and requires one new frame,
    guarding both halves of the contract: no waste while static and no frozen
    visual when the scroll-linked angle really changes.

    Once active, the same scene must keep benchmark-only GPU timer polling
    disabled throughout several scroll-linked renders.  Finally, the test
    recenters the reactor before moving it fully offscreen and proves that both
    its paused state and frame counter remain unchanged for the original 400 ms
    observation window.  Each phase records its own frame baseline and restores
    a visible precondition, so sharing one WebGL compilation removes setup work
    without carrying an ambiguous state into the next lifecycle contract.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)

    page.wait_for_timeout(150)
    settled_frames = int(canvas.get_attribute("data-render-frames") or "0")
    page.wait_for_timeout(400)
    assert int(canvas.get_attribute("data-render-frames") or "0") == settled_frames

    page.mouse.wheel(0, 80)
    for _ in range(20):
        if int(canvas.get_attribute("data-render-frames") or "0") > settled_frames:
            break
        page.wait_for_timeout(25)

    assert int(canvas.get_attribute("data-render-frames") or "0") > settled_frames

    canvas = _show_reactor(page)
    scrolling_baseline = int(
        canvas.get_attribute("data-render-frames") or "0"
    )
    for offset in (80, -35, 110, -60):
        page.mouse.wheel(0, offset)
        page.wait_for_timeout(50)

    expect(canvas).to_have_attribute("data-gpu-timer", "disabled")
    assert canvas.get_attribute("data-gpu-frame-ms") is None
    expect(canvas).to_have_attribute(
        "data-last-frame-ms",
        re.compile(r"^\d+\.\d{3}$"),
    )
    assert int(
        canvas.get_attribute("data-render-frames") or "0"
    ) > scrolling_baseline

    canvas = _show_reactor(page)
    page.evaluate(
        "() => window.scrollTo({top: 0, behavior: 'instant'})"
    )
    expect(canvas).to_have_attribute("data-render-visible", "false")
    expect(canvas).to_have_attribute("data-render-paused", "true")
    paused_frames = int(canvas.get_attribute("data-render-frames") or "0")
    page.wait_for_timeout(400)
    assert int(canvas.get_attribute("data-render-frames") or "0") == paused_frames


def test_scroll_state_reaches_tokamak_without_a_second_host_animation_frame(
    driver: DriverFactory,
) -> None:
    """Keep the scroll-linked reactor angle on the browser's current frame.

    The first FPS optimization accidentally introduced two serial animation
    frame boundaries between a physical scroll and the visible reactor update:
    the page's scroll listener deferred its geometry calculation to a host
    ``requestAnimationFrame``, then the iframe correctly deferred the expensive
    WebGL draw to its own ``requestAnimationFrame``. Under sustained scrolling,
    especially while the browser was busy rasterizing the large section, that
    extra host frame made the model angle advance in conspicuous steps and the
    whole area felt as though scrolling itself were stuttering.

    This regression pauses animation-frame callbacks after the fully loaded
    reactor has settled and then performs a real programmatic scroll. The host
    element's diagnostic rotation must change even while frames remain paused:
    scroll delivery itself must calculate and send the newest state
    synchronously. Reinstating the former host-side frame queue leaves the
    rotation unchanged and makes this test fail. The iframe is still allowed to
    coalesce the costly render into its next frame, so this test protects smooth
    input tracking without requiring redundant GPU work. The test explicitly
    requests instant scrolling and dispatches the host event in the same task;
    its result therefore cannot depend on CSS smooth-scroll progress, a display
    frame, or an arbitrary wall-clock sleep.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
        controllable_animation_frames=True,
    )
    model = page.locator(".tokamak-model")
    model.scroll_into_view_if_needed()
    canvas = _reactor_canvas(page)
    expect(canvas).to_have_attribute("data-render-visible", "true")
    initial_rotation = model.get_attribute("data-rotation-x")
    assert initial_rotation is not None

    page.evaluate("window.__mutaTestSetAnimationFramesPaused(true)")
    try:
        scroll_positions = page.evaluate(
            """
            () => {
              const before = window.scrollY;
              const maximum =
                document.documentElement.scrollHeight - window.innerHeight;
              const target =
                before <= maximum - 120 ? before + 120 : before - 120;
              window.scrollTo({top: target, behavior: "instant"});
              window.dispatchEvent(new Event("scroll"));
              return {before, target};
            }
            """
        )

        assert page.evaluate("window.scrollY") != scroll_positions["before"]
        assert model.get_attribute("data-rotation-x") != initial_rotation
    finally:
        page.evaluate("window.__mutaTestSetAnimationFramesPaused(false)")


def test_tokamak_pipeline_preserves_msaa_batching_and_whole_frame_work(
    driver: DriverFactory,
) -> None:
    """Keep geometric edge quality and static batching in one fresh scene.

    Four-sample MSAA is still required for the wireframe scene, but applying it
    again to fullscreen bloom, SMAA, and final-output triangles cannot improve
    their geometry and multiplies render-target bandwidth. The browser canvas
    likewise receives an already postprocessed fullscreen image. This test
    fixes the intended pipeline topology: 4x MSAA for the scene, no default
    canvas MSAA, single-sample postprocessing, and the fused sixteen-pass
    fullscreen chain. Reintroducing the former multisampled composer buffers or
    accidentally dropping scene MSAA will therefore fail explicitly.

    The same immutable diagnostics also prove that the source's 171 static line
    objects are baked into far fewer live buffers without deleting vertices and
    that complete-frame draw calls stay below the former high-cost pipeline.
    Reading both contracts from the same dark 1280 x 720 scene removes a second
    identical WebGL compilation; no event or state mutation separates them.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)

    expect(canvas).to_have_attribute("data-performance-revision", "demand-batched-v5")
    expect(canvas).to_have_attribute("data-canvas-msaa", "0")
    expect(canvas).to_have_attribute("data-scene-msaa", "4")
    expect(canvas).to_have_attribute("data-postprocess-samples", "0")
    expect(canvas).to_have_attribute("data-postprocess-stages", "16")
    expect(canvas).to_have_attribute("data-bloom-sampling", "paired-bilinear")
    expect(canvas).to_have_attribute(
        "data-plasma-halo",
        "theme-aware-light-v3",
    )
    expect(canvas).to_have_attribute("data-transparent-pass-mode", "single")
    expect(canvas).to_have_attribute("data-shader-compilation", "complete")
    expect(canvas).to_have_attribute("data-gpu-timer", "disabled")
    assert canvas.get_attribute("data-gpu-frame-ms") is None

    diagnostics = canvas.evaluate(
        """
        (element) => ({
          sourceWireObjects: Number(element.dataset.sourceWireObjects),
          wireObjects: Number(element.dataset.wireObjects),
          renderCalls: Number(element.dataset.renderCalls),
          renderedLines: Number(element.dataset.renderLines),
        })
        """
    )

    assert diagnostics["sourceWireObjects"] == 171
    assert 0 < diagnostics["wireObjects"] <= 32
    assert diagnostics["wireObjects"] < diagnostics["sourceWireObjects"] / 4
    assert 0 < diagnostics["renderCalls"] < 180
    assert diagnostics["renderedLines"] > 30_000


def test_tokamak_matches_intended_visual_reference(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Protect all three approved reactor pixel contracts in one renderer.

    Performance changes in this renderer touch batching, transparent face
    submission, MSAA target routing, and bloom sampling. Each one can improve
    frame time while silently changing line density, occlusion, glow, or color.
    The checked-in references capture both themes at the stable front angle and
    the light renderer at its oblique scroll angle. Every scene sends a complete
    state, waits for a new demand-rendered frame, and keeps its own screenshot.
    The light front image additionally retains the aggregate channel contract
    that catches violet compositing even when source material constants look
    pink. The oblique crop retains the dedicated regression against a broad
    translucent bloom washing over the vessel.

    Sharing only the standalone WebGL compilation removes repeated setup; it
    does not share pixels or tolerances between scenes. Tiny interpolation and
    floating-point differences remain allowed, while missing wire groups,
    violet output, altered bloom, black light surfaces, reduced resolution, or
    stale camera framing still exceed the unchanged limits by orders of
    magnitude.
    """

    page = driver(
        stored_theme="dark",
        viewport=(480, 420),
        load_interactive_visuals=True,
    )
    page.goto(f"{base_url}/tokamak-reactor.html")
    canvas = page.locator("canvas")
    expect(canvas).to_be_visible()
    baselines = Path(__file__).with_name("visual_baselines")
    light_front: Image.Image | None = None

    for theme in ("dark", "light"):
        prior_frames = int(
            canvas.get_attribute("data-render-frames") or "0"
        )
        page.evaluate(
            """
            (selectedTheme) => window.postMessage({
              type: "tokamak-state",
              theme: selectedTheme,
              rotationX: -0.3,
              modelScale: 1,
              visible: true,
            }, "*")
            """,
            theme,
        )
        expect(canvas).to_have_attribute("data-reactor-theme", theme)
        page.wait_for_function(
            """
            (baseline) => Number(
              document.querySelector("canvas").dataset.renderFrames
            ) > baseline
            """,
            arg=prior_frames,
        )
        page.wait_for_timeout(100)

        actual = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
        expected = Image.open(
            baselines / f"tokamak-{theme}.png"
        ).convert("RGB")
        (
            maximum_channel_mean,
            high_delta_fraction,
            extreme_delta_fraction,
        ) = _image_difference_statistics(expected, actual)

        assert maximum_channel_mean < 0.6, theme
        assert high_delta_fraction < 0.012, theme
        assert extreme_delta_fraction < 0.003, theme

        if theme == "light":
            light_front = actual

    assert light_front is not None
    saturated_pixels = [
        pixel
        for pixel in light_front.getdata()
        if pixel[0] > 120 and max(pixel) - min(pixel) > 30
    ]
    assert len(saturated_pixels) > 20_000
    red, green, blue = (
        sum(pixel[channel] for pixel in saturated_pixels)
        / len(saturated_pixels)
        for channel in range(3)
    )
    assert blue - green < 55
    assert red - blue > 45

    prior_frames = int(canvas.get_attribute("data-render-frames") or "0")
    page.evaluate(
        """
        () => window.postMessage({
          type: "tokamak-state",
          theme: "light",
          rotationX: -0.52,
          modelScale: 1,
          visible: true,
        }, "*")
        """
    )
    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    expect(canvas).to_have_attribute(
        "data-plasma-halo",
        "theme-aware-light-v3",
    )
    page.wait_for_function(
        """
        (baseline) => Number(
          document.querySelector("canvas").dataset.renderFrames
        ) > baseline
        """,
        arg=prior_frames,
    )
    page.wait_for_timeout(100)

    oblique = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
    expected_oblique = Image.open(
        baselines / "tokamak-light-oblique.png"
    ).convert("RGB")
    plasma_crop = (70, 105, 410, 280)
    (
        maximum_channel_mean,
        high_delta_fraction,
        extreme_delta_fraction,
    ) = _image_difference_statistics(
        expected_oblique.crop(plasma_crop),
        oblique.crop(plasma_crop),
    )

    assert maximum_channel_mean < 1.0, "light-oblique"
    assert high_delta_fraction < 0.02, "light-oblique"
    assert extreme_delta_fraction < 0.005, "light-oblique"
