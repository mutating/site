"""Browser tests for theme-aware rendering of the tokamak wireframe."""

from __future__ import annotations

from collections.abc import Callable
import io
from pathlib import Path
import re

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import Locator, Page, expect


DriverFactory = Callable[..., Page]


def _reactor_canvas(page: Page) -> Locator:
    """Return the rendered canvas inside the self-contained tokamak frame."""

    canvas = page.frame_locator(".tokamak-model__frame").locator("canvas")
    expect(canvas).to_be_visible()
    return canvas


def _show_reactor(page: Page) -> Locator:
    """Scroll the reactor into its active rendering range and return its canvas."""

    page.locator(".tokamak-model").scroll_into_view_if_needed()
    canvas = _reactor_canvas(page)
    expect(canvas).to_have_attribute("data-render-visible", "true")
    expect(canvas).to_have_attribute("data-render-frames", re.compile(r"[1-9]\d*"))
    return canvas


def test_light_tokamak_uses_invisible_depth_occluders(
    driver: DriverFactory,
) -> None:
    """Keep rear and internal wireframe edges hidden on the light background.

    The light theme previously hid every solid reactor mesh while leaving all
    line objects visible. Removing those surfaces also removed their depth
    information, so edges on the back and inside of the reactor showed through
    the front and formed dense, illegible clumps that did not exist in the dark
    theme. The light renderer must therefore keep a nonzero set of solid meshes
    as colorless depth occluders: they may write only depth, never color, while
    the ordinary page background and the pink wireframe remain visible.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _reactor_canvas(page)

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


def test_tokamak_bloom_radius_does_not_reveal_frame_edges(
    driver: DriverFactory,
) -> None:
    """Keep the plasma glow tight enough that the iframe boundary stays hidden.

    The former shared bloom profile spread a faint pink wash across the
    light-theme reactor in some sizes and camera angles. Besides reaching the
    rectangular canvas edge, that wash looked like translucent shading over the
    vessel instead of light emitted by the plasma. The light renderer therefore
    uses a compact 0.04 radius and reduced 0.28 strength, while the dark theme
    retains its established 0.18/1.15 profile. This test fixes the light values
    exposed by the live renderer so the broad overlay cannot silently return.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _reactor_canvas(page)

    expect(canvas).to_have_attribute("data-bloom-radius", "0.04")
    expect(canvas).to_have_attribute("data-bloom-strength", "0.28")
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
    canvas = _reactor_canvas(page)
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
            expect(canvas).to_have_attribute("data-bloom-radius", "0.04")
            expect(canvas).to_have_attribute("data-bloom-strength", "0.28")
        else:
            assert depth_occluders == 0
            expect(canvas).to_have_attribute("data-bloom-radius", "0.18")
            expect(canvas).to_have_attribute("data-bloom-strength", "1.15")


def test_tokamak_stops_rendering_when_the_scene_is_idle(
    driver: DriverFactory,
) -> None:
    """Render one settled frame instead of running an endless animation loop.

    The reactor has no autonomous motion: its angle changes only when scrolling,
    resizing, or switching theme. The former implementation nevertheless called
    requestAnimationFrame forever and reran the full scene, bloom, SMAA, and
    output pipeline while every visible pixel remained unchanged. This test
    scrolls the model into view, waits for the state-driven frame to settle, and
    proves that its frame counter remains exactly constant during an idle
    interval. It then scrolls the page slightly and requires one new frame,
    guarding both halves of the contract: no waste while static and no frozen
    visual when the scroll-linked angle really changes.
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
    input tracking without requiring redundant GPU work.
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
              window.scrollTo(0, target);
              return {before, target};
            }
            """
        )
        page.wait_for_timeout(50)

        assert page.evaluate("window.scrollY") != scroll_positions["before"]
        assert model.get_attribute("data-rotation-x") != initial_rotation
    finally:
        page.evaluate("window.__mutaTestSetAnimationFramesPaused(false)")


