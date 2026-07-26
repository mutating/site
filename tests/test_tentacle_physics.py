"""Browser regression coverage for the interactive tentacle physics clock."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]


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
    clicks must each show the source-strength impulse on press and return to
    hover immediately on release without waiting for another animation frame.
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
        expect(canvas).to_have_attribute(
            "data-pointer-interaction-phase",
            "hover",
            timeout=150,
        )
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


def test_held_press_settles_at_the_source_deadline_without_a_new_frame(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """End the strong phase on its 41.5 ms wall-clock deadline.

    A click and a held press share the same initial source impulse. After
    ``0.28 / 6.75`` seconds, a still-held left button must switch from the
    13.333× impulse to ordinary 2× held attraction. During a resize Chromium can
    postpone the next animation frame, so relying on that frame makes the last
    strong image linger and creates the same apparently overlong pulse as a
    released click.

    The test pauses animation frames after the lightweight production surface
    has loaded, presses and keeps the real left mouse button down, and verifies
    that the wall-clock deadline itself submits the held state. It also checks
    the measured transition time with generous headless-rendering tolerance,
    proving that the fallback is tied to the original 41.5 ms envelope rather
    than to an arbitrary long timeout.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
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
    canvas.evaluate(
        """
        element => {
          element.ownerDocument.defaultView
            .__mutaTestSetAnimationFramesPaused(true);
        }
        """
    )
    page.wait_for_timeout(35)
    page.mouse.move(120, 90)
    page.mouse.down(button="left")

    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "impulse",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=250,
    )
    settled_after_milliseconds = float(
        canvas.get_attribute("data-last-impulse-settled-after-ms")
        or "0"
    )
    assert 41 <= settled_after_milliseconds <= 200
    page.mouse.up(button="left")
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "hover",
        timeout=150,
    )


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
    is strengthened from 1× to 2×. Releasing either button must return directly
    to ordinary hover attraction, without changing the shared timing or radius.

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

    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "hover",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        "1.000",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        "0",
    )
