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
    clicks must each show the source-strength impulse on press and submit a
    zero-force recovery frame immediately on release without waiting for
    another animation frame. Once regular delivery resumes, the short recovery
    must finish as ordinary hover.
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
        assert released_state["phase"] in {"recovering", "hover"}
        assert released_state["pressed"] == "false"
        assert released_state["force"] in {0, 1}
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
    assert released_phase in {"recovering", "hover"}


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
    Release must then be processed normally. This is the workload and race the
    lightweight tests previously failed to represent.
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

    page.mouse.up(button="left")
    released_phase = canvas.get_attribute(
        "data-rendered-interaction-phase"
    )
    assert released_phase in {"recovering", "hover"}


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


@pytest.mark.parametrize(
    ("button", "direction", "held_multiplier"),
    [
        ("left", "1", "2.000"),
        ("right", "-1", "2.000"),
    ],
)
def test_expired_click_impulse_is_not_replayed_on_a_later_frame(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
    held_multiplier: str,
) -> None:
    """Keep the original click impulse limited to its 41.5 ms wall-clock window.

    The source ``muta-tentacles.html`` gives a press one immediate strong
    attraction/repulsion impulse and lets its envelope expire after
    ``0.28 / 6.75`` seconds (about 41.5 ms). The adapted rectangular surface
    used to split a delayed rendered frame into several physics updates. That
    could apply an expired high-force phase or the following held force multiple
    times before the next image appeared, making the first click response look
    much longer than in the source file.

    This regression test advances one animation frame by exactly 100 ms, then
    verifies two independent contracts for both left- and right-button presses:
    the configured envelope still equals the source's 41.5 ms, and no animated
    later rendered frame re-enters the impulse phase after that real-time window.
    The immediate input frame is intentionally excluded because it is the one
    strong impulse the original interaction is supposed to submit.
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
    page.evaluate(
        """
        () => {
          const canvas = document.querySelector("#muta");
          canvas.dataset.animationImpulseFramesSincePress = "0";
          window.__mutaTentaclesDebugFrameAfter(100);
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
    is strengthened from 1× to 2×. Releasing either button must submit a
    zero-force recovery frame before returning to ordinary hover attraction,
    without changing the shared timing or radius.

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
    assert released_state["phase"] in {"recovering", "hover"}
    assert released_state["pressed"] == "false"
    assert released_state["force"] in {0, 1}
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


def _assert_release_redraw(
    page: Page,
    canvas: Page,
    *,
    release: str,
) -> None:
    """Assert a selected release path clears force and the rendered star."""

    baseline, peak, pressed_frame = _press_surface(page, canvas)
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
    assert released["phase"] in {"recovering", "hover"}
    assert released["frame"] > pressed_frame
    assert released["force"] in {0, 1}
    assert released["displacement"] <= baseline * 1.25
    assert released["displacement"] <= peak * 0.5

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
def test_release_redraws_the_actual_tentacle_geometry_at_every_width(
    driver: DriverFactory,
    base_url: str,
    viewport: tuple[int, int],
) -> None:
    """Prevent the rendered attraction star from surviving a completed click.

    Earlier tests asserted only the logical ``phase`` and force attributes.
    The video demonstrates why that was insufficient: the phase could already
    read ``hover`` while Verlet/IK points remained pulled into a bright star for
    seconds. The intermittent narrow-window variant was especially easy to
    miss because a resize can delay normal animation frames after the strong
    immediate input render.

    At phone, tablet, and desktop widths this test pauses the normal animation
    loop, uses a fixed tentacle generation seed, records the actual maximum
    rendered tip displacement, clicks, and releases. Release itself must submit
    a newer WebGL frame, clear the pressed force, and bring geometry back close
    to its pre-click displacement and below half of the impulse peak. A manual
    200 ms wall-clock step must leave an ordinary hover frame. Thus the test
    fails on the visible defect even if state strings alone look correct.
    """

    page = driver(stored_theme="dark", viewport=viewport)
    canvas = _open_diagnostic_surface(page, base_url)
    _assert_release_redraw(page, canvas, release="pointerup")


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
    omits ``pointerup``, and demands the same force clearing and geometric
    recovery as a normal release. It exercises the exact two lifecycle paths
    that make the user's intermittent report reproducible.
    """

    page = driver(stored_theme="dark", viewport=(755, 620))
    canvas = _open_diagnostic_surface(page, base_url)
    _assert_release_redraw(page, canvas, release=release)


TIMING_MUTATIONS = [
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
        "            runImmediateInteractionFrame();",
        """            runImmediateInteractionFrame();
            setTimeout(
              () => runImmediateInteractionFrame(true),
              PRESS_IMPULSE_TOTAL_SECONDS * 1000,
            );""",
        "deadline-render",
        id="queue-blocking-deadline-render",
    ),
    pytest.param(
        "const RELEASE_RECOVERY_SECONDS = 0.18;",
        "const RELEASE_RECOVERY_SECONDS = 5;",
        "release",
        id="never-finish-recovery",
    ),
    pytest.param(
        "const RELEASE_RECOVERY_RATE = 34;",
        "const RELEASE_RECOVERY_RATE = 1;",
        "release",
        id="leave-geometry-in-star",
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

    1. extend the total impulse window;
    2. slow exponential decay;
    3. extend the full-strength hold;
    4. raise peak force;
    5. widen the strong radius;
    6. strengthen the post-impulse held attraction;
    7. queue a blocking WebGL render at the impulse deadline;
    8. make release recovery effectively never finish;
    9. slow geometric recovery so the star remains visible;
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
        if contract == "envelope":
            _assert_exact_impulse_envelope(page)
        elif contract == "deadline-render":
            _assert_no_deadline_render(page, canvas)
        elif contract == "release":
            _assert_release_redraw(
                page,
                canvas,
                release="pointerup",
            )
        elif contract == "implicit-buttons-zero":
            _assert_release_redraw(
                page,
                canvas,
                release=contract,
            )
        elif contract == "window-blur":
            _assert_release_redraw(
                page,
                canvas,
                release=contract,
            )
        else:
            raise AssertionError(f"Unknown mutation contract: {contract}")