def test_tokamak_uses_msaa_only_for_geometry_and_single_sample_postprocessing(
    driver: DriverFactory,
) -> None:
    """Keep geometric edge quality while removing redundant multisampling.

    Four-sample MSAA is still required for the wireframe scene, but applying it
    again to fullscreen bloom, SMAA, and final-output triangles cannot improve
    their geometry and multiplies render-target bandwidth. The browser canvas
    likewise receives an already postprocessed fullscreen image. This test
    fixes the intended pipeline topology: 4x MSAA for the scene, no default
    canvas MSAA, single-sample postprocessing, and the fused sixteen-pass
    fullscreen chain. Reintroducing the former multisampled composer buffers or
    accidentally dropping scene MSAA will therefore fail explicitly.
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
        "theme-aware-light-v2",
    )
    expect(canvas).to_have_attribute("data-transparent-pass-mode", "single")
    expect(canvas).to_have_attribute("data-shader-compilation", "complete")
    expect(canvas).to_have_attribute("data-gpu-timer", "disabled")
    assert canvas.get_attribute("data-gpu-frame-ms") is None


def test_tokamak_does_not_poll_gpu_timer_queries_during_normal_scrolling(
    driver: DriverFactory,
) -> None:
    """Keep benchmark-only GPU synchronization out of production scrolling.

    The optimization benchmark originally left
    ``EXT_disjoint_timer_query_webgl2`` enabled in the shipped reactor. Every
    state-driven render created a GPU query and polled its completion every
    16 milliseconds. On the affected machine those reads competed with
    compositor work and sometimes forced driver synchronization precisely while
    the user was scrolling through the reactor, turning an otherwise cheap
    demand-rendered update into a visibly jerky section.

    The production canvas must explicitly report that this diagnostic is
    disabled and must never publish a GPU-frame result, even after several
    scroll-linked renders. Restoring the old query creation/polling loop changes
    ``data-gpu-timer`` to ``supported`` or ``unavailable`` and, on supported
    hardware, creates ``data-gpu-frame-ms``; either regression fails here.
    CPU-side frame diagnostics remain available and are verified separately.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)

    for offset in (80, -35, 110, -60):
        page.mouse.wheel(0, offset)
        page.wait_for_timeout(50)

    expect(canvas).to_have_attribute("data-gpu-timer", "disabled")
    assert canvas.get_attribute("data-gpu-frame-ms") is None
    expect(canvas).to_have_attribute(
        "data-last-frame-ms",
        re.compile(r"^\d+\.\d{3}$"),
    )


def test_tokamak_batches_static_wireframes_and_reports_whole_frame_work(
    driver: DriverFactory,
) -> None:
    """Collapse identical static line submissions without deleting geometry.

    The source model contains 171 LineSegments objects, many of which use the
    same material and render order. Sending every object separately was a large
    CPU and driver overhead even though their vertices never move relative to
    each other. The optimized renderer bakes those transforms into combined
    buffers while preserving the original source count for diagnostics. This
    test requires a substantial reduction in live line objects, retains a
    nonzero line-vertex count, and caps complete-frame draw calls high enough to
    allow platform variation but low enough to detect a return to the former
    roughly 400-call dark-theme pipeline, where double-sided transparent meshes
    required separate back- and front-face submissions.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)
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


def test_tokamak_pauses_when_it_leaves_the_viewport(
    driver: DriverFactory,
) -> None:
    """Stop all reactor GPU work after the containing section leaves view.

    Demand rendering alone prevents idle redraws, but an offscreen resize or
    theme event could still schedule expensive frames for content the user
    cannot see. The parent page now tracks the reactor with IntersectionObserver
    and sends an explicit visibility state to the iframe. This regression first
    activates the model, then moves it well outside the pre-render margin and
    proves both that the renderer enters its paused state and that its frame
    counter cannot advance while offscreen.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    canvas = _show_reactor(page)

    page.locator("#top").evaluate("(element) => element.scrollIntoView()")
    expect(canvas).to_have_attribute("data-render-visible", "false")
    expect(canvas).to_have_attribute("data-render-paused", "true")
    paused_frames = int(canvas.get_attribute("data-render-frames") or "0")
    page.wait_for_timeout(400)
    assert int(canvas.get_attribute("data-render-frames") or "0") == paused_frames


