"""Browser regression coverage for the interactive tentacle physics clock."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC_QUERY = (
    "?max-tentacles=64&render-supersample=0.5"
    "&manual-animation-frame=1&interaction-diagnostics=1"
    "&generation-seed=12345"
)


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

    page = driver(stored_theme="dark", viewport=(1280, 720))
    page.goto(
        f"{base_url}/muta-tentacles.html?generation-seed=12345",
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
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

    page = driver(stored_theme="dark", viewport=(240, 180))
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-spatial-candidates=1&generation-seed=12345"
            "&max-tentacles=64&render-supersample=0.5"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
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
        "data-fps",
        re.compile(r"^[1-9][0-9]*$"),
    )


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
    aspect-aware reload to finish. It deliberately pauses only animation-frame
    delivery—the browser condition that makes the race deterministic—while
    preserving real pointer events and wall-clock timers. Three actual left
    clicks must each show the source-strength impulse on press and submit an
    ordinary hover frame immediately on release without waiting for another
    animation frame. The release frame must neither retain peak force nor
    introduce a synthetic zero-force recovery kick.
    Repeating the click cycle is intentional: it guards the formerly
    intermittent second-or-later click as well as the first click after a
    resize.

    The test reduces only tentacle count and supersampling so all three viewport
    cases finish quickly in headless Chromium; it uses the unchanged production
    event handlers, force envelope, resize reload, and render clock.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 650),
        controllable_animation_frames=True,
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
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
    page.wait_for_function(
        "origin => performance.timeOrigin !== origin",
        arg=initial_time_origin,
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-fps",
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
        canvas.evaluate(
            """
            element => {
              element.ownerDocument.defaultView
                .__mutaTestSetAnimationFramesPaused(true);
            }
            """
        )
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
        canvas.evaluate(
            """
            element => {
              element.ownerDocument.defaultView
                .__mutaTestSetAnimationFramesPaused(false);
            }
            """
        )
        expect(canvas).to_have_attribute(
            "data-pointer-interaction-phase",
            "hover",
            timeout=1_000,
        )


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
    density and rendering resolution. Normal animation is manually clocked so
    any unexpected frame can only come from an input/deadline callback. After
    one real press, the initially submitted frame id and measured input latency
    must remain unchanged for 120 ms—almost three complete impulse windows.
    It then advances the first non-redundant physics frame by nine milliseconds and
    verifies that the peak was not applied a second time. Release must then be
    processed normally. This full-density assertion is the part the earlier
    lightweight tests lacked: it catches the long visible deformation even
    when the nominal force timer and low-density state tests look correct.
    """

    page = driver(stored_theme="dark", viewport=viewport)
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?manual-animation-frame=1&generation-seed=12345"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 3; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(16);
          }
        }
        """
    )
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

    This test supplies 100 ms animation-frame timestamps and verifies that the
    embedded production surface performs exactly one 33 ms physics update,
    fixing both the number of force applications and their simulated duration.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        animation_frame_interval_ms=100,
    )
    page.evaluate(
        """
        () => scrollTo({
          top: document.querySelector("#contact").offsetTop,
          behavior: "instant",
        })
        """
    )
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")

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
        element => element.ownerDocument.defaultView.postMessage({
          type: "muta-tentacles-runtime",
          visible: true,
          scrollActive: false,
        }, "*")
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
        element => element.ownerDocument.defaultView.postMessage({
          type: "muta-tentacles-runtime",
          visible: true,
          scrollActive: true,
        }, "*")
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
        element => element.ownerDocument.defaultView.postMessage({
          type: "muta-tentacles-runtime",
          visible: true,
          scrollActive: false,
        }, "*")
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

    This integration regression remains at ``scrollY == 0`` and requires the
    real iframe canvas to initialize while the contact section is still far
    below the viewport. Its runtime must then be paused by the existing
    visibility protocol, so prewarming consumes no ongoing animation work.
    Restoring ``loading="lazy"`` makes the canvas absent and fails; forgetting
    the visibility handoff leaves the offscreen simulation running and also
    fails. The test therefore moves only startup cost away from scrolling
    without trading it for permanent background rendering.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
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


def test_contact_scroll_events_toggle_compositor_friendly_runtime_mode(
    driver: DriverFactory,
) -> None:
    """Connect real page scrolling to the iframe's lightweight render mode.

    The renderer-side optimization is useful only if the host page brackets an
    actual scroll burst correctly. This integration regression positions the
    contact section in view, dispatches the same top-level ``scroll`` event that
    Chromium emits during movement, and requires the iframe to enter
    suppression immediately.
    It then emits a wheel event directly over the iframe—the path used when the
    pointer sits on the tentacles—and requires the same transition before the
    parent document has a chance to deliver its own scroll event. Both paths
    must return to ordinary rendering when Chromium emits ``scrollend`` (with
    a conservative 320 ms quiet timer retained as a fallback), without a
    click, resize, theme toggle, or reload.

    Reverting only the parent listener leaves the iframe permanently ``active``
    and fails the first assertion. Forgetting or repeatedly postponing the
    scroll-end handoff leaves it ``suppressed`` and fails the final assertion.
    Resuming after the former 120 ms timeout fails the intermediate assertion:
    a slightly delayed trackpad event would otherwise allow one full expensive
    frame between two parts of the same momentum-scroll burst. The test
    therefore protects the exact user-visible path that previously made the
    bottom of the page scroll in steps.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1280, 720),
        load_interactive_visuals=True,
    )
    page.evaluate(
        """
        () => {
          document.documentElement.style.scrollBehavior = "auto";
          window.scrollTo({
            top: document.querySelector("#contact").offsetTop - 90,
            behavior: "auto",
          });
        }
        """
    )
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    expect(canvas).to_have_attribute(
        "data-runtime-visible",
        "true",
        timeout=30_000,
    )
    expect(canvas).to_have_attribute(
        "data-scroll-rendering",
        "active",
        timeout=30_000,
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
    page.wait_for_timeout(180)
    expect(canvas).to_have_attribute(
        "data-scroll-rendering",
        "suppressed",
    )
    page.evaluate(
        "() => window.dispatchEvent(new Event('scrollend'))"
    )
    expect(canvas).to_have_attribute(
        "data-scroll-rendering",
        "active",
        timeout=1_000,
    )

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
    expect(canvas).to_have_attribute("data-scroll-rendering", "suppressed")
    assert int(
        canvas.get_attribute("data-scroll-suppression-count") or "0"
    ) > wheel_baseline
    page.wait_for_timeout(50)
    page.evaluate(
        "() => window.dispatchEvent(new Event('scrollend'))"
    )
    expect(canvas).to_have_attribute(
        "data-scroll-rendering",
        "active",
        timeout=1_000,
    )


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
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
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
) -> Page:
    """Open a deterministic, manually clocked copy of the WebGL surface."""

    page.goto(
        f"{base_url}/{path}{DIAGNOSTIC_QUERY}",
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")
    expect(canvas).to_be_visible(timeout=30_000)
    page.evaluate(
        """
        () => {
          for (let frame = 0; frame < 8; frame += 1) {
            window.__mutaTentaclesDebugFrameAfter(16);
          }
        }
        """
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

    page = driver(stored_theme="dark", viewport=(390, 610))
    _open_diagnostic_surface(page, base_url)
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

    page = driver(stored_theme="dark", viewport=viewport)
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

    page = driver(stored_theme="dark", viewport=(755, 620))
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
        PROJECT_ROOT / "public" / "muta-tentacles.html"
    ).read_text(encoding="utf-8")
    legacy_source = _restore_legacy_release_recovery(source)

    page = driver(stored_theme="dark", viewport=(755, 620))
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
        PROJECT_ROOT / "public" / "muta-tentacles.html"
    ).read_text(encoding="utf-8")
    assert source.count(original) == 1
    mutated = source.replace(original, replacement, 1)
    assert mutated != source

    page = driver(stored_theme="dark", viewport=(755, 620))
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