def test_tokamak_matches_intended_visual_reference(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Protect the reactor image while allowing only subpixel GPU rounding.

    Performance changes in this renderer touch batching, transparent face
    submission, MSAA target routing, and bloom sampling. Each one can improve
    frame time while silently changing line density, occlusion, glow, or color.
    The checked-in references capture the approved renderer at the same
    viewport, device scale, model angle, and both themes. This test renders the
    standalone scene and compares every RGB pixel. It permits tiny interpolation
    and floating-point differences that are invisible at normal scale, but
    rejects broad or high-amplitude changes; a missing wire group, altered
    bloom, black light-theme surface, reduced resolution, or different camera
    framing exceeds these limits by orders of magnitude.
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

    for theme in ("dark", "light"):
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
        page.wait_for_timeout(100)

        actual = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
        expected = Image.open(
            baselines / f"tokamak-{theme}.png"
        ).convert("RGB")
        difference = ImageChops.difference(expected, actual)
        channel_means = ImageStat.Stat(difference).mean
        pixels = list(difference.getdata())
        total_pixels = len(pixels)
        high_delta_fraction = (
            sum(max(pixel) > 8 for pixel in pixels) / total_pixels
        )
        extreme_delta_fraction = (
            sum(max(pixel) > 24 for pixel in pixels) / total_pixels
        )

        assert max(channel_means) < 0.6
        assert high_delta_fraction < 0.012
        assert extreme_delta_fraction < 0.003


def test_light_plasma_glow_stays_local_at_an_oblique_scroll_angle(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep light-theme plasma luminous without shading the reactor body.

    The reactor rotates around its horizontal axis as its section passes through
    the viewport. At the approximately -0.52-radian side-on transition, the
    earlier light-theme treatment sent an overbright white core through the
    fullscreen bloom chain. Its wide pink-white wash covered the vessel around
    the plasma and looked like translucent shading rather than emitted light.
    The corrected light profile feeds a dedicated compact bloom setting and
    builds the glow from a narrow white center, saturated pink core, and tightly
    feathered rim. Its radius and strength are intentionally much lower than the
    dark-theme bloom. The broad flat energy disk is dark-theme-only, and the
    dark renderer deliberately keeps its established material unchanged.

    This test reproduces that exact light-theme angle in the standalone renderer
    and compares the plasma plus the surrounding vessel with a checked-in
    reference. The crop is intentionally taller than the luminous ring: this
    makes the test fail if a future shader or bloom change again paints a broad
    translucent band over the wireframe, even when the ring itself still looks
    bright. Reverting to the rejected wide bloom, the old constant-opacity
    toruses, or the contour-like single halo changes this crop far beyond the
    small cross-GPU tolerance below and must fail the test.
    """

    page = driver(
        stored_theme="light",
        viewport=(480, 420),
        load_interactive_visuals=True,
    )
    page.goto(f"{base_url}/tokamak-reactor.html")
    canvas = page.locator("canvas")
    expect(canvas).to_be_visible()
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
        "theme-aware-light-v2",
    )
    page.wait_for_timeout(100)

    actual = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
    expected = Image.open(
        Path(__file__).with_name("visual_baselines")
        / "tokamak-light-oblique.png"
    ).convert("RGB")
    plasma_crop = (70, 105, 410, 280)
    difference = ImageChops.difference(
        expected.crop(plasma_crop),
        actual.crop(plasma_crop),
    )
    pixels = list(difference.getdata())
    total_pixels = len(pixels)
    high_delta_fraction = (
        sum(max(pixel) > 8 for pixel in pixels) / total_pixels
    )
    extreme_delta_fraction = (
        sum(max(pixel) > 24 for pixel in pixels) / total_pixels
    )

    assert max(ImageStat.Stat(difference).mean) < 1.0
    assert high_delta_fraction < 0.02
    assert extreme_delta_fraction < 0.005
